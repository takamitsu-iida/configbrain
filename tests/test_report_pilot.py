import csv
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.config_generation_jobs import ConfigurationJobStore
from scripts.config_generation_service import ConfigurationGenerationRequest
from scripts.report_pilot import (
    FEEDBACK_FIELDS,
    PilotReportError,
    load_feedback,
    load_jobs_from_api,
    run_report,
)


def test_report_cli_can_run_as_a_script() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/report_pilot.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Aggregate pilot jobs" in result.stdout


def create_completed_jobs(database_path: Path, count: int) -> list[str]:
    store = ConfigurationJobStore(database_path)
    store.initialize()
    job_ids: list[str] = []
    for index in range(count):
        request = ConfigurationGenerationRequest(
            requirement=f"SECRET REQUIREMENT {index}",
            product="catalyst9300",
            release="26.x",
        )
        job, _ = store.create_or_get(
            request,
            idempotency_key=None,
            request_id=f"pilot-request-{index}",
            max_unfinished_jobs=count + 1,
        )
        assert store.mark_running(job.job_id)
        store.mark_completed(
            job.job_id,
            {
                "configuration": f"SECRET CONFIGURATION {index}",
                "citations": ["guide:section-0001"],
                "inferred_commands": [],
                "retrieval": [
                    {
                        "citation_id": "guide:section-0001",
                        "product": "catalyst9300",
                        "release": "26.x",
                    }
                ],
            },
            duration_ms=1000 + index,
        )
        job_ids.append(job.job_id)
    return job_ids


def write_feedback(
    path: Path,
    job_ids: list[str],
    *,
    dangerous_index: int | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FEEDBACK_FIELDS)
        writer.writeheader()
        for index, job_id in enumerate(job_ids):
            writer.writerow(
                {
                    "feedback_id": f"feedback-{index}",
                    "job_id": job_id,
                    "reviewer": "reviewer-a",
                    "reviewed_at": "2026-11-14T12:00:00+09:00",
                    "requirement_anonymized": "yes",
                    "verdict": "ready",
                    "expected_configuration": f"EXPECTED SECRET {index}",
                    "corrected_configuration": "",
                    "citations_useful": "yes",
                    "requirement_classification_appropriate": "yes",
                    "dangerous_or_destructive_error": (
                        "yes" if index == dangerous_index else "no"
                    ),
                    "issue_categories": (
                        "generation" if index == dangerous_index else ""
                    ),
                    "comments": f"COMMENT SECRET {index}",
                }
            )


def test_report_meets_exit_criteria_without_exposing_sensitive_text(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "jobs.sqlite3"
    feedback_path = tmp_path / "feedback.csv"
    job_ids = create_completed_jobs(database_path, 31)
    write_feedback(feedback_path, job_ids[:30])

    report = run_report(
        database_path,
        feedback_path,
        tmp_path / "report.json",
        tmp_path / "report.md",
    )

    assert report["summary"] == {
        "jobs": 30,
        "review_records": 30,
        "reviewed_jobs": 30,
        "overall_pass": True,
        "safety_stop": False,
    }
    assert report["job_metrics"]["success_rate_percent"] == 100
    assert report["feedback_metrics"]["usable_or_minor_rate_percent"] == 100
    assert all(report["goals"].values())
    serialized = json.dumps(report, ensure_ascii=False)
    assert "SECRET REQUIREMENT" not in serialized
    assert "SECRET CONFIGURATION" not in serialized
    assert "EXPECTED SECRET" not in serialized
    assert "COMMENT SECRET" not in serialized
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Overall pass: yes" in markdown
    assert "Success rate: 100.00%" in markdown


def test_report_sets_safety_stop_for_dangerous_error(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "jobs.sqlite3"
    feedback_path = tmp_path / "feedback.csv"
    job_ids = create_completed_jobs(database_path, 30)
    write_feedback(feedback_path, job_ids, dangerous_index=0)

    report = run_report(
        database_path,
        feedback_path,
        tmp_path / "report.json",
        tmp_path / "report.md",
    )

    assert report["summary"]["safety_stop"]
    assert not report["summary"]["overall_pass"]
    assert (
        report["feedback_metrics"]["dangerous_or_destructive_errors"]
        == 1
    )
    assert report["feedback_metrics"]["issue_category_counts"] == {
        "generation": 1
    }


def test_feedback_rejects_unknown_issue_category(tmp_path: Path) -> None:
    feedback_path = tmp_path / "feedback.csv"
    with feedback_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FEEDBACK_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "feedback_id": "feedback-1",
                "job_id": "00000000-0000-0000-0000-000000000001",
                "reviewer": "reviewer-a",
                "reviewed_at": "2026-11-14T12:00:00+09:00",
                "requirement_anonymized": "yes",
                "verdict": "unusable",
                "expected_configuration": "",
                "corrected_configuration": "",
                "citations_useful": "no",
                "requirement_classification_appropriate": "no",
                "dangerous_or_destructive_error": "no",
                "issue_categories": "unknown-category",
                "comments": "",
            }
        )

    with pytest.raises(PilotReportError, match="Unknown issue_categories"):
        load_feedback(feedback_path)


def test_report_rejects_feedback_for_unknown_job(tmp_path: Path) -> None:
    database_path = tmp_path / "jobs.sqlite3"
    feedback_path = tmp_path / "feedback.csv"
    create_completed_jobs(database_path, 1)
    write_feedback(
        feedback_path,
        ["00000000-0000-0000-0000-000000000001"],
    )

    with pytest.raises(
        PilotReportError,
        match="Feedback references unknown job_id",
    ):
        run_report(
            database_path,
            feedback_path,
            tmp_path / "report.json",
            tmp_path / "report.md",
        )


def test_load_jobs_from_running_api(tmp_path: Path) -> None:
    feedback_path = tmp_path / "feedback.csv"
    job_id = "00000000-0000-0000-0000-000000000001"
    write_feedback(feedback_path, [job_id])
    feedback = load_feedback(feedback_path)

    class FakeOpener:
        def __init__(self) -> None:
            self.requests: list[tuple[str, str | None, float]] = []

        def open(self, request: object, timeout: float) -> io.StringIO:
            self.requests.append(
                (
                    request.full_url,
                    request.get_header("X-api-key"),
                    timeout,
                )
            )
            return io.StringIO(
                json.dumps(
                    {
                        "job_id": job_id,
                        "status": "completed",
                        "request": {
                            "requirement": "SECRET REQUIREMENT",
                            "product": "catalyst9300",
                            "release": "26.x",
                            "knowledge_policy": "disallow",
                            "top_k": 6,
                        },
                        "result": {
                            "configuration": "SECRET CONFIGURATION",
                            "citations": ["guide:section-0001"],
                            "inferred_commands": [],
                            "retrieval": [
                                {
                                    "citation_id": "guide:section-0001",
                                    "product": "catalyst9300",
                                    "release": "26.x",
                                }
                            ],
                        },
                        "error_code": None,
                        "duration_ms": 1234,
                    }
                )
            )

    opener = FakeOpener()
    jobs = load_jobs_from_api(
        "http://127.0.0.1:8000",
        feedback,
        "SECRET_API_KEY",
        timeout_seconds=12,
        opener=opener,
    )

    assert jobs[0]["job_id"] == job_id
    assert jobs[0]["status"] == "completed"
    assert opener.requests == [
        (
            (
                "http://127.0.0.1:8000/v1/config-generations/"
                f"{job_id}"
            ),
            "SECRET_API_KEY",
            12,
        )
    ]
