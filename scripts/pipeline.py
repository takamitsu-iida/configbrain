"""Persist and inspect orchestration state for the manual conversion pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shutil
import sqlite3
import sys
import threading
import time
from socket import gaierror, timeout as SocketTimeout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError

import requests

if __package__:
    from .chunk_markdown import (
        CHARS_PER_TOKEN,
        create_chunks,
        save_chunks,
        sha256_file as chunk_sha256_file,
    )
    from .cloud_vision import CloudVisionClient, load_config as load_vision_config
    from .download_manuals import download_document
    from .extract import extract_markdown
    from .ocr_batch import run_batch
    from .ocr_candidates import build_manifest, save_manifest
    from .ocr_integrate import integrate_ocr, save_markdown
    from .package_document import build_package
    from .transform_markdown import (
        GracefulStopRequested,
        SYSTEM_PROMPT,
        OpenAICompatibleClient,
        QuotaThresholdError,
        load_config,
        transform_chunks,
    )
else:
    from chunk_markdown import (
        CHARS_PER_TOKEN,
        create_chunks,
        save_chunks,
        sha256_file as chunk_sha256_file,
    )
    from cloud_vision import CloudVisionClient, load_config as load_vision_config
    from download_manuals import download_document
    from extract import extract_markdown
    from ocr_batch import run_batch
    from ocr_candidates import build_manifest, save_manifest
    from ocr_integrate import integrate_ocr, save_markdown
    from package_document import build_package
    from transform_markdown import (
        GracefulStopRequested,
        SYSTEM_PROMPT,
        OpenAICompatibleClient,
        QuotaThresholdError,
        load_config,
        transform_chunks,
    )


class PipelineError(RuntimeError):
    """Raised when pipeline state cannot be loaded or updated safely."""


class PipelineQuotaStop(PipelineError):
    """Raised when an LLM quota threshold stops the pipeline."""


class PipelineGracefulStop(PipelineError):
    """Raised after pipeline state is safely checkpointed for shutdown."""


PHASES = ("download", "extraction", "ocr", "chunking", "transformation", "packaging")
STATUSES = ("pending", "running", "completed", "failed", "stale", "skipped")
CHUNK_MAX_ESTIMATED_TOKENS = 6_000
OCR_SELECTION = {
    "min_text_chars": 50,
    "min_image_area": 100_000,
    "image_kinds": ["Diagram"],
}
PACKAGE_SPECIFICATION_VERSION = 3
DEFAULT_PRODUCT_DATA_ROOT = Path("data/products/catalyst9300")
PRODUCTS_DATA_ROOT = Path("data/products")
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY = 1.0
TRANSIENT_HTTP_STATUS_PATTERN = re.compile(r"\bHTTP (?P<status>429|5\d{2})\b")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def connect(database_path: Path) -> sqlite3.Connection:
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
    except sqlite3.Error as error:
        raise PipelineError(f"Could not open state database: {database_path}") from error
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            document_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            filename TEXT NOT NULL,
            url TEXT NOT NULL,
            catalog_fingerprint TEXT NOT NULL,
            catalog_status TEXT NOT NULL CHECK (catalog_status IN ('active', 'removed')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS phase_states (
            document_id TEXT NOT NULL REFERENCES documents(document_id),
            phase TEXT NOT NULL CHECK (phase IN ('download', 'extraction', 'ocr', 'chunking', 'transformation', 'packaging')),
            status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'stale', 'skipped')),
            input_sha256 TEXT,
            output_sha256 TEXT,
            fingerprint TEXT,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            error TEXT,
            started_at TEXT,
            completed_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (document_id, phase)
        );
        CREATE INDEX IF NOT EXISTS phase_states_status_index
        ON phase_states (phase, status);
        """
    )
    connection.commit()


def load_catalog(path: Path) -> dict[str, Any]:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PipelineError(f"Could not read catalog: {path}") from error
    if not isinstance(catalog, dict):
        raise PipelineError("Catalog must be a JSON object")
    if catalog.get("schema_version") != 1:
        raise PipelineError("Catalog schema_version must be 1")
    for field in ("product", "release", "seed_url"):
        if not isinstance(catalog.get(field), str) or not catalog[field].strip():
            raise PipelineError(f"Catalog is missing a valid {field}")
    documents = catalog.get("documents")
    if not isinstance(documents, list):
        raise PipelineError("Catalog documents must be a JSON array")
    document_ids: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            raise PipelineError("Each catalog document must be a JSON object")
        for field in ("document_id", "title", "category", "filename", "url"):
            if not isinstance(document.get(field), str) or not document[field]:
                raise PipelineError(f"Catalog document is missing a valid {field}")
        document_id = document["document_id"]
        if document_id in document_ids:
            raise PipelineError(f"Catalog has duplicate document_id: {document_id}")
        document_ids.add(document_id)
    return catalog


def catalog_document_fingerprint(document: dict[str, str]) -> str:
    return fingerprint(
        {
            field: document[field]
            for field in ("document_id", "title", "category", "filename", "url")
        }
    )


def phase_rows(document_id: str, now: str) -> Iterator[tuple[str, str, str]]:
    for phase in PHASES:
        yield document_id, phase, now


def sync_catalog(connection: sqlite3.Connection, catalog: dict[str, Any]) -> dict[str, int]:
    now = utc_now()
    documents = catalog["documents"]
    active_ids = {document["document_id"] for document in documents}
    existing_rows = {
        row["document_id"]: row
        for row in connection.execute(
            "SELECT document_id, catalog_fingerprint, catalog_status FROM documents"
        )
    }
    new = updated = reactivated = removed = 0

    with connection:
        for document in documents:
            document_id = document["document_id"]
            document_fingerprint = catalog_document_fingerprint(document)
            existing = existing_rows.get(document_id)
            if existing is None:
                new += 1
                connection.execute(
                    """
                    INSERT INTO documents (
                        document_id, title, category, filename, url,
                        catalog_fingerprint, catalog_status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        document_id,
                        document["title"],
                        document["category"],
                        document["filename"],
                        document["url"],
                        document_fingerprint,
                        now,
                        now,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO phase_states (document_id, phase, status, updated_at)
                    VALUES (?, ?, 'pending', ?)
                    """,
                    phase_rows(document_id, now),
                )
                continue

            changed = existing["catalog_fingerprint"] != document_fingerprint
            was_removed = existing["catalog_status"] == "removed"
            if changed:
                updated += 1
            if was_removed:
                reactivated += 1
            connection.execute(
                """
                UPDATE documents
                SET title = ?, category = ?, filename = ?, url = ?,
                    catalog_fingerprint = ?, catalog_status = 'active', updated_at = ?
                WHERE document_id = ?
                """,
                (
                    document["title"],
                    document["category"],
                    document["filename"],
                    document["url"],
                    document_fingerprint,
                    now,
                    document_id,
                ),
            )
            if was_removed:
                connection.execute(
                    """
                    UPDATE phase_states
                    SET status = 'pending', error = NULL, updated_at = ?
                    WHERE document_id = ? AND status = 'skipped'
                    """,
                    (now, document_id),
                )

        for document_id, existing in existing_rows.items():
            if document_id in active_ids or existing["catalog_status"] == "removed":
                continue
            removed += 1
            connection.execute(
                """
                UPDATE documents
                SET catalog_status = 'removed', updated_at = ?
                WHERE document_id = ?
                """,
                (now, document_id),
            )
            connection.execute(
                """
                UPDATE phase_states
                SET status = 'skipped', error = 'Document removed from catalog',
                    updated_at = ?
                WHERE document_id = ?
                """,
                (now, document_id),
            )
    return {"new": new, "updated": updated, "reactivated": reactivated, "removed": removed}


def plan_summary(connection: sqlite3.Connection, chunks_root: Path) -> dict[str, Any]:
    document_count = connection.execute(
        "SELECT COUNT(*) FROM documents WHERE catalog_status = 'active'"
    ).fetchone()[0]
    phase_counts = {
        phase: {
            status: count
            for status, count in connection.execute(
                """
                SELECT status, COUNT(*) FROM phase_states
                JOIN documents USING (document_id)
                WHERE catalog_status = 'active' AND phase = ?
                GROUP BY status
                """,
                (phase,),
            )
        }
        for phase in PHASES
    }
    known_transform_calls = 0
    unknown_transform_documents = 0
    for row in connection.execute(
        "SELECT document_id FROM documents WHERE catalog_status = 'active' ORDER BY document_id"
    ):
        manifest_path = chunks_root / row["document_id"] / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            chunks = manifest.get("chunks")
            if not isinstance(chunks, list) or not chunks:
                raise ValueError
        except (OSError, json.JSONDecodeError, ValueError):
            unknown_transform_documents += 1
        else:
            known_transform_calls += len(chunks)
    return {
        "documents": document_count,
        "phase_counts": phase_counts,
        "gpt_calls": {
            "known": known_transform_calls,
            "unknown_documents": unknown_transform_documents,
        },
    }


def status_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT d.document_id, d.catalog_status, p.phase, p.status, p.attempts,
               p.input_sha256, p.output_sha256, p.fingerprint, p.error,
               p.started_at, p.completed_at, p.updated_at
        FROM phase_states AS p
        JOIN documents AS d USING (document_id)
        ORDER BY d.document_id, CASE p.phase
            WHEN 'download' THEN 1 WHEN 'extraction' THEN 2 WHEN 'ocr' THEN 3
            WHEN 'chunking' THEN 4 WHEN 'transformation' THEN 5 WHEN 'packaging' THEN 6
        END
        """
    ).fetchall()
    return [dict(row) for row in rows]


def directory_sha256(path: Path) -> str:
    if not path.is_dir():
        raise PipelineError(f"Output directory does not exist: {path}")
    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    if not files:
        raise PipelineError(f"Output directory contains no files: {path}")
    for item in files:
        relative_path = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative_path).to_bytes(4, "big"))
        digest.update(relative_path)
        digest.update(chunk_sha256_file(item).encode("ascii"))
    return digest.hexdigest()


def phase_configuration(
    phase: str,
    *,
    disable_marker_ocr: bool = True,
    skip_cloud_vision: bool = True,
) -> dict[str, Any]:
    if phase == "download":
        return {"user_agent": "Wget/1.21.4"}
    if phase == "extraction":
        return {"marker_ocr_disabled": disable_marker_ocr}
    if phase == "ocr":
        return {**OCR_SELECTION, "skip_cloud_vision": skip_cloud_vision}
    if phase == "chunking":
        return {
            "max_estimated_tokens": CHUNK_MAX_ESTIMATED_TOKENS,
            "characters_per_token": CHARS_PER_TOKEN,
            "strategy": "heading-first, then paragraph-boundary",
        }
    if phase == "transformation":
        return {
            "model": os.environ.get("LLM_MODEL", ""),
            "system_prompt": SYSTEM_PROMPT,
        }
    if phase == "packaging":
        return {"specification_version": PACKAGE_SPECIFICATION_VERSION}
    raise PipelineError(f"Unknown pipeline phase: {phase}")


def phase_fingerprint(
    phase: str,
    input_sha256: str | None,
    *,
    disable_marker_ocr: bool = True,
    skip_cloud_vision: bool = True,
) -> str:
    return fingerprint(
        {
            "phase": phase,
            "input_sha256": input_sha256,
            "configuration": phase_configuration(
                phase,
                disable_marker_ocr=disable_marker_ocr,
                skip_cloud_vision=skip_cloud_vision,
            ),
        }
    )


def phase_output_path(phase: str, paths: dict[str, Path]) -> Path:
    if phase == "download":
        return paths["pdf"]
    if phase == "extraction":
        return paths["extracted_root"]
    if phase == "ocr":
        return paths["processed_markdown"]
    if phase == "chunking":
        return paths["chunks"]
    if phase == "transformation":
        return paths["transformed"]
    if phase == "packaging":
        return paths["package"]
    raise PipelineError(f"Unknown pipeline phase: {phase}")


def output_sha256(phase: str, paths: dict[str, Path]) -> str:
    output = phase_output_path(phase, paths)
    return directory_sha256(output) if output.is_dir() else chunk_sha256_file(output)


def invalidate_downstream(
    connection: sqlite3.Connection, document_id: str, first_phase: str, reason: str
) -> None:
    first_index = PHASES.index(first_phase)
    now = utc_now()
    with connection:
        connection.execute(
            """
            UPDATE phase_states
            SET status = 'stale', error = ?, completed_at = NULL, updated_at = ?
            WHERE document_id = ? AND phase IN ({})
              AND status NOT IN ('pending', 'running', 'failed')
            """.format(",".join("?" for _ in PHASES[first_index:])),
            (reason, now, document_id, *PHASES[first_index:]),
        )


def invalidate_changed_documents(
    connection: sqlite3.Connection,
    catalog: dict[str, Any],
    roots: dict[str, Path],
    *,
    document_ids: set[str] | None = None,
    disable_marker_ocr: bool = True,
    skip_cloud_vision: bool = True,
    validate_configuration: bool = True,
) -> dict[str, int]:
    documents = {
        document["document_id"]: document
        for document in catalog["documents"]
        if document_ids is None or document["document_id"] in document_ids
    }
    states = {
        (row["document_id"], row["phase"]): row
        for row in connection.execute(
            """
            SELECT p.* FROM phase_states AS p
            JOIN documents AS d USING (document_id)
            WHERE d.catalog_status = 'active'
            """
        )
    }
    invalidated = {phase: 0 for phase in PHASES}
    for document_id, document in documents.items():
        paths = document_paths(document, roots)
        for phase in PHASES:
            state = states[(document_id, phase)]
            if state["status"] not in {"completed", "skipped"}:
                continue
            try:
                current_input = _phase_input(document, phase, paths)
                current_output = output_sha256(phase, paths)
            except (OSError, PipelineError):
                current_input = None
                current_output = None
            expected_fingerprint = (
                phase_fingerprint(
                    phase,
                    current_input,
                    disable_marker_ocr=disable_marker_ocr,
                    skip_cloud_vision=skip_cloud_vision,
                )
                if validate_configuration
                else state["fingerprint"]
            )
            if (
                state["input_sha256"] != current_input
                or state["output_sha256"] != current_output
                or state["fingerprint"] != expected_fingerprint
            ):
                invalidate_downstream(
                    connection,
                    document_id,
                    phase,
                    f"Invalidated: {phase} input, output, or configuration changed",
                )
                invalidated[phase] += 1
                break
    return invalidated


def remove_phase_output(phase: str, paths: dict[str, Path]) -> None:
    if phase == "ocr":
        if paths["ocr_dir"].is_dir():
            shutil.rmtree(paths["ocr_dir"])
        if paths["processed_markdown"].exists():
            paths["processed_markdown"].unlink()
        return
    output = phase_output_path(phase, paths)
    if output.is_dir():
        shutil.rmtree(output)
    elif output.exists():
        output.unlink()


def mark_phase_running(connection: sqlite3.Connection, document_id: str, phase: str) -> None:
    now = utc_now()
    with connection:
        connection.execute(
            """
            UPDATE phase_states
            SET status = 'running', attempts = attempts + 1, error = NULL,
                started_at = ?, completed_at = NULL, updated_at = ?
            WHERE document_id = ? AND phase = ?
            """,
            (now, now, document_id, phase),
        )


def mark_phase_finished(
    connection: sqlite3.Connection,
    document_id: str,
    phase: str,
    status: str,
    input_sha256: str | None,
    output_sha256: str | None,
    phase_fingerprint: str,
) -> None:
    if status not in {"completed", "skipped"}:
        raise PipelineError(f"Invalid completed phase status: {status}")
    now = utc_now()
    with connection:
        connection.execute(
            """
            UPDATE phase_states
            SET status = ?, input_sha256 = ?, output_sha256 = ?, fingerprint = ?,
                error = NULL, completed_at = ?, updated_at = ?
            WHERE document_id = ? AND phase = ?
            """,
            (
                status,
                input_sha256,
                output_sha256,
                phase_fingerprint,
                now,
                now,
                document_id,
                phase,
            ),
        )


def mark_phase_failed(
    connection: sqlite3.Connection, document_id: str, phase: str, error: Exception
) -> None:
    now = utc_now()
    with connection:
        connection.execute(
            """
            UPDATE phase_states
            SET status = 'failed', error = ?, completed_at = NULL, updated_at = ?
            WHERE document_id = ? AND phase = ?
            """,
            (str(error), now, document_id, phase),
        )


def exception_chain(error: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        yield current
        seen.add(id(current))
        current = current.__cause__ or current.__context__


def is_transient_error(error: Exception) -> bool:
    for cause in exception_chain(error):
        if isinstance(cause, HTTPError):
            return cause.code == 429 or 500 <= cause.code <= 599
        if isinstance(cause, (requests.Timeout, requests.ConnectionError, TimeoutError, SocketTimeout, gaierror)):
            return True
        if isinstance(cause, URLError):
            reason = cause.reason
            if isinstance(reason, (TimeoutError, SocketTimeout, gaierror, OSError)):
                return True
        if TRANSIENT_HTTP_STATUS_PATTERN.search(str(cause)):
            return True
    return False


def retry_delay(retry_number: int, base_delay: float) -> float:
    if retry_number < 1:
        raise PipelineError("Retry number must be greater than zero")
    if base_delay <= 0:
        raise PipelineError("Retry base delay must be greater than zero")
    return base_delay * (2 ** (retry_number - 1))


def selected_phases(start: str, end: str) -> tuple[str, ...]:
    if start not in PHASES or end not in PHASES:
        raise PipelineError("Unknown pipeline phase")
    start_index = PHASES.index(start)
    end_index = PHASES.index(end)
    if start_index > end_index:
        raise PipelineError("--from must not be after --through")
    return PHASES[start_index : end_index + 1]


def document_paths(document: dict[str, str], roots: dict[str, Path]) -> dict[str, Path]:
    document_id = document["document_id"]
    pdf_path = roots["raw"] / document_id / document["filename"]
    extracted_root = roots["extracted"] / document_id
    marker_dir = extracted_root / pdf_path.stem
    return {
        "pdf": pdf_path,
        "extracted_root": extracted_root,
        "marker_dir": marker_dir,
        "raw_markdown": marker_dir / f"{pdf_path.stem}.md",
        "ocr_manifest": roots["ocr"] / document_id / "candidates.json",
        "ocr_dir": roots["ocr"] / document_id,
        "processed_markdown": roots["processed"] / document_id / f"{pdf_path.stem}.md",
        "chunks": roots["chunks"] / document_id,
        "transformed": roots["transformed"] / document_id,
        "package": roots["packages"] / document_id,
    }


def _phase_input(document: dict[str, str], phase: str, paths: dict[str, Path]) -> str | None:
    if phase == "download":
        return fingerprint({"url": document["url"], "filename": document["filename"]})
    if phase == "extraction":
        return chunk_sha256_file(paths["pdf"])
    if phase == "ocr":
        return chunk_sha256_file(paths["raw_markdown"])
    if phase == "chunking":
        return chunk_sha256_file(paths["processed_markdown"])
    if phase == "transformation":
        return directory_sha256(paths["chunks"])
    return fingerprint(
        {
            "transformed_sha256": directory_sha256(paths["transformed"]),
            "document_metadata": {
                field: document[field] for field in ("title", "category", "filename", "url")
            },
        }
    )


def execute_phase(
    phase: str,
    document: dict[str, str],
    paths: dict[str, Path],
    catalog_path: Path,
    resume: bool,
    disable_marker_ocr: bool,
    stop_requested: Callable[[], bool] | None = None,
) -> tuple[str, str]:
    if phase == "download":
        destination = download_document(document, paths["pdf"].parent.parent, force=False)
        return "completed", chunk_sha256_file(destination)
    if phase == "extraction":
        extract_markdown(
            paths["pdf"],
            paths["extracted_root"],
            disable_ocr=disable_marker_ocr,
            stream_output=True,
        )
        return "completed", directory_sha256(paths["extracted_root"])
    if phase == "ocr":
        candidate_manifest = build_manifest(
            paths["pdf"],
            paths["marker_dir"],
            OCR_SELECTION["min_text_chars"],
            OCR_SELECTION["min_image_area"],
            set(OCR_SELECTION["image_kinds"]),
        )
        save_manifest(candidate_manifest, paths["ocr_manifest"], force=True)
        images = candidate_manifest["images"]
        if not images:
            raw_markdown = paths["raw_markdown"].read_text(encoding="utf-8")
            save_markdown(raw_markdown, paths["processed_markdown"], force=True)
            return "skipped", chunk_sha256_file(paths["processed_markdown"])
        client = CloudVisionClient(load_vision_config())
        run_batch(paths["ocr_manifest"], paths["ocr_dir"], client)
        markdown, _ = integrate_ocr(
            paths["raw_markdown"], paths["ocr_manifest"], paths["ocr_dir"]
        )
        save_markdown(markdown, paths["processed_markdown"], force=True)
        return "completed", chunk_sha256_file(paths["processed_markdown"])
    if phase == "chunking":
        chunks = create_chunks(paths["processed_markdown"])
        save_chunks(
            chunks,
            paths["processed_markdown"],
            paths["chunks"],
            CHUNK_MAX_ESTIMATED_TOKENS,
        )
        return "completed", directory_sha256(paths["chunks"])
    if phase == "transformation":
        transform_chunks(
            paths["chunks"],
            paths["transformed"],
            OpenAICompatibleClient(load_config()),
            resume=resume,
            stop_requested=stop_requested,
        )
        return "completed", directory_sha256(paths["transformed"])
    if phase == "packaging":
        build_package(
            paths["transformed"],
            paths["marker_dir"],
            catalog_path,
            document["document_id"],
            paths["package"],
        )
        return "completed", directory_sha256(paths["package"])
    raise PipelineError(f"Unknown pipeline phase: {phase}")


def skip_cloud_vision_ocr(paths: dict[str, Path]) -> tuple[str, str]:
    candidate_manifest = build_manifest(
        paths["pdf"],
        paths["marker_dir"],
        OCR_SELECTION["min_text_chars"],
        OCR_SELECTION["min_image_area"],
        set(OCR_SELECTION["image_kinds"]),
    )
    save_manifest(candidate_manifest, paths["ocr_manifest"], force=True)
    try:
        raw_markdown = paths["raw_markdown"].read_text(encoding="utf-8")
    except OSError as error:
        raise PipelineError(
            f"Could not read extracted Markdown for skipped OCR: {paths['raw_markdown']}"
        ) from error
    save_markdown(raw_markdown, paths["processed_markdown"], force=True)
    return "skipped", chunk_sha256_file(paths["processed_markdown"])


def run_pipeline(
    connection: sqlite3.Connection,
    catalog: dict[str, Any],
    catalog_path: Path,
    roots: dict[str, Path],
    phases: tuple[str, ...],
    document_ids: set[str] | None = None,
    resume: bool = False,
    disable_marker_ocr: bool = True,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_base_delay: float = DEFAULT_RETRY_BASE_DELAY,
    skip_cloud_vision: bool = True,
    sleeper: Callable[[float], None] = time.sleep,
    executor: Callable[
        [str, dict[str, str], dict[str, Path], Path, bool, bool], tuple[str, str]
    ] | None = None,
    stop_requested: Callable[[], bool] = lambda: False,
) -> dict[str, int]:
    if max_retries < 0:
        raise PipelineError("Maximum retries cannot be negative")
    if retry_base_delay <= 0:
        raise PipelineError("Retry base delay must be greater than zero")
    documents = [
        document
        for document in catalog["documents"]
        if document_ids is None or document["document_id"] in document_ids
    ]
    if document_ids is not None:
        unknown = document_ids - {document["document_id"] for document in documents}
        if unknown:
            raise PipelineError(f"Document IDs are not in the catalog: {', '.join(sorted(unknown))}")

    states = {
        (row["document_id"], row["phase"]): row
        for row in connection.execute("SELECT * FROM phase_states")
    }
    results = {"completed": 0, "skipped": 0, "failed": 0, "already_completed": 0}
    for document in documents:
        paths = document_paths(document, roots)
        for phase in phases:
            if stop_requested():
                raise PipelineGracefulStop("stop requested before starting the next phase")
            state = states.get((document["document_id"], phase))
            if state is None:
                raise PipelineError(
                    f"Document is not synchronized to state database: {document['document_id']}"
                )
            if state["status"] in {"completed", "skipped"}:
                results["already_completed"] += 1
                continue
            try:
                if state["status"] == "stale":
                    remove_phase_output(phase, paths)
                input_sha256 = _phase_input(document, phase, paths)
                current_phase_fingerprint = phase_fingerprint(
                    phase,
                    input_sha256,
                    disable_marker_ocr=disable_marker_ocr,
                    skip_cloud_vision=skip_cloud_vision,
                )
            except Exception as error:
                mark_phase_failed(connection, document["document_id"], phase, error)
                results["failed"] += 1
                break
            for retry_number in range(max_retries + 1):
                if stop_requested():
                    raise PipelineGracefulStop(
                        "stop requested before starting the next attempt"
                    )
                try:
                    print(
                        f"Running {document['document_id']} {phase} "
                        f"(attempt {retry_number + 1}/{max_retries + 1})",
                        flush=True,
                    )
                    mark_phase_running(connection, document["document_id"], phase)
                    if phase == "ocr" and skip_cloud_vision:
                        status, output_sha256 = skip_cloud_vision_ocr(paths)
                    else:
                        if executor is None:
                            status, output_sha256 = execute_phase(
                                phase,
                                document,
                                paths,
                                catalog_path,
                                resume,
                                disable_marker_ocr,
                                stop_requested,
                            )
                        else:
                            status, output_sha256 = executor(
                                phase,
                                document,
                                paths,
                                catalog_path,
                                resume,
                                disable_marker_ocr,
                            )
                    mark_phase_finished(
                        connection,
                        document["document_id"],
                        phase,
                        status,
                        input_sha256,
                        output_sha256,
                        current_phase_fingerprint,
                    )
                    results[status] += 1
                    print(
                        f"Finished {document['document_id']} {phase}: {status}",
                        flush=True,
                    )
                    if stop_requested():
                        raise PipelineGracefulStop(
                            f"stop requested after finishing {document['document_id']} {phase}"
                        )
                    break
                except Exception as error:
                    if isinstance(error, PipelineGracefulStop):
                        raise
                    if isinstance(error, GracefulStopRequested):
                        mark_phase_failed(
                            connection, document["document_id"], phase, error
                        )
                        print(
                            f"Stopped {document['document_id']} {phase} at a checkpoint",
                            flush=True,
                        )
                        raise PipelineGracefulStop(str(error)) from error
                    if isinstance(error, QuotaThresholdError):
                        mark_phase_failed(
                            connection, document["document_id"], phase, error
                        )
                        print(
                            f"Stopped {document['document_id']} {phase}: {error}",
                            flush=True,
                        )
                        raise PipelineQuotaStop(str(error)) from error
                    if retry_number < max_retries and is_transient_error(error):
                        delay = retry_delay(retry_number + 1, retry_base_delay)
                        print(
                            f"Retrying {document['document_id']} {phase} after "
                            f"{delay:g}s: {error}",
                            flush=True,
                        )
                        if stop_requested():
                            raise PipelineGracefulStop(
                                "stop requested before retry delay"
                            )
                        sleeper(delay)
                        continue
                    mark_phase_failed(connection, document["document_id"], phase, error)
                    results["failed"] += 1
                    print(
                        f"Failed {document['document_id']} {phase}: {error}",
                        flush=True,
                    )
                    break
            else:
                raise PipelineError("Retry loop exhausted unexpectedly")
            phase_state = connection.execute(
                "SELECT status FROM phase_states WHERE document_id = ? AND phase = ?",
                (document["document_id"], phase),
            ).fetchone()
            if phase_state is None or phase_state["status"] == "failed":
                break
    return results


def print_plan(summary: dict[str, Any], sync: dict[str, int]) -> None:
    print(f"Active documents: {summary['documents']}")
    print(
        "Catalog changes: "
        + ", ".join(f"{key}={value}" for key, value in sync.items())
    )
    print(
        "Estimated GPT transformation calls: "
        f"{summary['gpt_calls']['known']} known; "
        f"{summary['gpt_calls']['unknown_documents']} document(s) pending chunking"
    )
    for phase in PHASES:
        counts = summary["phase_counts"][phase]
        rendered = ", ".join(
            f"{status}={counts.get(status, 0)}" for status in STATUSES if counts.get(status, 0)
        ) or "none"
        print(f"{phase}: {rendered}")


def print_status(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No pipeline state found.")
        return
    for row in rows:
        suffix = f"; error={row['error']}" if row["error"] else ""
        print(
            f"{row['document_id']} {row['phase']} {row['status']} "
            f"attempts={row['attempts']}{suffix}"
        )


def product_roots(args: argparse.Namespace) -> dict[str, Path]:
    data_root = args.data_root
    return {
        name: getattr(args, f"{name}_root", None) or data_root / name
        for name in (
            "raw",
            "extracted",
            "ocr",
            "processed",
            "chunks",
            "transformed",
            "packages",
        )
    }


def status_database_path(args: argparse.Namespace) -> Path:
    if args.state_db is not None:
        return args.state_db
    return PRODUCTS_DATA_ROOT / args.product / "state.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage manual conversion pipeline state.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="Synchronize a catalog and show planned work.")
    plan.add_argument("--catalog", required=True, type=Path)
    plan.add_argument("--data-root", type=Path, default=DEFAULT_PRODUCT_DATA_ROOT)
    plan.add_argument("--state-db", type=Path)
    plan.add_argument("--chunks-root", type=Path)
    status = subparsers.add_parser("status", help="Show persisted document and phase state.")
    status_target = status.add_mutually_exclusive_group(required=True)
    status_target.add_argument(
        "--product",
        help="Product ID whose data/products/<product>/state.sqlite3 should be shown.",
    )
    status_target.add_argument("--state-db", type=Path)
    run = subparsers.add_parser("run", help="Run selected pipeline phases for catalog documents.")
    run.add_argument("--catalog", required=True, type=Path)
    run.add_argument("--data-root", type=Path, default=DEFAULT_PRODUCT_DATA_ROOT)
    run.add_argument("--state-db", type=Path)
    run.add_argument("--document-id", action="append", dest="document_ids")
    run.add_argument(
        "--pending-only",
        action="store_true",
        help="Run only documents that have pending phases; preserve completed documents.",
    )
    run.add_argument("--from", dest="start_phase", choices=PHASES, default=PHASES[0])
    run.add_argument("--through", dest="end_phase", choices=PHASES, default=PHASES[-1])
    run.add_argument("--resume", action="store_true")
    run_marker_ocr = run.add_mutually_exclusive_group()
    run_marker_ocr.add_argument(
        "--enable-marker-ocr",
        dest="disable_marker_ocr",
        action="store_false",
        help="Enable Marker OCR during extraction (disabled by default).",
    )
    run_marker_ocr.add_argument(
        "--disable-marker-ocr",
        dest="disable_marker_ocr",
        action="store_true",
        help="Disable Marker OCR during extraction (default).",
    )
    run_cloud_vision = run.add_mutually_exclusive_group()
    run_cloud_vision.add_argument(
        "--enable-cloud-vision",
        dest="skip_cloud_vision",
        action="store_false",
        help="Enable Cloud Vision OCR (disabled by default).",
    )
    run_cloud_vision.add_argument(
        "--skip-cloud-vision",
        dest="skip_cloud_vision",
        action="store_true",
        help="Do not call Cloud Vision; pass extracted Markdown onward (default).",
    )
    run.set_defaults(disable_marker_ocr=True, skip_cloud_vision=True)
    run.add_argument("--raw-root", type=Path)
    run.add_argument("--extracted-root", type=Path)
    run.add_argument("--ocr-root", type=Path)
    run.add_argument("--processed-root", type=Path)
    run.add_argument("--chunks-root", type=Path)
    run.add_argument("--transformed-root", type=Path)
    run.add_argument("--packages-root", type=Path)
    run.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    run.add_argument("--retry-base-delay", type=float, default=DEFAULT_RETRY_BASE_DELAY)
    run.add_argument(
        "--quota-stop-percent",
        type=float,
        default=95.0,
        help="Stop before the next LLM request when quota usage reaches this percent.",
    )
    retry = subparsers.add_parser(
        "retry", help="Retry failed documents while preserving completed phases."
    )
    retry.add_argument("--failed", action="store_true", required=True)
    retry.add_argument("--catalog", required=True, type=Path)
    retry.add_argument("--data-root", type=Path, default=DEFAULT_PRODUCT_DATA_ROOT)
    retry.add_argument("--state-db", type=Path)
    retry.add_argument("--resume", action="store_true")
    retry_marker_ocr = retry.add_mutually_exclusive_group()
    retry_marker_ocr.add_argument(
        "--enable-marker-ocr",
        dest="disable_marker_ocr",
        action="store_false",
        help="Enable Marker OCR during extraction (disabled by default).",
    )
    retry_marker_ocr.add_argument(
        "--disable-marker-ocr",
        dest="disable_marker_ocr",
        action="store_true",
        help="Disable Marker OCR during extraction (default).",
    )
    retry_cloud_vision = retry.add_mutually_exclusive_group()
    retry_cloud_vision.add_argument(
        "--enable-cloud-vision",
        dest="skip_cloud_vision",
        action="store_false",
        help="Enable Cloud Vision OCR (disabled by default).",
    )
    retry_cloud_vision.add_argument(
        "--skip-cloud-vision",
        dest="skip_cloud_vision",
        action="store_true",
        help="Do not call Cloud Vision; pass extracted Markdown onward (default).",
    )
    retry.set_defaults(disable_marker_ocr=True, skip_cloud_vision=True)
    retry.add_argument("--raw-root", type=Path)
    retry.add_argument("--extracted-root", type=Path)
    retry.add_argument("--ocr-root", type=Path)
    retry.add_argument("--processed-root", type=Path)
    retry.add_argument("--chunks-root", type=Path)
    retry.add_argument("--transformed-root", type=Path)
    retry.add_argument("--packages-root", type=Path)
    retry.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    retry.add_argument("--retry-base-delay", type=float, default=DEFAULT_RETRY_BASE_DELAY)
    retry.add_argument(
        "--quota-stop-percent",
        type=float,
        default=95.0,
        help="Stop before the next LLM request when quota usage reaches this percent.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    stop_event = threading.Event()
    previous_sigint_handler = signal.getsignal(signal.SIGINT)

    def request_graceful_stop(signum: int, frame: Any) -> None:
        del signum, frame
        if stop_event.is_set():
            print(
                "\nGraceful stop is already pending; waiting for a safe checkpoint.",
                file=sys.stderr,
                flush=True,
            )
            return
        stop_event.set()
        print(
            "\nCtrl-C received; stopping after the current checkpoint.",
            file=sys.stderr,
            flush=True,
        )

    signal.signal(signal.SIGINT, request_graceful_stop)
    try:
        if args.command == "status":
            state_db = status_database_path(args)
            roots = None
        else:
            roots = product_roots(args)
            state_db = args.state_db or args.data_root / "state.sqlite3"
        connection = connect(state_db)
        try:
            initialize_database(connection)
            if args.command == "plan":
                assert roots is not None
                sync = sync_catalog(connection, load_catalog(args.catalog))
                invalidate_changed_documents(
                    connection,
                    load_catalog(args.catalog),
                    roots,
                    validate_configuration=False,
                )
                print_plan(plan_summary(connection, roots["chunks"]), sync)
            elif args.command == "status":
                print_status(status_rows(connection))
            else:
                assert roots is not None
                if not 0 <= args.quota_stop_percent <= 100:
                    raise PipelineError(
                        "--quota-stop-percent must be from 0 to 100"
                    )
                os.environ["LLM_QUOTA_STOP_PERCENT"] = str(
                    args.quota_stop_percent
                )
                catalog = load_catalog(args.catalog)
                sync_catalog(connection, catalog)
                selected_document_ids = (
                    {
                        row["document_id"]
                        for row in connection.execute(
                            """
                            SELECT DISTINCT p.document_id FROM phase_states AS p
                            JOIN documents AS d USING (document_id)
                            WHERE p.status = 'failed' AND d.catalog_status = 'active'
                            """
                        )
                    }
                    if args.command == "retry"
                    else (
                        set(args.document_ids)
                        if args.document_ids
                        else {
                            row["document_id"]
                            for row in connection.execute(
                                """
                                SELECT DISTINCT p.document_id FROM phase_states AS p
                                JOIN documents AS d USING (document_id)
                                WHERE p.status = 'pending'
                                  AND d.catalog_status = 'active'
                                """
                            )
                        }
                        if args.pending_only
                        else None
                    )
                )
                invalidate_changed_documents(
                    connection,
                    catalog,
                    roots,
                    document_ids=selected_document_ids,
                    disable_marker_ocr=args.disable_marker_ocr,
                    skip_cloud_vision=args.skip_cloud_vision,
                )
                results = run_pipeline(
                    connection,
                    catalog,
                    args.catalog,
                    roots,
                    PHASES if args.command == "retry" else selected_phases(args.start_phase, args.end_phase),
                    selected_document_ids,
                    args.resume,
                    args.disable_marker_ocr,
                    args.max_retries,
                    args.retry_base_delay,
                    args.skip_cloud_vision,
                    stop_requested=stop_event.is_set,
                )
                print(
                    "Pipeline run: "
                    + ", ".join(f"{key}={value}" for key, value in results.items())
                )
        finally:
            connection.close()
    except PipelineGracefulStop as error:
        print(f"Pipeline stopped cleanly: {error}", file=sys.stderr)
        return 130
    except PipelineQuotaStop as error:
        print(f"Pipeline stopped for quota safety: {error}", file=sys.stderr)
        return 1
    except PipelineError as error:
        print(f"Pipeline state error: {error}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGINT, previous_sigint_handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
