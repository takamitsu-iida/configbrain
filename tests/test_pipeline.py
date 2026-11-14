import json
from pathlib import Path

import pytest
import requests

from scripts.pipeline import (
    PHASES,
    PipelineGracefulStop,
    PipelineQuotaStop,
    build_parser,
    connect,
    fingerprint,
    invalidate_changed_documents,
    initialize_database,
    load_catalog,
    is_transient_error,
    phase_configuration,
    plan_summary,
    run_pipeline,
    selected_phases,
    product_roots,
    status_database_path,
    status_rows,
    sync_catalog,
)
from scripts.transform_markdown import GracefulStopRequested, QuotaThresholdError


def write_catalog(path: Path, documents: list[dict[str, str]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "product": "catalyst9300",
                "release": "26.x",
                "seed_url": "https://www.cisco.com/release",
                "documents": documents,
            }
        ),
        encoding="utf-8",
    )
    return path


def document(document_id: str, title: str = "Guide") -> dict[str, str]:
    return {
        "document_id": document_id,
        "title": title,
        "category": "Configuration Guides",
        "filename": f"{document_id}.pdf",
        "url": f"https://www.cisco.com/release/26-x/{document_id}.pdf",
    }


def test_sync_catalog_creates_document_and_all_pending_phases(tmp_path: Path) -> None:
    connection = connect(tmp_path / "state.sqlite3")
    initialize_database(connection)
    catalog_path = write_catalog(tmp_path / "catalog.json", [document("guide-a")])

    changes = sync_catalog(connection, load_catalog(catalog_path))
    rows = status_rows(connection)

    assert changes == {"new": 1, "updated": 0, "reactivated": 0, "removed": 0}
    assert [(row["phase"], row["status"]) for row in rows] == [
        (phase, "pending") for phase in PHASES
    ]


def test_sync_catalog_keeps_pending_changed_documents_pending_and_skips_removed_documents(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "state.sqlite3")
    initialize_database(connection)
    catalog_path = write_catalog(
        tmp_path / "catalog.json", [document("changed"), document("removed")]
    )
    sync_catalog(connection, load_catalog(catalog_path))

    catalog_path = write_catalog(tmp_path / "catalog.json", [document("changed", "New title")])
    changes = sync_catalog(connection, load_catalog(catalog_path))
    rows = status_rows(connection)

    assert changes == {"new": 0, "updated": 1, "reactivated": 0, "removed": 1}
    assert {row["status"] for row in rows if row["document_id"] == "changed"} == {
        "pending"
    }
    assert {row["status"] for row in rows if row["document_id"] == "removed"} == {"skipped"}
    assert {row["catalog_status"] for row in rows if row["document_id"] == "removed"} == {
        "removed"
    }


def test_plan_summary_counts_existing_chunks_as_known_gpt_calls(tmp_path: Path) -> None:
    connection = connect(tmp_path / "state.sqlite3")
    initialize_database(connection)
    catalog_path = write_catalog(
        tmp_path / "catalog.json", [document("chunked"), document("not-chunked")]
    )
    sync_catalog(connection, load_catalog(catalog_path))
    chunks_root = tmp_path / "chunks"
    manifest_path = chunks_root / "chunked" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({"chunks": [{}, {}, {}]}), encoding="utf-8")

    summary = plan_summary(connection, chunks_root)

    assert summary["documents"] == 2
    assert summary["gpt_calls"] == {"known": 3, "unknown_documents": 1}
    assert summary["phase_counts"]["download"] == {"pending": 2}


def test_run_pipeline_isolates_failed_document_and_skips_completed_phase(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "state.sqlite3")
    initialize_database(connection)
    catalog_path = write_catalog(
        tmp_path / "catalog.json", [document("fails"), document("succeeds")]
    )
    catalog = load_catalog(catalog_path)
    sync_catalog(connection, catalog)
    calls: list[str] = []

    def executor(
        phase: str,
        catalog_document: dict[str, str],
        paths: dict[str, Path],
        catalog_input: Path,
        resume: bool,
        disable_marker_ocr: bool,
    ) -> tuple[str, str]:
        calls.append(catalog_document["document_id"])
        if catalog_document["document_id"] == "fails":
            raise RuntimeError("download unavailable")
        return "completed", "output-checksum"

    roots = {
        name: tmp_path / name
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
    results = run_pipeline(
        connection,
        catalog,
        catalog_path,
        roots,
        ("download",),
        executor=executor,
    )

    rows = status_rows(connection)
    assert results == {
        "completed": 1,
        "skipped": 0,
        "failed": 1,
        "already_completed": 0,
    }
    assert calls == ["fails", "succeeds"]
    assert next(
        row for row in rows if row["document_id"] == "fails" and row["phase"] == "download"
    )["status"] == "failed"
    assert next(
        row
        for row in rows
        if row["document_id"] == "succeeds" and row["phase"] == "download"
    )["status"] == "completed"

    calls.clear()
    rerun = run_pipeline(
        connection,
        catalog,
        catalog_path,
        roots,
        ("download",),
        document_ids={"succeeds"},
        executor=executor,
    )
    assert rerun == {
        "completed": 0,
        "skipped": 0,
        "failed": 0,
        "already_completed": 1,
    }
    assert calls == []


def test_selected_phases_validates_pipeline_order() -> None:
    assert selected_phases("ocr", "transformation") == (
        "ocr",
        "chunking",
        "transformation",
    )


def test_pipeline_ocr_defaults_are_disabled_and_can_be_enabled() -> None:
    parser = build_parser()

    run_defaults = parser.parse_args(["run", "--catalog", "catalog.json"])
    retry_defaults = parser.parse_args(
        ["retry", "--failed", "--catalog", "catalog.json"]
    )
    run_enabled = parser.parse_args(
        [
            "run",
            "--catalog",
            "catalog.json",
            "--enable-marker-ocr",
            "--enable-cloud-vision",
        ]
    )

    assert run_defaults.disable_marker_ocr is True
    assert run_defaults.skip_cloud_vision is True
    assert retry_defaults.disable_marker_ocr is True
    assert retry_defaults.skip_cloud_vision is True
    assert run_enabled.disable_marker_ocr is False
    assert run_enabled.skip_cloud_vision is False
    pending_only = parser.parse_args(
        ["run", "--catalog", "catalog.json", "--pending-only"]
    )
    assert pending_only.pending_only is True


def test_product_data_root_sets_default_phase_paths() -> None:
    args = build_parser().parse_args(
        [
            "run",
            "--catalog",
            "catalog.json",
            "--data-root",
            "data/products/catalyst9500",
        ]
    )

    assert product_roots(args) == {
        "raw": Path("data/products/catalyst9500/raw"),
        "extracted": Path("data/products/catalyst9500/extracted"),
        "ocr": Path("data/products/catalyst9500/ocr"),
        "processed": Path("data/products/catalyst9500/processed"),
        "chunks": Path("data/products/catalyst9500/chunks"),
        "transformed": Path("data/products/catalyst9500/transformed"),
        "packages": Path("data/products/catalyst9500/packages"),
    }


def test_status_requires_product_or_state_database() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["status"])
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["status", "--product", "catalyst9200", "--state-db", "state.sqlite3"]
        )


def test_status_resolves_product_or_explicit_state_database() -> None:
    parser = build_parser()

    product_args = parser.parse_args(["status", "--product", "catalyst9200"])
    database_args = parser.parse_args(
        ["status", "--state-db", "data/trials/test/state.sqlite3"]
    )

    assert status_database_path(product_args) == Path(
        "data/products/catalyst9200/state.sqlite3"
    )
    assert status_database_path(database_args) == Path(
        "data/trials/test/state.sqlite3"
    )


def test_phase_configuration_tracks_ocr_enablement() -> None:
    assert phase_configuration("extraction")["marker_ocr_disabled"] is True
    assert phase_configuration("ocr")["skip_cloud_vision"] is True
    assert (
        phase_configuration("extraction", disable_marker_ocr=False)[
            "marker_ocr_disabled"
        ]
        is False
    )
    assert phase_configuration("ocr", skip_cloud_vision=False)["skip_cloud_vision"] is False


def test_invalidation_marks_only_extraction_and_downstream_when_pdf_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = connect(tmp_path / "state.sqlite3")
    initialize_database(connection)
    catalog_path = write_catalog(tmp_path / "catalog.json", [document("guide")])
    catalog = load_catalog(catalog_path)
    sync_catalog(connection, catalog)
    roots = {
        name: tmp_path / name
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
    inputs = {phase: f"input-{phase}" for phase in PHASES}
    outputs = {phase: f"output-{phase}" for phase in PHASES}

    monkeypatch.setattr(
        "scripts.pipeline._phase_input",
        lambda catalog_document, phase, paths: inputs[phase],
    )
    monkeypatch.setattr(
        "scripts.pipeline.output_sha256",
        lambda phase, paths: outputs[phase],
    )
    for phase in PHASES:
        connection.execute(
            """
            UPDATE phase_states
            SET status = 'completed', input_sha256 = ?, output_sha256 = ?, fingerprint = ?
            WHERE document_id = 'guide' AND phase = ?
            """,
            (
                inputs[phase],
                outputs[phase],
                fingerprint(
                    {
                        "phase": phase,
                        "input_sha256": inputs[phase],
                        "configuration": phase_configuration(phase),
                    }
                ),
                phase,
            ),
        )
    connection.commit()
    inputs["extraction"] = "changed-pdf-checksum"

    invalidated = invalidate_changed_documents(connection, catalog, roots)
    rows = status_rows(connection)

    assert invalidated == {
        "download": 0,
        "extraction": 1,
        "ocr": 0,
        "chunking": 0,
        "transformation": 0,
        "packaging": 0,
    }
    assert {
        row["phase"]: row["status"] for row in rows
    } == {
        "download": "completed",
        "extraction": "stale",
        "ocr": "stale",
        "chunking": "stale",
        "transformation": "stale",
        "packaging": "stale",
    }


def test_invalidation_only_checks_selected_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = connect(tmp_path / "state.sqlite3")
    initialize_database(connection)
    catalog_path = write_catalog(
        tmp_path / "catalog.json", [document("selected"), document("other")]
    )
    catalog = load_catalog(catalog_path)
    sync_catalog(connection, catalog)
    roots = {
        name: tmp_path / name
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
    monkeypatch.setattr("scripts.pipeline._phase_input", lambda *args: "changed")
    monkeypatch.setattr("scripts.pipeline.output_sha256", lambda *args: "changed")
    connection.execute(
        "UPDATE phase_states SET status = 'completed' WHERE phase = 'download'"
    )
    connection.commit()

    invalidate_changed_documents(
        connection, catalog, roots, document_ids={"selected"}
    )
    rows = status_rows(connection)

    selected = {
        row["phase"]: row["status"]
        for row in rows
        if row["document_id"] == "selected"
    }
    other = {
        row["phase"]: row["status"]
        for row in rows
        if row["document_id"] == "other"
    }
    assert selected["download"] == "stale"
    assert other["download"] == "completed"


def test_invalidation_can_preserve_stored_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = connect(tmp_path / "state.sqlite3")
    initialize_database(connection)
    catalog_path = write_catalog(tmp_path / "catalog.json", [document("guide")])
    catalog = load_catalog(catalog_path)
    sync_catalog(connection, catalog)
    roots = {
        name: tmp_path / name
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
    monkeypatch.setattr("scripts.pipeline._phase_input", lambda *args: "input")
    monkeypatch.setattr("scripts.pipeline.output_sha256", lambda *args: "output")
    connection.execute(
        """
        UPDATE phase_states
        SET status = 'completed', input_sha256 = 'input',
            output_sha256 = 'output', fingerprint = 'stored'
        WHERE phase = 'ocr'
        """
    )
    connection.commit()

    invalidated = invalidate_changed_documents(
        connection, catalog, roots, validate_configuration=False
    )

    assert invalidated["ocr"] == 0
    row = next(row for row in status_rows(connection) if row["phase"] == "ocr")
    assert row["status"] == "completed"


def test_run_pipeline_retries_only_transient_errors_with_exponential_backoff(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "state.sqlite3")
    initialize_database(connection)
    catalog_path = write_catalog(tmp_path / "catalog.json", [document("guide")])
    catalog = load_catalog(catalog_path)
    sync_catalog(connection, catalog)
    roots = {
        name: tmp_path / name
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
    calls = 0
    delays: list[float] = []

    def transient_executor(*args: object) -> tuple[str, str]:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise requests.ConnectionError("connection reset")
        return "completed", "output-checksum"

    results = run_pipeline(
        connection,
        catalog,
        catalog_path,
        roots,
        ("download",),
        max_retries=3,
        retry_base_delay=0.25,
        sleeper=delays.append,
        executor=transient_executor,  # type: ignore[arg-type]
    )

    row = next(row for row in status_rows(connection) if row["phase"] == "download")
    assert results == {
        "completed": 1,
        "skipped": 0,
        "failed": 0,
        "already_completed": 0,
    }
    assert calls == 3
    assert delays == [0.25, 0.5]
    assert row["attempts"] == 3
    assert row["status"] == "completed"


def test_run_pipeline_does_not_retry_validation_errors(tmp_path: Path) -> None:
    connection = connect(tmp_path / "state.sqlite3")
    initialize_database(connection)
    catalog_path = write_catalog(tmp_path / "catalog.json", [document("guide")])
    catalog = load_catalog(catalog_path)
    sync_catalog(connection, catalog)
    roots = {
        name: tmp_path / name
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
    calls = 0

    def validation_executor(*args: object) -> tuple[str, str]:
        nonlocal calls
        calls += 1
        raise ValueError("checksum mismatch")

    results = run_pipeline(
        connection,
        catalog,
        catalog_path,
        roots,
        ("download",),
        max_retries=3,
        sleeper=lambda delay: pytest.fail(f"unexpected retry delay: {delay}"),
        executor=validation_executor,  # type: ignore[arg-type]
    )

    row = next(row for row in status_rows(connection) if row["phase"] == "download")
    assert results["failed"] == 1
    assert calls == 1
    assert row["attempts"] == 1
    assert row["status"] == "failed"
    assert is_transient_error(requests.ConnectionError("timeout"))
    assert not is_transient_error(ValueError("invalid JSON"))


def test_run_pipeline_stops_all_documents_at_quota_threshold(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "state.sqlite3")
    initialize_database(connection)
    catalog_path = write_catalog(
        tmp_path / "catalog.json", [document("first"), document("second")]
    )
    catalog = load_catalog(catalog_path)
    sync_catalog(connection, catalog)
    roots = {
        name: tmp_path / name
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
    calls: list[str] = []

    def quota_executor(
        phase: str,
        catalog_document: dict[str, str],
        paths: dict[str, Path],
        catalog_input: Path,
        resume: bool,
        disable_marker_ocr: bool,
    ) -> tuple[str, str]:
        calls.append(catalog_document["document_id"])
        raise QuotaThresholdError("quota reached")

    with pytest.raises(PipelineQuotaStop, match="quota reached"):
        run_pipeline(
            connection,
            catalog,
            catalog_path,
            roots,
            ("download",),
            executor=quota_executor,
        )

    assert calls == ["first"]
    first = next(
        row
        for row in status_rows(connection)
        if row["document_id"] == "first" and row["phase"] == "download"
    )
    second = next(
        row
        for row in status_rows(connection)
        if row["document_id"] == "second" and row["phase"] == "download"
    )
    assert first["status"] == "failed"
    assert second["status"] == "pending"


def test_run_pipeline_records_checkpointed_interrupt_as_resumable_failure(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "state.sqlite3")
    initialize_database(connection)
    catalog_path = write_catalog(tmp_path / "catalog.json", [document("guide")])
    catalog = load_catalog(catalog_path)
    sync_catalog(connection, catalog)
    roots = {
        name: tmp_path / name
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

    def interrupted_executor(*args: object) -> tuple[str, str]:
        raise GracefulStopRequested("checkpoint saved")

    with pytest.raises(PipelineGracefulStop, match="checkpoint saved"):
        run_pipeline(
            connection,
            catalog,
            catalog_path,
            roots,
            ("download",),
            executor=interrupted_executor,  # type: ignore[arg-type]
        )

    row = next(row for row in status_rows(connection) if row["phase"] == "download")
    assert row["status"] == "failed"
    assert row["error"] == "checkpoint saved"
