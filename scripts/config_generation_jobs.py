"""Persistent background jobs for configuration generation."""

from __future__ import annotations

from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
)
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Literal, Protocol
import uuid

from .config_generation_service import (
    ConfigurationGenerationError,
    ConfigurationGenerationOutcome,
    ConfigurationGenerationRequest,
)


LOGGER = logging.getLogger(__name__)
JobStatus = Literal["queued", "running", "completed", "failed"]


class ConfigurationJobError(RuntimeError):
    """Raised when a persistent configuration job cannot be processed."""


class IdempotencyConflictError(ConfigurationJobError):
    """Raised when an idempotency key is reused for a different request."""


class JobCapacityError(ConfigurationJobError):
    """Raised when the unfinished job limit has been reached."""


class ConfigurationGenerationRunner(Protocol):
    def generate(
        self,
        request: ConfigurationGenerationRequest,
    ) -> ConfigurationGenerationOutcome: ...


@dataclass(frozen=True)
class ConfigurationJob:
    job_id: str
    request_id: str
    status: JobStatus
    request: ConfigurationGenerationRequest
    result: dict[str, Any] | None
    error_code: str | None
    error: str | None
    duration_ms: int | None
    created_at: str
    updated_at: str
    started_at: str | None
    completed_at: str | None


class ConfigurationJobStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.executescript(
                    """
                    PRAGMA journal_mode = WAL;
                    CREATE TABLE IF NOT EXISTS configuration_jobs (
                        job_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL,
                        idempotency_key TEXT UNIQUE,
                        request_sha256 TEXT NOT NULL,
                        request_json TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (
                            status IN ('queued', 'running', 'completed', 'failed')
                        ),
                        result_json TEXT,
                        error_code TEXT,
                        error TEXT,
                        duration_ms INTEGER CHECK (
                            duration_ms IS NULL OR duration_ms >= 0
                        ),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS configuration_jobs_status_index
                    ON configuration_jobs (status, created_at);
                    """
                )
                columns = {
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(configuration_jobs)"
                    )
                }
                if "request_id" not in columns:
                    connection.execute(
                        "ALTER TABLE configuration_jobs ADD COLUMN request_id TEXT"
                    )
                    connection.execute(
                        """
                        UPDATE configuration_jobs
                        SET request_id = job_id
                        WHERE request_id IS NULL
                        """
                    )
                if "error_code" not in columns:
                    connection.execute(
                        "ALTER TABLE configuration_jobs ADD COLUMN error_code TEXT"
                    )
                if "duration_ms" not in columns:
                    connection.execute(
                        "ALTER TABLE configuration_jobs ADD COLUMN duration_ms INTEGER"
                    )
        except sqlite3.Error as error:
            raise ConfigurationJobError(
                f"Could not initialize job database: {self._database_path}"
            ) from error

    def create_or_get(
        self,
        request: ConfigurationGenerationRequest,
        idempotency_key: str | None,
        request_id: str,
        max_unfinished_jobs: int,
    ) -> tuple[ConfigurationJob, bool]:
        request_json = _canonical_json(asdict(request))
        request_sha256 = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if idempotency_key is not None:
                    existing = connection.execute(
                        """
                        SELECT * FROM configuration_jobs
                        WHERE idempotency_key = ?
                        """,
                        (idempotency_key,),
                    ).fetchone()
                    if existing is not None:
                        if existing["request_sha256"] != request_sha256:
                            raise IdempotencyConflictError(
                                "Idempotency key is already used for a different request"
                            )
                        return _job_from_row(existing), False
                unfinished_count = connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM configuration_jobs
                    WHERE status IN ('queued', 'running')
                    """
                ).fetchone()
                if (
                    unfinished_count is not None
                    and int(unfinished_count["count"]) >= max_unfinished_jobs
                ):
                    raise JobCapacityError(
                        "Configuration job capacity has been reached"
                    )
                now = _utc_now()
                job_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO configuration_jobs (
                        job_id, request_id, idempotency_key, request_sha256,
                        request_json, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (
                        job_id,
                        request_id,
                        idempotency_key,
                        request_sha256,
                        request_json,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM configuration_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise ConfigurationJobError(
                        f"Created job could not be loaded: {job_id}"
                    )
                return _job_from_row(row), True
        except (IdempotencyConflictError, JobCapacityError):
            raise
        except sqlite3.Error as error:
            raise ConfigurationJobError("Could not create configuration job") from error

    def get(self, job_id: str) -> ConfigurationJob | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM configuration_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise ConfigurationJobError(
                f"Could not load configuration job: {job_id}"
            ) from error
        return _job_from_row(row) if row is not None else None

    def queued_job_ids(self) -> list[str]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT job_id FROM configuration_jobs
                    WHERE status = 'queued'
                    ORDER BY created_at, job_id
                    """
                ).fetchall()
        except sqlite3.Error as error:
            raise ConfigurationJobError("Could not load queued jobs") from error
        return [str(row["job_id"]) for row in rows]

    def fail_interrupted_jobs(self) -> None:
        now = _utc_now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE configuration_jobs
                    SET status = 'failed',
                        error_code = 'interrupted',
                        error = 'Job was interrupted by process restart',
                        completed_at = ?,
                        updated_at = ?
                    WHERE status = 'running'
                    """,
                    (now, now),
                )
        except sqlite3.Error as error:
            raise ConfigurationJobError(
                "Could not recover interrupted configuration jobs"
            ) from error

    def mark_running(self, job_id: str) -> bool:
        now = _utc_now()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE configuration_jobs
                    SET status = 'running', started_at = ?, updated_at = ?
                    WHERE job_id = ? AND status = 'queued'
                    """,
                    (now, now, job_id),
                )
        except sqlite3.Error as error:
            raise ConfigurationJobError(
                f"Could not start configuration job: {job_id}"
            ) from error
        return cursor.rowcount == 1

    def mark_completed(
        self,
        job_id: str,
        result: dict[str, Any],
        duration_ms: int,
    ) -> None:
        now = _utc_now()
        try:
            result_json = _canonical_json(result)
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE configuration_jobs
                    SET status = 'completed', result_json = ?,
                        error_code = NULL, error = NULL, duration_ms = ?,
                        completed_at = ?, updated_at = ?
                    WHERE job_id = ? AND status = 'running'
                    """,
                    (result_json, duration_ms, now, now, job_id),
                )
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise ConfigurationJobError(
                f"Could not complete configuration job: {job_id}"
            ) from error
        if cursor.rowcount != 1:
            raise ConfigurationJobError(
                f"Configuration job is not running: {job_id}"
            )

    def mark_failed(
        self,
        job_id: str,
        error_code: str,
        error_message: str,
        duration_ms: int | None,
    ) -> None:
        now = _utc_now()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE configuration_jobs
                    SET status = 'failed', error_code = ?, error = ?,
                        duration_ms = ?, completed_at = ?, updated_at = ?
                    WHERE job_id = ? AND status IN ('queued', 'running')
                    """,
                    (
                        error_code,
                        error_message,
                        duration_ms,
                        now,
                        now,
                        job_id,
                    ),
                )
        except sqlite3.Error as error:
            raise ConfigurationJobError(
                f"Could not fail configuration job: {job_id}"
            ) from error
        if cursor.rowcount != 1:
            raise ConfigurationJobError(
                f"Configuration job cannot be failed: {job_id}"
            )

    def _connect(self) -> sqlite3.Connection:
        try:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self._database_path, timeout=5)
        except (OSError, sqlite3.Error) as error:
            raise ConfigurationJobError(
                f"Could not open job database: {self._database_path}"
            ) from error
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


class ConfigurationJobManager:
    def __init__(
        self,
        store: ConfigurationJobStore,
        service: ConfigurationGenerationRunner,
        *,
        max_unfinished_jobs: int = 20,
        execution_timeout_seconds: float = 300,
    ) -> None:
        if max_unfinished_jobs < 1:
            raise ValueError("max_unfinished_jobs must be at least 1")
        if execution_timeout_seconds <= 0:
            raise ValueError("execution_timeout_seconds must be greater than 0")
        self._store = store
        self._service = service
        self._max_unfinished_jobs = max_unfinished_jobs
        self._execution_timeout_seconds = execution_timeout_seconds
        self._executor: ThreadPoolExecutor | None = None

    def start(self) -> None:
        if self._executor is not None:
            raise ConfigurationJobError("Configuration job manager is already started")
        self._store.initialize()
        self._store.fail_interrupted_jobs()
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="configuration-job",
        )
        for job_id in self._store.queued_job_ids():
            self._executor.submit(self._run_job, job_id)

    def shutdown(self) -> None:
        if self._executor is None:
            return
        self._executor.shutdown(wait=True)
        self._executor = None

    def submit(
        self,
        request: ConfigurationGenerationRequest,
        idempotency_key: str | None,
        request_id: str,
    ) -> tuple[ConfigurationJob, bool]:
        if self._executor is None:
            raise ConfigurationJobError("Configuration job manager is not started")
        job, created = self._store.create_or_get(
            request,
            idempotency_key,
            request_id,
            self._max_unfinished_jobs,
        )
        if created:
            self._executor.submit(self._run_job, job.job_id)
            _log_job_event(job, "queued")
        return job, created

    def get(self, job_id: str) -> ConfigurationJob | None:
        return self._store.get(job_id)

    def _run_job(self, job_id: str) -> None:
        started = time.monotonic()
        job: ConfigurationJob | None = None
        try:
            if not self._store.mark_running(job_id):
                return
            job = self._store.get(job_id)
            if job is None:
                raise ConfigurationJobError(
                    f"Configuration job disappeared after start: {job_id}"
                )
            _log_job_event(job, "running")
            generation_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="configuration-generation",
            )
            future = generation_executor.submit(
                self._service.generate,
                job.request,
            )
            try:
                outcome = future.result(
                    timeout=self._execution_timeout_seconds
                )
            except FutureTimeoutError:
                duration_ms = _duration_ms(started)
                self._store.mark_failed(
                    job_id,
                    "timeout",
                    "Configuration generation timed out",
                    duration_ms,
                )
                _log_job_event(
                    job,
                    "failed",
                    duration_ms=duration_ms,
                    error_code="timeout",
                )
                future.cancel()
                return
            finally:
                generation_executor.shutdown(wait=True, cancel_futures=True)
            duration_ms = _duration_ms(started)
            self._store.mark_completed(job_id, outcome.result, duration_ms)
            _log_job_event(job, "completed", duration_ms=duration_ms)
        except Exception as error:
            duration_ms = _duration_ms(started)
            error_code = (
                "invalid_request"
                if isinstance(error, ConfigurationGenerationError)
                else "generation_failed"
            )
            LOGGER.error(
                "Configuration job failed: job_id=%s request_id=%s "
                "error_type=%s",
                job_id,
                job.request_id if job is not None else "unknown",
                type(error).__name__,
            )
            try:
                self._store.mark_failed(
                    job_id,
                    error_code,
                    "Configuration generation failed",
                    duration_ms,
                )
                if job is not None:
                    _log_job_event(
                        job,
                        "failed",
                        duration_ms=duration_ms,
                        error_code=error_code,
                    )
            except ConfigurationJobError:
                LOGGER.error(
                    "Could not persist configuration job failure: job_id=%s",
                    job_id,
                )


def _job_from_row(row: sqlite3.Row) -> ConfigurationJob:
    try:
        request_data = json.loads(row["request_json"])
        result = (
            json.loads(row["result_json"])
            if row["result_json"] is not None
            else None
        )
        request = ConfigurationGenerationRequest(**request_data)
    except (json.JSONDecodeError, TypeError) as error:
        raise ConfigurationJobError(
            f"Configuration job has invalid persisted JSON: {row['job_id']}"
        ) from error
    return ConfigurationJob(
        job_id=str(row["job_id"]),
        request_id=str(row["request_id"] or row["job_id"]),
        status=row["status"],
        request=request,
        result=result,
        error_code=row["error_code"],
        error=row["error"],
        duration_ms=row["duration_ms"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _log_job_event(
    job: ConfigurationJob,
    status: JobStatus,
    *,
    duration_ms: int | None = None,
    error_code: str | None = None,
) -> None:
    LOGGER.info(
        "configuration_job job_id=%s request_id=%s product=%s release=%s "
        "status=%s duration_ms=%s error_code=%s",
        job.job_id,
        job.request_id,
        job.request.product,
        job.request.release or "",
        status,
        duration_ms if duration_ms is not None else "",
        error_code or "",
    )
