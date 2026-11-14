"""Aggregate pilot API jobs and reviewer feedback."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import build_opener, ProxyHandler, Request
import uuid

from dotenv import dotenv_values

if __package__:
    from .pilot_ui import FEEDBACK_FIELDS
else:
    from pilot_ui import FEEDBACK_FIELDS


VERDICTS = ("ready", "minor_changes", "major_changes", "unusable")
TRISTATE_VALUES = ("yes", "no", "unknown")
ISSUE_CATEGORIES = (
    "api",
    "retrieval",
    "generation",
    "requirement",
    "corpus",
    "pipeline",
)
REQUIRED_JOB_COLUMNS = {
    "job_id",
    "request_json",
    "status",
    "result_json",
    "error_code",
    "duration_ms",
}


class PilotReportError(RuntimeError):
    """Raised when pilot records cannot be aggregated safely."""


@dataclass(frozen=True)
class PilotFeedback:
    feedback_id: str
    job_id: str
    reviewer: str
    reviewed_at: str
    requirement_anonymized: str
    verdict: str
    citations_useful: str
    requirement_classification_appropriate: str
    dangerous_or_destructive_error: str
    issue_categories: tuple[str, ...]


def load_feedback(path: Path) -> list[PilotFeedback]:
    if not path.is_file():
        raise PilotReportError(f"Pilot feedback CSV does not exist: {path}")
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fieldnames = tuple(reader.fieldnames or ())
            if fieldnames != FEEDBACK_FIELDS:
                raise PilotReportError(
                    "Pilot feedback CSV header does not match the template"
                )
            rows = [
                row
                for row in reader
                if any((value or "").strip() for value in row.values())
            ]
    except OSError as error:
        raise PilotReportError(f"Could not read pilot feedback CSV: {path}") from error
    feedback: list[PilotFeedback] = []
    seen_ids: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        feedback_id = _required(row, "feedback_id", row_number)
        if feedback_id in seen_ids:
            raise PilotReportError(
                f"Duplicate feedback_id at row {row_number}: {feedback_id}"
            )
        seen_ids.add(feedback_id)
        job_id = _required(row, "job_id", row_number)
        try:
            uuid.UUID(job_id)
        except ValueError as error:
            raise PilotReportError(
                f"Invalid job_id at row {row_number}: {job_id}"
            ) from error
        reviewer = _required(row, "reviewer", row_number)
        reviewed_at = _required(row, "reviewed_at", row_number)
        try:
            datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise PilotReportError(
                f"Invalid reviewed_at at row {row_number}: {reviewed_at}"
            ) from error
        requirement_anonymized = _choice(
            row,
            "requirement_anonymized",
            ("yes", "no"),
            row_number,
        )
        verdict = _choice(row, "verdict", VERDICTS, row_number)
        citations_useful = _choice(
            row,
            "citations_useful",
            TRISTATE_VALUES,
            row_number,
        )
        classification = _choice(
            row,
            "requirement_classification_appropriate",
            TRISTATE_VALUES,
            row_number,
        )
        dangerous = _choice(
            row,
            "dangerous_or_destructive_error",
            ("yes", "no"),
            row_number,
        )
        categories = tuple(
            dict.fromkeys(
                value.strip()
                for value in (row.get("issue_categories") or "").split(";")
                if value.strip()
            )
        )
        unknown_categories = set(categories) - set(ISSUE_CATEGORIES)
        if unknown_categories:
            raise PilotReportError(
                f"Unknown issue_categories at row {row_number}: "
                + ", ".join(sorted(unknown_categories))
            )
        feedback.append(
            PilotFeedback(
                feedback_id=feedback_id,
                job_id=job_id,
                reviewer=reviewer,
                reviewed_at=reviewed_at,
                requirement_anonymized=requirement_anonymized,
                verdict=verdict,
                citations_useful=citations_useful,
                requirement_classification_appropriate=classification,
                dangerous_or_destructive_error=dangerous,
                issue_categories=categories,
            )
        )
    return feedback


def load_jobs(database_path: Path) -> list[dict[str, Any]]:
    if not database_path.is_file():
        raise PilotReportError(f"Job database does not exist: {database_path}")
    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(configuration_jobs)"
                )
            }
            missing = REQUIRED_JOB_COLUMNS - columns
            if missing:
                raise PilotReportError(
                    "Job database is missing columns: "
                    + ", ".join(sorted(missing))
                )
            rows = connection.execute(
                """
                SELECT job_id, request_json, status, result_json,
                       error_code, duration_ms
                FROM configuration_jobs
                ORDER BY created_at, job_id
                """
            ).fetchall()
    except sqlite3.Error as error:
        raise PilotReportError(
            f"Could not read job database: {database_path}"
        ) from error
    jobs: list[dict[str, Any]] = []
    for row in rows:
        request = _parse_json_object(
            row["request_json"],
            f"job request {row['job_id']}",
        )
        result = (
            _parse_json_object(
                row["result_json"],
                f"job result {row['job_id']}",
            )
            if row["result_json"] is not None
            else None
        )
        jobs.append(
            {
                "job_id": str(row["job_id"]),
                "request": request,
                "status": str(row["status"]),
                "result": result,
                "error_code": row["error_code"],
                "duration_ms": row["duration_ms"],
            }
        )
    return jobs


def load_jobs_from_api(
    base_url: str,
    feedback: list[PilotFeedback],
    api_key: str,
    *,
    timeout_seconds: float = 30,
    opener: Any | None = None,
) -> list[dict[str, Any]]:
    parsed_base_url = urlparse(base_url)
    if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
        raise PilotReportError(f"Invalid API base URL: {base_url}")
    if timeout_seconds <= 0:
        raise PilotReportError("API timeout must be greater than zero")
    client = opener or build_opener(ProxyHandler({}))
    jobs: list[dict[str, Any]] = []
    for job_id in sorted({item.job_id for item in feedback}):
        url = urljoin(
            base_url.rstrip("/") + "/",
            f"v1/config-generations/{job_id}",
        )
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "X-API-Key": api_key,
            },
        )
        try:
            with client.open(request, timeout=timeout_seconds) as response:
                job = json.load(response)
        except HTTPError as error:
            raise PilotReportError(
                f"API returned HTTP {error.code} for job_id {job_id}"
            ) from error
        except (URLError, OSError, json.JSONDecodeError) as error:
            raise PilotReportError(
                f"Could not read job_id {job_id} from the API"
            ) from error
        if not isinstance(job, dict) or job.get("job_id") != job_id:
            raise PilotReportError(
                f"API returned an invalid job response for job_id {job_id}"
            )
        request_payload = job.get("request")
        result = job.get("result")
        if not isinstance(request_payload, dict) or (
            result is not None and not isinstance(result, dict)
        ):
            raise PilotReportError(
                f"API returned an invalid job payload for job_id {job_id}"
            )
        jobs.append(
            {
                "job_id": job_id,
                "request": request_payload,
                "status": str(job.get("status", "")),
                "result": result,
                "error_code": job.get("error_code"),
                "duration_ms": job.get("duration_ms"),
            }
        )
    return jobs


def build_pilot_report(
    jobs: list[dict[str, Any]],
    feedback: list[PilotFeedback],
) -> dict[str, Any]:
    job_by_id = {job["job_id"]: job for job in jobs}
    unmatched_job_ids = sorted(
        {
            item.job_id
            for item in feedback
            if item.job_id not in job_by_id
        }
    )
    if unmatched_job_ids:
        raise PilotReportError(
            "Feedback references unknown job_id values: "
            + ", ".join(unmatched_job_ids)
        )
    reviewed_job_ids = {item.job_id for item in feedback}
    pilot_jobs = [
        job for job in jobs if job["job_id"] in reviewed_job_ids
    ]
    status_counts = Counter(job["status"] for job in pilot_jobs)
    completed_jobs = [
        job for job in pilot_jobs if job["status"] == "completed"
    ]
    durations = sorted(
        int(job["duration_ms"])
        for job in pilot_jobs
        if isinstance(job.get("duration_ms"), int)
    )
    product_counts = Counter(
        str(job["request"].get("product", ""))
        for job in pilot_jobs
    )
    release_counts = Counter(
        str(job["request"].get("release") or "(unspecified)")
        for job in pilot_jobs
    )
    error_counts = Counter(
        str(job["error_code"] or "unknown")
        for job in pilot_jobs
        if job["status"] == "failed"
    )
    product_isolation_violations = [
        job["job_id"]
        for job in completed_jobs
        if not _product_isolated(job)
    ]
    citation_integrity_violations = [
        job["job_id"]
        for job in completed_jobs
        if not _citations_valid(job)
    ]
    inferred_command_violations = [
        job["job_id"]
        for job in completed_jobs
        if bool((job.get("result") or {}).get("inferred_commands"))
    ]
    matched_feedback = feedback
    verdict_counts = Counter(item.verdict for item in matched_feedback)
    issue_counts = Counter(
        category
        for item in matched_feedback
        for category in item.issue_categories
    )
    reviewed_job_count = len(
        {item.job_id for item in matched_feedback}
    )
    safety_issue_count = sum(
        item.dangerous_or_destructive_error == "yes"
        for item in matched_feedback
    )
    anonymization_violation_count = sum(
        item.requirement_anonymized == "no"
        for item in matched_feedback
    )
    usable_or_minor_rate = _percentage(
        sum(
            item.verdict in {"ready", "minor_changes"}
            for item in matched_feedback
        ),
        len(matched_feedback),
    )
    citation_usefulness_rate = _yes_rate(
        item.citations_useful for item in matched_feedback
    )
    classification_rate = _yes_rate(
        item.requirement_classification_appropriate
        for item in matched_feedback
    )
    job_success_rate = _percentage(
        len(completed_jobs),
        len(pilot_jobs),
    )
    goals = {
        "at_least_30_reviewed_jobs": reviewed_job_count >= 30,
        "job_success_rate_at_least_95_percent": (
            job_success_rate is not None and job_success_rate >= 95
        ),
        "no_product_isolation_violations": not product_isolation_violations,
        "no_citation_integrity_violations": not citation_integrity_violations,
        "no_inferred_commands": not inferred_command_violations,
        "no_dangerous_or_destructive_errors": safety_issue_count == 0,
        "usable_or_minor_rate_at_least_80_percent": (
            usable_or_minor_rate is not None
            and usable_or_minor_rate >= 80
        ),
        "citation_usefulness_at_least_90_percent": (
            citation_usefulness_rate is not None
            and citation_usefulness_rate >= 90
        ),
        "classification_accuracy_at_least_90_percent": (
            classification_rate is not None
            and classification_rate >= 90
        ),
        "all_feedback_jobs_exist": not unmatched_job_ids,
        "all_requirements_anonymized": anonymization_violation_count == 0,
    }
    return {
        "summary": {
            "jobs": len(pilot_jobs),
            "review_records": len(feedback),
            "reviewed_jobs": reviewed_job_count,
            "overall_pass": all(goals.values()),
            "safety_stop": safety_issue_count > 0,
        },
        "job_metrics": {
            "status_counts": dict(sorted(status_counts.items())),
            "success_rate_percent": job_success_rate,
            "duration_ms": {
                "count": len(durations),
                "p50": _percentile(durations, 50),
                "p95": _percentile(durations, 95),
                "minimum": min(durations) if durations else None,
                "maximum": max(durations) if durations else None,
            },
            "product_counts": dict(sorted(product_counts.items())),
            "release_counts": dict(sorted(release_counts.items())),
            "error_code_counts": dict(sorted(error_counts.items())),
            "product_isolation_violations": product_isolation_violations,
            "citation_integrity_violations": citation_integrity_violations,
            "inferred_command_violations": inferred_command_violations,
        },
        "feedback_metrics": {
            "verdict_counts": dict(sorted(verdict_counts.items())),
            "usable_or_minor_rate_percent": usable_or_minor_rate,
            "citation_usefulness_percent": citation_usefulness_rate,
            "classification_accuracy_percent": classification_rate,
            "dangerous_or_destructive_errors": safety_issue_count,
            "anonymization_violations": anonymization_violation_count,
            "issue_category_counts": dict(sorted(issue_counts.items())),
            "unmatched_job_ids": unmatched_job_ids,
        },
        "goals": goals,
    }


def format_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    jobs = report["job_metrics"]
    feedback = report["feedback_metrics"]
    goals = report["goals"]
    lines = [
        "# Pilot evaluation report",
        "",
        "## Summary",
        "",
        f"- Jobs: {summary['jobs']}",
        f"- Review records: {summary['review_records']}",
        f"- Reviewed jobs: {summary['reviewed_jobs']}",
        f"- Overall pass: {'yes' if summary['overall_pass'] else 'no'}",
        f"- Safety stop: {'yes' if summary['safety_stop'] else 'no'}",
        "",
        "## Job metrics",
        "",
        f"- Success rate: {_format_percent(jobs['success_rate_percent'])}",
        f"- Duration p50: {_format_ms(jobs['duration_ms']['p50'])}",
        f"- Duration p95: {_format_ms(jobs['duration_ms']['p95'])}",
        f"- Product isolation violations: {len(jobs['product_isolation_violations'])}",
        f"- Citation integrity violations: {len(jobs['citation_integrity_violations'])}",
        f"- Inferred command violations: {len(jobs['inferred_command_violations'])}",
        "",
        "## Feedback metrics",
        "",
        f"- Ready or minor changes: {_format_percent(feedback['usable_or_minor_rate_percent'])}",
        f"- Citations useful: {_format_percent(feedback['citation_usefulness_percent'])}",
        f"- Requirement classification appropriate: {_format_percent(feedback['classification_accuracy_percent'])}",
        f"- Dangerous or destructive errors: {feedback['dangerous_or_destructive_errors']}",
        f"- Anonymization violations: {feedback['anonymization_violations']}",
        f"- Unmatched job IDs: {len(feedback['unmatched_job_ids'])}",
        "",
        "## Verdicts",
        "",
        "| Verdict | Count |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {verdict} | {feedback['verdict_counts'].get(verdict, 0)} |"
        for verdict in VERDICTS
    )
    lines.extend(
        [
            "",
            "## Issue categories",
            "",
            "| Category | Count |",
            "|---|---:|",
        ]
    )
    lines.extend(
        f"| {category} | {feedback['issue_category_counts'].get(category, 0)} |"
        for category in ISSUE_CATEGORIES
    )
    lines.extend(
        [
            "",
            "## Exit criteria",
            "",
            "| Criterion | Pass |",
            "|---|---|",
        ]
    )
    lines.extend(
        f"| {name.replace('_', ' ')} | {'yes' if passed else 'no'} |"
        for name, passed in goals.items()
    )
    return "\n".join(lines) + "\n"


def run_report(
    jobs_database: Path,
    feedback_csv: Path,
    json_output: Path,
    markdown_output: Path,
) -> dict[str, Any]:
    report = build_pilot_report(
        load_jobs(jobs_database),
        load_feedback(feedback_csv),
    )
    _write_text(
        json_output,
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    _write_text(markdown_output, format_markdown(report))
    return report


def run_api_report(
    base_url: str,
    api_key: str,
    feedback_csv: Path,
    json_output: Path,
    markdown_output: Path,
    *,
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    feedback = load_feedback(feedback_csv)
    report = build_pilot_report(
        load_jobs_from_api(
            base_url,
            feedback,
            api_key,
            timeout_seconds=timeout_seconds,
        ),
        feedback,
    )
    _write_text(
        json_output,
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    _write_text(markdown_output, format_markdown(report))
    return report


def _product_isolated(job: dict[str, Any]) -> bool:
    request = job["request"]
    result = job.get("result") or {}
    retrieval = result.get("retrieval")
    if not isinstance(retrieval, list) or not retrieval:
        return False
    return all(
        isinstance(item, dict)
        and item.get("product") == request.get("product")
        and (
            not request.get("release")
            or item.get("release") == request.get("release")
        )
        for item in retrieval
    )


def _citations_valid(job: dict[str, Any]) -> bool:
    result = job.get("result") or {}
    citations = result.get("citations")
    retrieval = result.get("retrieval")
    if not isinstance(citations, list) or not citations:
        return False
    if not isinstance(retrieval, list):
        return False
    available = {
        item.get("citation_id")
        for item in retrieval
        if isinstance(item, dict)
    }
    return set(citations).issubset(available)


def _parse_json_object(value: str, context: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise PilotReportError(f"Invalid JSON in {context}") from error
    if not isinstance(parsed, dict):
        raise PilotReportError(f"{context} must be a JSON object")
    return parsed


def _required(row: dict[str, str | None], field: str, row_number: int) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        raise PilotReportError(f"Missing {field} at row {row_number}")
    return value


def _choice(
    row: dict[str, str | None],
    field: str,
    choices: tuple[str, ...],
    row_number: int,
) -> str:
    value = _required(row, field, row_number)
    if value not in choices:
        raise PilotReportError(
            f"Invalid {field} at row {row_number}: {value}"
        )
    return value


def _yes_rate(values: Any) -> float | None:
    selected = [value for value in values if value in {"yes", "no"}]
    return _percentage(selected.count("yes"), len(selected))


def _percentage(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator * 100 / denominator, 2)


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    index = max(0, math.ceil(percentile / 100 * len(values)) - 1)
    return values[index]


def _format_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}%"


def _format_ms(value: int | None) -> str:
    return "n/a" if value is None else f"{value} ms"


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Aggregate pilot jobs and reviewer feedback."
    )
    parser.add_argument(
        "--jobs-db",
        type=Path,
        default=Path("data/api/jobs.sqlite3"),
    )
    parser.add_argument(
        "--feedback",
        type=Path,
        default=Path("data/evaluation/pilot/feedback.csv"),
    )
    parser.add_argument(
        "--base-url",
        help=(
            "Read reviewed jobs from a running API instead of the SQLite "
            "database."
        ),
    )
    parser.add_argument(
        "--api-key-env",
        default="CONFIGBRAIN_API_KEY",
        help="Environment variable containing the API key.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Environment file used when the API key is not exported.",
    )
    parser.add_argument(
        "--api-timeout",
        type=float,
        default=30,
        help="Per-job API request timeout in seconds.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("data/evaluation/pilot/report.json"),
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=Path("data/evaluation/pilot/report.md"),
    )
    parser.add_argument(
        "--require-goals",
        action="store_true",
        help="Exit with status 2 when pilot exit criteria are not met.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.base_url:
            env_values = (
                dotenv_values(args.env_file)
                if args.env_file.is_file()
                else {}
            )
            api_key = (
                os.environ.get(args.api_key_env)
                or env_values.get(args.api_key_env)
            )
            if not api_key or not api_key.strip():
                raise PilotReportError(
                    f"{args.api_key_env} must be configured"
                )
            report = run_api_report(
                args.base_url,
                api_key.strip(),
                args.feedback,
                args.json_output,
                args.markdown_output,
                timeout_seconds=args.api_timeout,
            )
        else:
            report = run_report(
                args.jobs_db,
                args.feedback,
                args.json_output,
                args.markdown_output,
            )
    except PilotReportError as error:
        print(f"Pilot report failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if args.require_goals and not report["summary"]["overall_pass"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
