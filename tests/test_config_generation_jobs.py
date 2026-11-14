from pathlib import Path
import sqlite3
import time

from scripts.config_generation_jobs import (
    ConfigurationJobManager,
    ConfigurationJobStore,
)
from scripts.config_generation_service import (
    ConfigurationGenerationOutcome,
    ConfigurationGenerationRequest,
)


class StubGenerationService:
    def generate(
        self,
        request: ConfigurationGenerationRequest,
    ) -> ConfigurationGenerationOutcome:
        return ConfigurationGenerationOutcome(
            result={"requirement": request.requirement},
            evidence=(),
        )


def test_store_migrates_existing_job_database(tmp_path: Path) -> None:
    database_path = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE configuration_jobs (
                job_id TEXT PRIMARY KEY,
                idempotency_key TEXT UNIQUE,
                request_sha256 TEXT NOT NULL,
                request_json TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            )
            """
        )

    ConfigurationJobStore(database_path).initialize()

    with sqlite3.connect(database_path) as connection:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(configuration_jobs)"
            )
        }
    assert {"request_id", "error_code", "duration_ms"} <= columns


def wait_for_status(
    manager: ConfigurationJobManager,
    job_id: str,
    status: str,
) -> None:
    for _ in range(100):
        job = manager.get(job_id)
        if job is not None and job.status == status:
            return
        time.sleep(0.01)
    raise AssertionError(f"Job did not reach {status}: {job_id}")


def test_manager_processes_queued_job_recovered_at_startup(
    tmp_path: Path,
) -> None:
    store = ConfigurationJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    job, _ = store.create_or_get(
        ConfigurationGenerationRequest(
            requirement="NTPを設定",
            product="catalyst9300",
        ),
        idempotency_key=None,
        request_id="request-recovered",
        max_unfinished_jobs=20,
    )
    manager = ConfigurationJobManager(store, StubGenerationService())

    manager.start()
    try:
        wait_for_status(manager, job.job_id, "completed")
        recovered = manager.get(job.job_id)
    finally:
        manager.shutdown()

    assert recovered is not None
    assert recovered.result == {"requirement": "NTPを設定"}
    assert recovered.request_id == "request-recovered"
    assert recovered.duration_ms is not None


def test_manager_marks_interrupted_running_job_failed(
    tmp_path: Path,
) -> None:
    store = ConfigurationJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    job, _ = store.create_or_get(
        ConfigurationGenerationRequest(
            requirement="NTPを設定",
            product="catalyst9300",
        ),
        idempotency_key=None,
        request_id="request-interrupted",
        max_unfinished_jobs=20,
    )
    assert store.mark_running(job.job_id)
    manager = ConfigurationJobManager(store, StubGenerationService())

    manager.start()
    try:
        recovered = manager.get(job.job_id)
    finally:
        manager.shutdown()

    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.error_code == "interrupted"
    assert recovered.error == "Job was interrupted by process restart"
