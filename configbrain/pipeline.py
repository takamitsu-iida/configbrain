"""Run the requirement-to-report pipeline with persistent, resumable checkpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient, models

from configbrain.expand_context import CONTEXT_POLICY_VERSION, ContextError, expand_context
from configbrain.generate_config import (
    DEFAULT_GENERATION_ROUNDS, EVIDENCE_REGISTRY_VERSION, GenerationError,
    generate_config,
)
from configbrain.index_manuals import (
    EMBEDDING_MODEL, IndexingError, _active_collection, _dataset_fingerprint,
    load_validated_corpus,
)
from configbrain.report import (
    ReportError, load_report, render_pipeline_failure_report,
)
from configbrain.search_manuals import SearchError, search_manuals
from configbrain.select_manuals import (
    SelectionError, _write_output, load_catalog, load_cloud_config, select_manuals,
)
from configbrain.verify_pdf import PDF_VERIFICATION_VERSION


class PipelineError(RuntimeError):
    """Raised when a pipeline stage or checkpoint cannot be used."""


STAGES = (
    ("selection", "selection.json"),
    ("search", "search-results.json"),
    ("contexts", "contexts.json"),
    ("generation", "config-draft.json"),
    ("report", "report.md"),
)
STATE_FILE = "pipeline-state.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _selection_metadata_fingerprint(metadata_root: Path) -> str:
    if metadata_root.is_symlink():
        raise PipelineError(f"Selection metadata root must not be a symlink: {metadata_root}")
    digest = hashlib.sha256()
    if not metadata_root.exists():
        return digest.hexdigest()
    if not metadata_root.is_dir():
        raise PipelineError(f"Selection metadata root is not a directory: {metadata_root}")
    try:
        for path in sorted(metadata_root.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                raise PipelineError(f"Invalid selection metadata file: {path}")
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    except OSError as error:
        raise PipelineError(f"Could not read selection metadata in {metadata_root}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_bytes())
    if not isinstance(result, dict):
        raise PipelineError(f"Expected a JSON object: {path}")
    return result


def _save_json(path: Path, value: dict[str, Any]) -> None:
    _write_output(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _validate_pipeline_sources(
    catalog_path: Path, database_path: Path, okf_root: Path,
    source_pdf_root: Path, reference_root: Path,
) -> dict[str, Any]:
    catalog = load_catalog(catalog_path, "catalyst9300", "26.x")
    kinds = {entry.get("document_type", "configuration_guide") for entry in catalog.documents}
    missing = [
        label for kind, label in (
            ("configuration_guide", "コンフィグガイド"),
            ("command_reference", "コマンドリファレンス"),
        ) if kind not in kinds
    ]
    if missing:
        raise PipelineError(
            f"必須資料がカタログにありません: {'、'.join(missing)}。"
            "同じ製品・リリースのコンフィグガイドとコマンドリファレンスを両方登録してください。"
        )
    corpus = load_validated_corpus(catalog_path, okf_root, source_pdf_root, reference_root)
    generation = _dataset_fingerprint(corpus.guides, EMBEDDING_MODEL, corpus.references)
    if not (database_path / "meta.json").is_file():
        raise PipelineError(f"検索DBがありません: {database_path}。同じカタログでindex_manualsを実行してください。")
    client = QdrantClient(path=str(database_path))
    try:
        collection = _active_collection(client)
        if collection is None:
            raise PipelineError("検索索引が未登録です。同じカタログでindex_manualsを実行してください。")
        generation_filter = models.Filter(must=[models.FieldCondition(
            key="dataset_fingerprint", match=models.MatchValue(value=generation),
        )])
        total = client.count(collection, exact=True).count
        matching = client.count(collection, count_filter=generation_filter, exact=True).count
        if not total or matching != total:
            raise PipelineError(
                "検索索引が現在の資料・カタログ・索引形式と一致しません。"
                "同じカタログでindex_manualsを実行し、新しい出力先で検索からやり直してください。"
                "--resumeだけでは古い検索結果を再利用するため解消しません。"
            )
        for document in corpus.guides:
            if not client.count(collection, count_filter=models.Filter(must=[
                models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=document.document_id),
                ),
            ]), exact=True).count:
                raise PipelineError(f"ガイドの検索索引がありません: {document.document_id}")
    finally:
        client.close()
    return {
        "status": "completed", "configuration_guide_count": len(corpus.guides),
        "command_reference_count": len(corpus.references), "dataset_fingerprint": generation,
    }


def run_pipeline(
    requirement: str,
    output_dir: Path,
    *,
    catalog_path: Path = Path("data/catalog/c9300-iosxe26.json"),
    database_path: Path = Path("data/qdrant/db"),
    okf_root: Path = Path("data/okf"),
    source_pdf_root: Path = Path("data/source_manuals"),
    reference_root: Path = Path("data/command_references"),
    selection_metadata_root: Path = Path("data/selection_metadata"),
    limit: int = 20,
    generation_rounds: int = DEFAULT_GENERATION_ROUNDS,
    resume: bool = False,
) -> Path:
    requirement = requirement.strip()
    if not requirement:
        raise PipelineError("Requirement must be nonempty.")
    if type(limit) is not int or limit < 1:
        raise PipelineError("Search limit must be a positive integer.")
    if type(generation_rounds) is not int or generation_rounds < 1:
        raise PipelineError("Generation rounds must be a positive integer.")
    output_dir = output_dir.resolve()
    if any(output_dir.is_relative_to(root.resolve()) for root in (
        database_path, okf_root, source_pdf_root, reference_root, selection_metadata_root,
    )):
        raise PipelineError("Output directory must not be inside sources or the database.")
    paths = {stage: output_dir / filename for stage, filename in STAGES}
    state_path = output_dir / STATE_FILE
    reserved = [state_path, *paths.values()]
    for path in reserved:
        if path.is_symlink() or path.resolve() in (catalog_path.resolve(), Path(".env").resolve()):
            raise PipelineError(f"Output conflicts with an input or symbolic link: {path}")
    catalog_error = None
    try:
        catalog_sha256 = _sha256(catalog_path)
    except OSError as error:
        catalog_sha256 = None
        catalog_error = error
    metadata_sha256 = _selection_metadata_fingerprint(selection_metadata_root)
    inputs = {
        "requirement": requirement,
        "catalog_path": str(catalog_path.resolve()), "catalog_sha256": catalog_sha256,
        "database_path": str(database_path.resolve()), "okf_root": str(okf_root.resolve()),
        "source_pdf_root": str(source_pdf_root.resolve()), "limit": limit,
        "reference_root": str(reference_root.resolve()),
        "selection_metadata_root": str(selection_metadata_root.resolve()),
        "selection_metadata_sha256": metadata_sha256,
        "product": "catalyst9300", "release": "26.x",
    }
    if resume:
        if not state_path.is_file():
            raise PipelineError("No pipeline checkpoint found; omit --resume for a new run.")
        state = _read_json(state_path)
        saved_inputs = state.get("inputs")
        if isinstance(saved_inputs, dict):
            saved_inputs = {
                "reference_root": str(Path("data/command_references").resolve()),
                **saved_inputs,
            }
        if state.get("schema_version") != 1 or saved_inputs != inputs:
            raise PipelineError("Pipeline inputs changed; use a new output directory.")
        state["inputs"] = inputs
        if (
            "generation_rounds" in state
            and state["generation_rounds"] != generation_rounds
        ):
            raise PipelineError("Generation rounds changed; use a new output directory.")
        state["generation_rounds"] = generation_rounds
        completed = state.get("completed")
        if not isinstance(completed, dict):
            raise PipelineError("Invalid completed-stage checkpoint.")
        missing = False
        for stage, filename in STAGES:
            if stage not in completed:
                missing = True
                continue
            if missing or not isinstance(completed[stage], dict):
                raise PipelineError("Completed stages must form a consecutive prefix.")
            if (
                completed[stage].get("filename") != filename
                or not paths[stage].is_file()
                or completed[stage].get("sha256") != _sha256(paths[stage])
            ):
                raise PipelineError(f"Saved {stage} output changed or is missing; use a new output directory.")
        if set(completed) - {stage for stage, _ in STAGES}:
            raise PipelineError("Checkpoint contains unknown completed stages.")
    else:
        if any(path.exists() for path in reserved):
            raise PipelineError("Pipeline outputs already exist; use --resume or a new output directory.")
        output_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "schema_version": 1, "inputs": inputs, "completed": {},
            "generation_rounds": generation_rounds,
            "stage_statuses": {stage: "not_started" for stage, _ in STAGES},
        }
    if "stage_statuses" not in state:
        completed_stages = state["completed"]
        statuses = {
            stage: "completed" if stage in completed_stages else "not_started"
            for stage, _ in STAGES
        }
        if state.get("status") == "failed" and state.get("current_stage") in statuses:
            statuses[state["current_stage"]] = "failed"
        if (
            state.get("status") == "completed_with_warnings"
            and "generation" in completed_stages
        ):
            statuses["generation"] = "completed_with_warnings"
        state["stage_statuses"] = statuses

    if resume and "contexts" in state["completed"]:
        saved_contexts = _read_json(paths["contexts"])
        if saved_contexts.get("context_policy_version") != CONTEXT_POLICY_VERSION:
            raise PipelineError(
                "Context policy changed; use a new output directory to regenerate "
                "contexts and later stages without overwriting previous outputs."
            )

    if resume and "generation" in state["completed"]:
        saved_generation = _read_json(paths["generation"])
        if (
            saved_generation.get("evidence_registry_version") != EVIDENCE_REGISTRY_VERSION
            or type(saved_generation.get("pdf_verification_version")) is not int
            or saved_generation["pdf_verification_version"] != PDF_VERIFICATION_VERSION
        ):
            for stage in ("generation", "report"):
                state["completed"].pop(stage, None)
                state["stage_statuses"][stage] = "not_started"

    def fail_stage(stage: str, error: Exception) -> PipelineError:
        if stage == "preflight":
            state["preflight"] = {"status": "failed", "error": str(error)}
            state["completed"].pop("report", None)
            state["stage_statuses"]["report"] = "not_started"
        else:
            failed_index = next(
                index for index, (candidate, _) in enumerate(STAGES) if candidate == stage
            )
            for candidate, _ in STAGES[failed_index:]:
                state["completed"].pop(candidate, None)
                state["stage_statuses"][candidate] = "not_started"
            state["stage_statuses"][stage] = "failed"
        state.update(status="failed", current_stage=stage, error=str(error))
        _save_json(state_path, state)
        try:
            _write_output(
                paths["report"],
                render_pipeline_failure_report(
                    requirement, state["stage_statuses"],
                    f"資料・索引の事前確認に失敗しました: {error}" if stage == "preflight" else str(error),
                ),
            )
        except (SelectionError, OSError) as report_error:
            state["diagnostic_report_error"] = str(report_error)
            _save_json(state_path, state)
            return PipelineError(
                f"Stage {stage} failed: {error}. "
                f"Diagnostic report could not be written: {report_error}"
            )
        recovery = (
            "Check both source types and the index; use a new output directory if inputs or the index changed."
            if stage == "preflight" else "rerun with --resume."
        )
        return PipelineError(
            f"Stage {stage} failed: {error}. "
            f"Diagnostic report written to {paths['report']}; {recovery}"
        )

    try:
        print("[preflight] Checking guides, references and index", file=sys.stderr, flush=True)
        if catalog_error is not None:
            raise PipelineError(f"カタログを読み取れません: {catalog_path}: {catalog_error}") from catalog_error
        state["preflight"] = _validate_pipeline_sources(
            catalog_path, database_path, okf_root, source_pdf_root, reference_root,
        )
        _save_json(state_path, state)
    except (PipelineError, SelectionError, IndexingError, OSError, RuntimeError, ValueError) as error:
        raise fail_stage("preflight", error) from error

    if resume and "contexts" in state["completed"]:
        try:
            fresh = expand_context(
                paths["search"], catalog_path, okf_root, source_pdf_root, reference_root
            )
            if _read_json(paths["contexts"]) != fresh:
                raise PipelineError(
                    "Saved contexts differ from current sources; rerun contexts."
                )
        except (
            PipelineError, ContextError, SearchError, IndexingError, OSError, ValueError,
        ) as error:
            raise fail_stage("contexts", error) from error

    config = None
    for index, (stage, filename) in enumerate(STAGES, 1):
        if stage in state["completed"]:
            state["stage_statuses"].setdefault(stage, "completed")
            print(f"[{index}/{len(STAGES)}] Reusing {filename}", file=sys.stderr, flush=True)
            continue
        state["stage_statuses"][stage] = "running"
        state.update(status="running", current_stage=stage, error=None)
        _save_json(state_path, state)
        print(f"[{index}/{len(STAGES)}] Running {stage}", file=sys.stderr, flush=True)
        try:
            result = None
            if stage == "selection":
                config = config or load_cloud_config()
                result = select_manuals(
                    requirement, catalog_path, "catalyst9300", "26.x", config,
                    okf_root=okf_root, metadata_root=selection_metadata_root,
                )
            elif stage == "search":
                result = search_manuals(
                    paths["selection"], catalog_path, database_path, limit,
                    reference_root=reference_root,
                )
            elif stage == "contexts":
                result = expand_context(
                    paths["search"], catalog_path, okf_root, source_pdf_root, reference_root
                )
            elif stage == "generation":
                config = config or load_cloud_config()
                result = generate_config(
                    paths["contexts"], paths["search"], catalog_path,
                    okf_root, source_pdf_root, config,
                    generation_rounds=generation_rounds,
                    reference_root=reference_root,
                )
            else:
                report_stages = dict(state["stage_statuses"])
                report_stages["report"] = "completed"
                _write_output(
                    paths[stage],
                    load_report(paths["generation"], pipeline_stages=report_stages),
                )
            if result is not None:
                _save_json(paths[stage], result)
            state["completed"][stage] = {"filename": filename, "sha256": _sha256(paths[stage])}
            stage_has_warnings = (
                stage == "selection" and bool(result.get("selection_metadata_warnings"))
            ) or (
                stage == "generation" and (
                    result.get("generation_status") == "partial"
                    or result.get("pdf_verification_status") != "verified"
                    or any(item.get("semantic_review_pending") for item in result.get("items", []))
                )
            )
            state["stage_statuses"][stage] = (
                "completed_with_warnings" if stage_has_warnings else "completed"
            )
            _save_json(state_path, state)
        except (
            PipelineError, SelectionError, SearchError, ContextError, GenerationError,
            IndexingError, ReportError, OSError, RuntimeError, ValueError,
        ) as error:
            raise fail_stage(stage, error) from error
    generation = _read_json(paths["generation"])
    warnings = [
        {"item_id": item["item_id"], "error": item["generation_error"]}
        for item in generation.get("items", [])
        if isinstance(item, dict) and item.get("generation_error")
    ]
    warnings.extend(
        {"stage": "selection", "error": warning}
        for warning in _read_json(paths["selection"]).get("selection_metadata_warnings", [])
    )
    warnings.extend(
        {"item_id": item["item_id"], "error": item["reference_generation_error"]}
        for item in generation.get("items", [])
        if isinstance(item, dict) and item.get("reference_generation_error")
    )
    warnings.extend(
        {
            "item_id": item["item_id"],
            "error": (
                item.get("semantic_review_error")
                or "Some source-supported explanations have unconfirmed applicability."
            ),
        }
        for item in generation.get("items", [])
        if isinstance(item, dict) and (
            item.get("semantic_review_error") or item.get("semantic_review_pending")
        )
    )
    warnings.extend(
        {
            "item_id": warning["item_id"],
            "related_item_id": warning["related_item_id"],
            "error": warning["message"],
        }
        for warning in generation.get("consistency_warnings", [])
    )
    if generation.get("pdf_verification_status") != "verified":
        warnings.append({
            "pdf_verification_status": generation.get("pdf_verification_status"),
            "error": "One or more citations are not uniquely matched to the original PDF.",
        })
    state.update(
        status="completed_with_warnings" if warnings else "completed",
        current_stage=None, error=None,
    )
    if warnings:
        state["warnings"] = warnings
    else:
        state.pop("warnings", None)
    _save_json(state_path, state)
    return paths["report"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--requirement")
    source.add_argument("--requirement-file", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog/c9300-iosxe26.json"))
    parser.add_argument("--database", type=Path, default=Path("data/qdrant/db"))
    parser.add_argument("--okf-root", type=Path, default=Path("data/okf"))
    parser.add_argument("--source-pdf-root", type=Path, default=Path("data/source_manuals"))
    parser.add_argument("--reference-root", type=Path, default=Path("data/command_references"))
    parser.add_argument(
        "--selection-metadata-root", type=Path, default=Path("data/selection_metadata")
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--generation-rounds", type=int, default=DEFAULT_GENERATION_ROUNDS,
        help="Maximum rounds per CLI generation, reference explanation and semantic review (default: 3).",
    )
    args = parser.parse_args(argv)
    try:
        if args.requirement_file:
            reserved = [STATE_FILE, *(filename for _, filename in STAGES)]
            if args.requirement_file.resolve() in {
                (args.output_dir / name).resolve() for name in reserved
            }:
                raise PipelineError("Requirement file must not be a pipeline output.")
            requirement = args.requirement_file.read_text(encoding="utf-8")
        else:
            requirement = args.requirement
        report = run_pipeline(
            requirement, args.output_dir, catalog_path=args.catalog, database_path=args.database,
            okf_root=args.okf_root, source_pdf_root=args.source_pdf_root,
            reference_root=args.reference_root,
            selection_metadata_root=args.selection_metadata_root,
            limit=args.limit, generation_rounds=args.generation_rounds,
            resume=args.resume,
        )
        print(f"Report written: {report}")
    except (
        PipelineError, SelectionError, SearchError, ContextError, GenerationError,
        IndexingError, ReportError, OSError, RuntimeError, ValueError,
    ) as error:
        print(f"Pipeline failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
