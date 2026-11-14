"""Storage and editable template helpers for the pilot web UI."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Mapping


FEEDBACK_FIELDS = (
    "feedback_id",
    "job_id",
    "reviewer",
    "reviewed_at",
    "requirement_anonymized",
    "verdict",
    "expected_configuration",
    "corrected_configuration",
    "citations_useful",
    "requirement_classification_appropriate",
    "dangerous_or_destructive_error",
    "issue_categories",
    "comments",
)


class PilotUIError(RuntimeError):
    """Raised when pilot UI data cannot be read or written safely."""


class DuplicateFeedbackError(PilotUIError):
    """Raised when a feedback ID is reused."""


class FeedbackNotFoundError(PilotUIError):
    """Raised when feedback to update does not exist."""


@dataclass(frozen=True)
class RequirementTemplate:
    label: str
    requirement: str


_FEEDBACK_LOCK = Lock()


def load_requirement_templates(path: Path) -> list[RequirementTemplate]:
    if not path.is_file():
        raise PilotUIError(f"Requirement template file does not exist: {path}")
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as error:
        raise PilotUIError(
            f"Could not read requirement template file: {path}"
        ) from error
    templates: list[RequirementTemplate] = []
    seen_labels: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        columns = line.split("\t")
        if len(columns) != 2:
            raise PilotUIError(
                f"Requirement template line {line_number} must contain "
                "one tab"
            )
        label, requirement = (column.strip() for column in columns)
        if not label or not requirement:
            raise PilotUIError(
                f"Requirement template line {line_number} has an empty value"
            )
        if label in seen_labels:
            raise PilotUIError(
                f"Duplicate requirement template label at line "
                f"{line_number}: {label}"
            )
        seen_labels.add(label)
        templates.append(
            RequirementTemplate(
                label=label,
                requirement=requirement,
            )
        )
    if not templates:
        raise PilotUIError("Requirement template file has no templates")
    return templates


def append_feedback(
    path: Path,
    record: Mapping[str, str],
) -> None:
    if set(record) != set(FEEDBACK_FIELDS):
        raise PilotUIError("Pilot feedback record has unexpected fields")
    path.parent.mkdir(parents=True, exist_ok=True)
    with _FEEDBACK_LOCK:
        existing_rows = _read_feedback_rows(path)
        feedback_id = record["feedback_id"]
        if any(row.get("feedback_id") == feedback_id for row in existing_rows):
            raise DuplicateFeedbackError(
                f"Feedback ID already exists: {feedback_id}"
            )
        _write_feedback_rows(path, [*existing_rows, dict(record)])


def update_feedback(
    path: Path,
    record: Mapping[str, str],
) -> None:
    if set(record) != set(FEEDBACK_FIELDS):
        raise PilotUIError("Pilot feedback record has unexpected fields")
    with _FEEDBACK_LOCK:
        existing_rows = _read_feedback_rows(path)
        feedback_id = record["feedback_id"]
        updated = False
        rows: list[dict[str, str]] = []
        for existing in existing_rows:
            if existing.get("feedback_id") == feedback_id:
                if existing.get("job_id") != record["job_id"]:
                    raise PilotUIError(
                        "Pilot feedback job_id cannot be changed"
                    )
                rows.append(dict(record))
                updated = True
            else:
                rows.append(existing)
        if not updated:
            raise FeedbackNotFoundError(
                f"Feedback ID does not exist: {feedback_id}"
            )
        _write_feedback_rows(path, rows)


def _read_feedback_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != FEEDBACK_FIELDS:
                raise PilotUIError(
                    "Pilot feedback CSV header does not match the template"
                )
            return [dict(row) for row in reader]
    except OSError as error:
        raise PilotUIError(
            f"Could not read pilot feedback CSV: {path}"
        ) from error


def _write_feedback_rows(
    path: Path,
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary_path.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=FEEDBACK_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        temporary_path.replace(path)
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        raise PilotUIError(
            f"Could not write pilot feedback CSV: {path}"
        ) from error
