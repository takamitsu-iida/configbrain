import copy
import json
from pathlib import Path

import pytest

from configbrain import pipeline
from configbrain.generate_config import GenerationError
from configbrain.report import ReportError
from configbrain.select_manuals import CloudConfig


@pytest.fixture
def pipeline_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report_draft: dict) -> dict:
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"test_catalog": true}\n')
    case = {
        "catalog_path": catalog, "output_dir": tmp_path / "run",
        "calls": [], "fail_generation": False, "corpus": "original",
    }

    def select(requirement, catalog_path, product, release, config):
        case["calls"].append("selection")
        assert catalog_path == catalog
        assert (product, release) == ("catalyst9300", "26.x")
        return {"requirement": requirement, "catalog": "test"}

    def search(selection_path, catalog_path, database_path, limit):
        case["calls"].append("search")
        selection = json.loads(selection_path.read_bytes())
        assert selection["catalog"] == "test"
        return {"requirement": selection["requirement"], "limit": limit}

    def expand(search_path, catalog_path, okf_root, source_pdf_root):
        case["calls"].append("contexts")
        search = json.loads(search_path.read_bytes())
        return {"requirement": search["requirement"], "corpus": case["corpus"]}

    def generate(contexts_path, search_path, catalog_path, okf_root, source_pdf_root, config):
        case["calls"].append("generation")
        if case["fail_generation"]:
            raise GenerationError("Draft remains invalid after one repair.")
        contexts = json.loads(contexts_path.read_bytes())
        assert contexts["corpus"] == case["corpus"]
        draft = copy.deepcopy(report_draft)
        draft["requirement"] = contexts["requirement"]
        return draft

    monkeypatch.setattr(pipeline, "select_manuals", select)
    monkeypatch.setattr(pipeline, "search_manuals", search)
    monkeypatch.setattr(pipeline, "expand_context", expand)
    monkeypatch.setattr(pipeline, "generate_config", generate)
    monkeypatch.setattr(
        pipeline, "load_cloud_config",
        lambda: CloudConfig("https://example.test", "test-key", None),
    )
    return case


def run(case: dict, **kwargs) -> Path:
    return pipeline.run_pipeline(
        "VLAN 120を作成してポートに割り当てる",
        case["output_dir"], catalog_path=case["catalog_path"], **kwargs,
    )


def test_pipeline_writes_all_stages_and_report(pipeline_case: dict) -> None:
    case = pipeline_case
    report = run(case)
    assert case["calls"] == ["selection", "search", "contexts", "generation"]
    assert report == case["output_dir"] / "report.md"
    assert "interface <INTERFACE_ID>" in report.read_text()
    state = json.loads((case["output_dir"] / pipeline.STATE_FILE).read_bytes())
    assert state["status"] == "completed" and state["current_stage"] is None
    assert list(state["completed"]) == [stage for stage, _ in pipeline.STAGES]
    for stage, filename in pipeline.STAGES:
        assert state["completed"][stage]["sha256"] == pipeline._sha256(case["output_dir"] / filename)
    assert "test-key" not in json.dumps(state)


def test_generation_failure_resumes_without_reselecting_or_searching(pipeline_case: dict) -> None:
    case = pipeline_case
    case["fail_generation"] = True
    with pytest.raises(pipeline.PipelineError, match="generation.*--resume"):
        run(case)
    output = case["output_dir"]
    state = json.loads((output / pipeline.STATE_FILE).read_bytes())
    assert state["status"] == "failed"
    assert list(state["completed"]) == ["selection", "search", "contexts"]
    saved = {name: (output / name).read_bytes() for name in (
        "selection.json", "search-results.json", "contexts.json",
    )}
    assert not (output / "config-draft.json").exists()
    assert not (output / "report.md").exists()
    case["fail_generation"] = False
    case["calls"].clear()
    run(case, resume=True)
    assert case["calls"] == ["contexts", "generation"]
    assert all((output / name).read_bytes() == content for name, content in saved.items())


def test_completed_run_resumes_without_api_credentials(
    pipeline_case: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = pipeline_case
    report = run(case)
    saved = report.read_bytes()
    case["calls"].clear()
    monkeypatch.setattr(pipeline, "load_cloud_config", lambda: pytest.fail("No API configuration needed"))
    assert run(case, resume=True).read_bytes() == saved
    assert case["calls"] == ["contexts"]


@pytest.mark.parametrize("change", ["requirement", "catalog", "limit", "output", "missing_output", "corpus"])
def test_resume_rejects_changed_inputs_outputs_and_sources(pipeline_case: dict, change: str) -> None:
    case = pipeline_case
    run(case)
    case["calls"].clear()
    kwargs = {"resume": True}
    if change == "requirement":
        with pytest.raises(pipeline.PipelineError, match="inputs changed"):
            pipeline.run_pipeline(
                "Different requirement", case["output_dir"],
                catalog_path=case["catalog_path"], resume=True,
            )
        return
    if change == "catalog":
        case["catalog_path"].write_text('{"changed": true}')
    elif change == "limit":
        kwargs["limit"] = 5
    elif change == "output":
        (case["output_dir"] / "selection.json").write_text("{}")
    elif change == "missing_output":
        (case["output_dir"] / "contexts.json").unlink()
    elif change == "corpus":
        case["corpus"] = "changed"
    with pytest.raises(pipeline.PipelineError, match="changed|missing|differ"):
        run(case, **kwargs)
    assert not any(stage in case["calls"] for stage in ("selection", "search", "generation"))


def test_report_failure_resumes_only_rendering(
    pipeline_case: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = pipeline_case
    original = pipeline.load_report

    def fail(path: Path) -> str:
        raise ReportError("Rendering failed.")

    monkeypatch.setattr(pipeline, "load_report", fail)
    with pytest.raises(pipeline.PipelineError, match="report"):
        run(case)
    assert (case["output_dir"] / "config-draft.json").is_file()
    monkeypatch.setattr(pipeline, "load_report", original)
    monkeypatch.setattr(pipeline, "load_cloud_config", lambda: pytest.fail("No repeated API call"))
    case["calls"].clear()
    assert run(case, resume=True).is_file()
    assert case["calls"] == ["contexts"]


def test_resume_rejects_a_checkpoint_with_missing_prerequisites(pipeline_case: dict) -> None:
    case = pipeline_case
    run(case)
    checkpoint = case["output_dir"] / pipeline.STATE_FILE
    state = json.loads(checkpoint.read_bytes())
    del state["completed"]["search"]
    checkpoint.write_text(json.dumps(state))
    case["calls"].clear()
    with pytest.raises(pipeline.PipelineError, match="consecutive prefix"):
        run(case, resume=True)
    assert case["calls"] == []


def test_new_runs_do_not_overwrite_existing_files_or_source_directories(pipeline_case: dict) -> None:
    case = pipeline_case
    output = case["output_dir"]
    output.mkdir()
    saved = output / "config-draft.json"
    saved.write_text("existing output")
    with pytest.raises(pipeline.PipelineError, match="already exist"):
        run(case)
    assert saved.read_text() == "existing output"
    with pytest.raises(pipeline.PipelineError, match="inside sources"):
        pipeline.run_pipeline("test", output / "nested", okf_root=output)
    saved.unlink()
    saved.symlink_to(case["catalog_path"])
    with pytest.raises(pipeline.PipelineError, match="symbolic link"):
        run(case)


def test_cli_requirement_file_and_resume(
    pipeline_case: dict, tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    case = pipeline_case
    requirement = tmp_path / "requirements.txt"
    requirement.write_text("VLAN 120を作成してポートに割り当てる")
    args = [
        "--requirement-file", str(requirement), "--output-dir", str(case["output_dir"]),
        "--catalog", str(case["catalog_path"]),
    ]
    assert pipeline.main(args) == 0
    captured = capsys.readouterr()
    assert "Report written:" in captured.out
    assert "[1/5] Running selection" in captured.err
    assert pipeline.main(args) == 1
    assert "already exist" in capsys.readouterr().err
    assert pipeline.main(args + ["--resume"]) == 0
    assert "Reusing" in capsys.readouterr().err
