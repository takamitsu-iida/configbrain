"""Run the requirement-to-report pipeline with persistent, resumable checkpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from configbrain.expand_context import ContextError, expand_context
from configbrain.generate_config import GenerationError, generate_config
from configbrain.index_manuals import IndexingError
from configbrain.report import ReportError, load_report
from configbrain.search_manuals import SearchError, search_manuals
from configbrain.select_manuals import (
    SelectionError, _write_output, load_cloud_config, select_manuals,
)


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


def _read_json(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_bytes())
    if not isinstance(result, dict):
        raise PipelineError(f"Expected a JSON object: {path}")
    return result


def _save_json(path: Path, value: dict[str, Any]) -> None:
    _write_output(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def run_pipeline(
    requirement: str,
    output_dir: Path,
    *,
    catalog_path: Path = Path("data/catalog/c9300-iosxe26.json"),
    database_path: Path = Path("data/qdrant/db"),
    okf_root: Path = Path("data/okf"),
    source_pdf_root: Path = Path("data/source_manuals"),
    limit: int = 20,
    resume: bool = False,
) -> Path:
    requirement = requirement.strip()
    if not requirement:
        raise PipelineError("Requirement must be nonempty.")
    if type(limit) is not int or limit < 1:
        raise PipelineError("Search limit must be a positive integer.")
    output_dir = output_dir.resolve()
    if any(output_dir.is_relative_to(root.resolve()) for root in (
        database_path, okf_root, source_pdf_root,
    )):
        raise PipelineError("Output directory must not be inside sources or the database.")
    paths = {stage: output_dir / filename for stage, filename in STAGES}
    state_path = output_dir / STATE_FILE
    reserved = [state_path, *paths.values()]
    for path in reserved:
        if path.is_symlink() or path.resolve() in (catalog_path.resolve(), Path(".env").resolve()):
            raise PipelineError(f"Output conflicts with an input or symbolic link: {path}")
    inputs = {
        "requirement": requirement,
        "catalog_path": str(catalog_path.resolve()), "catalog_sha256": _sha256(catalog_path),
        "database_path": str(database_path.resolve()), "okf_root": str(okf_root.resolve()),
        "source_pdf_root": str(source_pdf_root.resolve()), "limit": limit,
        "product": "catalyst9300", "release": "26.x",
    }
    if resume:
        if not state_path.is_file():
            raise PipelineError("No pipeline checkpoint found; omit --resume for a new run.")
        state = _read_json(state_path)
        if state.get("schema_version") != 1 or state.get("inputs") != inputs:
            raise PipelineError("Pipeline inputs changed; use a new output directory.")
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
        if "contexts" in completed:
            fresh = expand_context(paths["search"], catalog_path, okf_root, source_pdf_root)
            if _read_json(paths["contexts"]) != fresh:
                raise PipelineError("Saved contexts differ from current sources; use a new output directory.")
    else:
        if any(path.exists() for path in reserved):
            raise PipelineError("Pipeline outputs already exist; use --resume or a new output directory.")
        output_dir.mkdir(parents=True, exist_ok=True)
        state = {"schema_version": 1, "inputs": inputs, "completed": {}}
    config = None
    for index, (stage, filename) in enumerate(STAGES, 1):
        if stage in state["completed"]:
            print(f"[{index}/{len(STAGES)}] Reusing {filename}", file=sys.stderr, flush=True)
            continue
        state.update(status="running", current_stage=stage, error=None)
        _save_json(state_path, state)
        print(f"[{index}/{len(STAGES)}] Running {stage}", file=sys.stderr, flush=True)
        try:
            result = None
            if stage == "selection":
                config = config or load_cloud_config()
                result = select_manuals(requirement, catalog_path, "catalyst9300", "26.x", config)
            elif stage == "search":
                result = search_manuals(paths["selection"], catalog_path, database_path, limit)
            elif stage == "contexts":
                result = expand_context(paths["search"], catalog_path, okf_root, source_pdf_root)
            elif stage == "generation":
                config = config or load_cloud_config()
                result = generate_config(
                    paths["contexts"], paths["search"], catalog_path,
                    okf_root, source_pdf_root, config,
                )
            else:
                _write_output(paths[stage], load_report(paths["generation"]))
            if result is not None:
                _save_json(paths[stage], result)
            state["completed"][stage] = {"filename": filename, "sha256": _sha256(paths[stage])}
            _save_json(state_path, state)
        except (
            PipelineError, SelectionError, SearchError, ContextError, GenerationError,
            IndexingError, ReportError, OSError, RuntimeError, ValueError,
        ) as error:
            state.update(status="failed", error=str(error))
            _save_json(state_path, state)
            raise PipelineError(
                f"Stage {stage} failed: {error}. Completed stages are saved; rerun with --resume."
            ) from error
    state.update(status="completed", current_stage=None, error=None)
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
    parser.add_argument("--limit", type=int, default=20)
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
            limit=args.limit, resume=args.resume,
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
