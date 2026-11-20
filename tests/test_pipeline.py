import copy
import json
from pathlib import Path

import pytest

from configbrain import pipeline
from configbrain.generate_config import GenerationError
from configbrain.report import ReportError
from configbrain.select_manuals import CloudConfig, SelectionError
from configbrain.search_manuals import SearchError
from configbrain.expand_context import ContextError
from configbrain.semantic_review import review_explanations
from configbrain.verify_pdf import _citations


@pytest.fixture
def pipeline_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report_draft: dict) -> dict:
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"test_catalog": true}\n')
    case = {
        "catalog_path": catalog, "output_dir": tmp_path / "run",
        "selection_metadata_root": tmp_path / "selection-metadata",
        "calls": [], "fail_generation": False, "partial_generation": False,
        "partial_pdf_verification": False, "consistency_warning": False,
        "selection_metadata_warnings": [],
        "corpus": "original", "fail_stage": None,
        "preflight_error": None,
    }

    def preflight(*args):
        if case["preflight_error"]:
            raise pipeline.PipelineError(case["preflight_error"])
        return {"status": "completed", "configuration_guide_count": 1, "command_reference_count": 1}

    def select(
        requirement, catalog_path, product, release, config, *, okf_root, metadata_root,
    ):
        case["calls"].append("selection")
        if case["fail_stage"] == "selection":
            raise SelectionError("selection failed")
        assert catalog_path == catalog
        assert (product, release) == ("catalyst9300", "26.x")
        assert okf_root == Path("data/okf")
        assert metadata_root == case["selection_metadata_root"]
        return {
            "requirement": requirement,
            "catalog": "test",
            "selection_metadata_warnings": case["selection_metadata_warnings"],
        }

    def search(selection_path, catalog_path, database_path, limit, *, reference_root):
        case["calls"].append("search")
        if case["fail_stage"] == "search":
            raise SearchError("search failed")
        selection = json.loads(selection_path.read_bytes())
        assert selection["catalog"] == "test"
        return {"requirement": selection["requirement"], "limit": limit}

    def expand(search_path, catalog_path, okf_root, source_pdf_root, reference_root):
        case["calls"].append("contexts")
        case["reference_root"] = reference_root
        if case["fail_stage"] == "contexts":
            raise ContextError("contexts failed")
        search = json.loads(search_path.read_bytes())
        return {
            "requirement": search["requirement"], "corpus": case["corpus"],
            "context_policy_version": pipeline.CONTEXT_POLICY_VERSION,
        }

    def generate(
        contexts_path, search_path, catalog_path, okf_root, source_pdf_root, config,
        generation_rounds=3,
        reference_root=Path("data/command_references"),
    ):
        case["calls"].append("generation")
        case["generation_rounds"] = generation_rounds
        assert reference_root == case["reference_root"]
        if case["fail_generation"] or case["fail_stage"] == "generation":
            raise GenerationError("Draft remains invalid after one repair.")
        contexts = json.loads(contexts_path.read_bytes())
        assert contexts["corpus"] == case["corpus"]
        draft = copy.deepcopy(report_draft)
        draft["requirement"] = contexts["requirement"]
        draft["evidence_registry_version"] = pipeline.EVIDENCE_REGISTRY_VERSION
        draft["pdf_verification_version"] = pipeline.PDF_VERIFICATION_VERSION
        draft["pdf_verification_status"] = (
            "partial" if case["partial_pdf_verification"] else "verified"
        )

        def annotate_citations(value):
            if isinstance(value, dict):
                if "source_id" in value:
                    value.setdefault("quote", "Verified source text.")
                    value.setdefault("evidence_id", "evidence-v1-" + "c" * 64)
                    value["pdf_verification"] = {
                        "status": (
                            "ambiguous" if case["partial_pdf_verification"] else "matched"
                        ),
                        "page_indexes": (
                            [34, 35] if case["partial_pdf_verification"] else [35]
                        ),
                        "fragment_count": 1,
                        "method": "normalized_text_fragments",
                    }
                for child in value.values():
                    annotate_citations(child)
            elif isinstance(value, list):
                for child in value:
                    annotate_citations(child)

        annotate_citations(draft)
        if case["partial_generation"]:
            draft["generation_status"] = "partial"
            draft["items"][0].update(
                status=None, generation_error="Generation for item-1 failed.",
                configuration=[], templates=[], verification=[],
                verification_templates=[],
            )
        if case["consistency_warning"]:
            draft["items"].append(copy.deepcopy(draft["items"][0]))
            draft["items"][1].update(
                item_id="item-2", requirement="Assign IPv4 address 192.0.2.1/24",
                status="not_found", assessment="No eligible source command.",
                configuration=[], templates=[], verification=[],
                verification_templates=[], missing_information=[], assumptions=[],
                constraints=[], evidence=[], withheld_steps=[],
                inference_candidates=[],
            )
            draft["generation_status"] = "partial"
            draft["consistency_warnings"] = [{
                "item_id": "item-1", "related_item_id": "item-2",
                "message": "A command in item-1 overlaps values requested by item-2.",
            }]
        return draft

    monkeypatch.setattr(pipeline, "select_manuals", select)
    monkeypatch.setattr(pipeline, "search_manuals", search)
    monkeypatch.setattr(pipeline, "expand_context", expand)
    monkeypatch.setattr(pipeline, "generate_config", generate)
    monkeypatch.setattr(pipeline, "_validate_pipeline_sources", preflight)
    monkeypatch.setattr(
        pipeline, "load_cloud_config",
        lambda: CloudConfig("https://example.test", "test-key", None),
    )
    return case


def run(case: dict, **kwargs) -> Path:
    kwargs.setdefault("selection_metadata_root", case["selection_metadata_root"])
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
    report_text = report.read_text()
    assert "## パイプライン実行状況" in report_text
    assert all(f"(`{stage}`) | 成功 |" in report_text for stage, _ in pipeline.STAGES)
    state = json.loads((case["output_dir"] / pipeline.STATE_FILE).read_bytes())
    assert state["status"] == "completed" and state["current_stage"] is None
    assert list(state["completed"]) == [stage for stage, _ in pipeline.STAGES]
    assert set(state["stage_statuses"].values()) == {"completed"}
    assert state["generation_rounds"] == 3
    assert case["generation_rounds"] == 3
    for stage, filename in pipeline.STAGES:
        assert state["completed"][stage]["sha256"] == pipeline._sha256(case["output_dir"] / filename)
    assert "test-key" not in json.dumps(state)


def test_missing_selection_metadata_warning_reaches_pipeline_state(
    pipeline_case: dict,
) -> None:
    case = pipeline_case
    case["selection_metadata_warnings"] = [
        "Selection metadata is missing for vlan-guide; chapter titles only.",
    ]

    report = run(case)
    state = json.loads((case["output_dir"] / pipeline.STATE_FILE).read_bytes())

    assert state["status"] == "completed_with_warnings"
    assert state["stage_statuses"]["selection"] == "completed_with_warnings"
    assert state["warnings"] == [{
        "stage": "selection",
        "error": "Selection metadata is missing for vlan-guide; chapter titles only.",
    }]
    assert "警告あり" in report.read_text(encoding="utf-8")


def test_preflight_failure_reports_missing_sources_before_any_llm_calls(
    pipeline_case: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = pipeline_case
    case["preflight_error"] = "コマンドリファレンスがありません。"
    monkeypatch.setattr(pipeline, "load_cloud_config", lambda: pytest.fail("No API configuration needed"))

    with pytest.raises(pipeline.PipelineError, match="preflight"):
        run(case)

    assert case["calls"] == []
    state = json.loads((case["output_dir"] / pipeline.STATE_FILE).read_bytes())
    assert state["current_stage"] == "preflight" and state["completed"] == {}
    assert state["preflight"]["status"] == "failed"
    assert set(state["stage_statuses"].values()) == {"not_started"}
    report = (case["output_dir"] / "report.md").read_text()
    assert "事前確認に失敗" in report and "コマンドリファレンスがありません" in report


def test_preflight_failure_on_resume_preserves_cached_plan_and_allows_rechecking(
    pipeline_case: dict,
) -> None:
    case = pipeline_case
    run(case)
    saved = {
        filename: (case["output_dir"] / filename).read_bytes()
        for stage, filename in pipeline.STAGES if stage != "report"
    }
    case["preflight_error"] = "保存HTMLがありません。"
    case["calls"].clear()
    with pytest.raises(pipeline.PipelineError, match="preflight"):
        run(case, resume=True)
    assert not case["calls"]
    assert all((case["output_dir"] / filename).read_bytes() == raw for filename, raw in saved.items())
    case["preflight_error"] = None
    assert run(case, resume=True).is_file()
    assert case["calls"] == ["contexts"]


def test_missing_catalog_writes_preflight_diagnostic_without_api_access(
    pipeline_case: dict,
) -> None:
    case = pipeline_case
    case["catalog_path"].unlink()
    with pytest.raises(pipeline.PipelineError, match="カタログを読み取れません"):
        run(case)
    assert case["calls"] == []
    assert "カタログを読み取れません" in (case["output_dir"] / "report.md").read_text()


def test_pipeline_completes_with_warnings_for_partial_generation(
    pipeline_case: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = pipeline_case
    case["partial_generation"] = True
    report = run(case)
    saved_report = report.read_bytes()
    text = report.read_text()
    state = json.loads((case["output_dir"] / pipeline.STATE_FILE).read_bytes())
    assert "自動生成未完了" in text
    assert "充足状態は判定されていません" in text
    assert state["status"] == "completed_with_warnings"
    assert state["current_stage"] is None
    assert state["warnings"] == [{
        "item_id": "item-1", "error": "Generation for item-1 failed.",
    }]
    assert list(state["completed"]) == [stage for stage, _ in pipeline.STAGES]
    case["calls"].clear()
    monkeypatch.setattr(
        pipeline, "load_cloud_config", lambda: pytest.fail("No API configuration needed"),
    )
    assert run(case, resume=True).read_bytes() == saved_report
    assert case["calls"] == ["contexts"]


def test_pipeline_records_cross_item_consistency_warnings(pipeline_case: dict) -> None:
    case = pipeline_case
    case["consistency_warning"] = True
    report = run(case)
    state = json.loads((case["output_dir"] / pipeline.STATE_FILE).read_bytes())
    assert "項目間の整合性警告" in report.read_text()
    assert state["status"] == "completed_with_warnings"
    assert state["warnings"] == [{
        "item_id": "item-1", "related_item_id": "item-2",
        "error": "A command in item-1 overlaps values requested by item-2.",
    }]


def test_pipeline_records_partial_pdf_verification_as_a_warning(
    pipeline_case: dict,
) -> None:
    case = pipeline_case
    case["partial_pdf_verification"] = True

    report = run(case)

    state = json.loads((case["output_dir"] / pipeline.STATE_FILE).read_bytes())
    assert "複数候補" in report.read_text()
    assert state["status"] == "completed_with_warnings"
    assert state["stage_statuses"]["generation"] == "completed_with_warnings"
    assert state["warnings"] == [{
        "pdf_verification_status": "partial",
        "error": "One or more citations are not uniquely matched to the original PDF.",
    }]


@pytest.mark.parametrize("outcome", ["unsupported", "unknown", "request_failed"])
def test_pipeline_surfaces_semantic_issues_and_keeps_cli_on_resume(
    pipeline_case: dict, monkeypatch: pytest.MonkeyPatch,
    mock_semantic_reviewer: list, outcome: str,
) -> None:
    from configbrain import semantic_review

    generate = pipeline.generate_config
    approve = semantic_review.request_json

    def request(system_prompt, prompt, config):
        if outcome == "request_failed":
            raise SelectionError("Cloud LLM request failed.")
        response, model = approve(system_prompt, prompt, config)
        for result in response["reviews"]:
            for claim in result["claims"]:
                if outcome == "unsupported":
                    claim["support"] = "contradicted"
                else:
                    claim.update(applicability="unknown", conditions=["Existing device state."])
        return response, model

    def reviewed_generation(*args, **kwargs):
        draft = generate(*args, **kwargs)
        atoms = {
            citation["evidence_id"]: {
                "source_id": citation["source_id"], "text": citation["quote"],
                "evidence_id": citation["evidence_id"],
                "locator": citation.get("locator", {"kind": "paragraph", "block_index": 1}),
            }
            for citation in _citations(draft["items"])
        }
        evidence = {
            "sources": draft["sources"], "evidence_atoms": atoms,
            "items": [{
                "item_id": "item-1", "source_ids": ["source-1"], "reference_source_ids": [],
                "evidence_ids": list(atoms), "reference_evidence_ids": [],
            }],
        }
        review_explanations(
            draft, evidence, CloudConfig("https://unused.test", "not-a-real-key", None), 1,
            lambda *args: pytest.fail("No repair when rounds=1"),
            lambda *args: pytest.fail("No repaired citations to verify"),
        )
        draft["generation_status"] = "complete" if outcome == "unknown" else "partial"
        return draft

    monkeypatch.setattr(semantic_review, "request_json", request)
    monkeypatch.setattr(pipeline, "generate_config", reviewed_generation)
    case = pipeline_case
    report = run(case)
    original = report.read_bytes()
    state = json.loads((case["output_dir"] / pipeline.STATE_FILE).read_bytes())
    assert state["status"] == "completed_with_warnings"
    assert state["stage_statuses"]["generation"] == "completed_with_warnings"
    assert len(state["warnings"]) == 1
    assert state["warnings"][0]["item_id"] == "item-1"
    assert "vlan 120" in report.read_text()
    assert ("支持・適用条件未確認" if outcome == "unknown" else "説明の意味検証に未解決") in report.read_text()
    case["calls"].clear()
    monkeypatch.setattr(
        semantic_review, "request_json", lambda *args: pytest.fail("Resume must reuse saved review"),
    )
    assert run(case, resume=True).read_bytes() == original
    assert case["calls"] == ["contexts"]


def test_pipeline_generation_rounds_are_configurable_and_checkpointed(
    pipeline_case: dict,
) -> None:
    case = pipeline_case
    run(case, generation_rounds=2)
    state = json.loads((case["output_dir"] / pipeline.STATE_FILE).read_bytes())
    assert state["generation_rounds"] == 2
    assert case["generation_rounds"] == 2
    with pytest.raises(pipeline.PipelineError, match="Generation rounds changed"):
        run(case, generation_rounds=3, resume=True)


@pytest.mark.parametrize(
    ("stage", "completed"),
    [
        ("selection", []),
        ("search", ["selection"]),
        ("contexts", ["selection", "search"]),
        ("generation", ["selection", "search", "contexts"]),
    ],
)
def test_stage_failure_writes_a_diagnostic_report(
    pipeline_case: dict, stage: str, completed: list[str],
) -> None:
    case = pipeline_case
    case["fail_generation"] = stage == "generation"
    case["fail_stage"] = stage
    with pytest.raises(pipeline.PipelineError, match=f"{stage}.*--resume"):
        run(case)
    output = case["output_dir"]
    state = json.loads((output / pipeline.STATE_FILE).read_bytes())
    assert state["status"] == "failed"
    assert list(state["completed"]) == completed
    assert state["stage_statuses"][stage] == "failed"
    stage_index = [name for name, _ in pipeline.STAGES].index(stage)
    for downstream, _ in pipeline.STAGES[stage_index + 1:]:
        assert state["stage_statuses"][downstream] == "not_started"
    text = (output / "report.md").read_text()
    assert "パイプライン実行診断" in text
    assert f"`{stage}`) | 失敗 |" in text
    assert "この診断には未検証の設定コマンドを含みません" in text


def test_generation_failure_resumes_without_reselecting_or_searching(pipeline_case: dict) -> None:
    case = pipeline_case
    case["fail_generation"] = True
    with pytest.raises(pipeline.PipelineError, match="generation.*--resume"):
        run(case)
    output = case["output_dir"]
    saved = {name: (output / name).read_bytes() for name in (
        "selection.json", "search-results.json", "contexts.json",
    )}
    case["fail_generation"] = False
    case["fail_stage"] = None
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


def test_resume_renders_generation_drafts_without_request_history(
    pipeline_case: dict,
) -> None:
    case = pipeline_case
    run(case)
    output = case["output_dir"]
    generation_path = output / "config-draft.json"
    generation = json.loads(generation_path.read_bytes())
    generation.update(generation_rounds=3, generation_rounds_used=1)
    generation["generation_requests"] = [{
        "round": 1, "kind": "initial", "item_ids": ["item-1"],
        "issues": {}, "validated_item_ids": ["item-1"], "partial_item_ids": [],
        "response_errors": {}, "outcome": "resolved", "no_progress": False,
        "unresolved_issues": {},
    }]
    generation_path.write_text(json.dumps(generation))
    state_path = output / pipeline.STATE_FILE
    state = json.loads(state_path.read_bytes())
    state["completed"]["generation"]["sha256"] = pipeline._sha256(generation_path)
    state["completed"].pop("report")
    state["stage_statuses"]["report"] = "not_started"
    state_path.write_text(json.dumps(state))
    run(case, resume=True)
    assert "生成ラウンド・リクエスト履歴" in (output / "report.md").read_text()

    generation.pop("generation_requests")
    generation_path.write_text(json.dumps(generation))
    state = json.loads(state_path.read_bytes())
    state["completed"]["generation"]["sha256"] = pipeline._sha256(generation_path)
    state["completed"].pop("report")
    state["stage_statuses"]["report"] = "not_started"
    state_path.write_text(json.dumps(state))
    run(case, resume=True)
    assert "生成ラウンド・リクエスト履歴" not in (output / "report.md").read_text()


def test_resume_regenerates_drafts_without_canonical_evidence_ids(
    pipeline_case: dict,
) -> None:
    case = pipeline_case
    run(case)
    generation_path = case["output_dir"] / "config-draft.json"
    draft = json.loads(generation_path.read_bytes())
    draft.pop("evidence_registry_version")
    generation_path.write_text(json.dumps(draft))
    state_path = case["output_dir"] / pipeline.STATE_FILE
    state = json.loads(state_path.read_bytes())
    state["completed"]["generation"]["sha256"] = pipeline._sha256(generation_path)
    state_path.write_text(json.dumps(state))
    case["calls"].clear()

    run(case, resume=True)

    assert case["calls"] == ["contexts", "generation"]
    regenerated = json.loads(generation_path.read_bytes())
    assert regenerated["evidence_registry_version"] == pipeline.EVIDENCE_REGISTRY_VERSION


@pytest.mark.parametrize(
    "change", [
        "requirement", "catalog", "limit", "reference_root", "selection_metadata",
        "output", "missing_output", "corpus",
    ],
)
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
    elif change == "reference_root":
        kwargs["reference_root"] = case["output_dir"].parent / "other-references"
    elif change == "selection_metadata":
        case["selection_metadata_root"].mkdir()
        (case["selection_metadata_root"] / "vlan-guide.json").write_text('{"updated": true}')
    elif change == "output":
        (case["output_dir"] / "selection.json").write_text("{}")
    elif change == "missing_output":
        (case["output_dir"] / "contexts.json").unlink()
    elif change == "corpus":
        case["corpus"] = "changed"
    with pytest.raises(pipeline.PipelineError, match="changed|missing|differ"):
        run(case, **kwargs)
    assert not any(stage in case["calls"] for stage in ("selection", "search", "generation"))


def test_pipeline_preserves_custom_reference_root_on_resume(pipeline_case: dict) -> None:
    case = pipeline_case
    root = case["output_dir"].parent / "references"
    run(case, reference_root=root)
    case["calls"].clear()
    run(case, reference_root=root, resume=True)
    assert case["calls"] == ["contexts"]
    assert case["reference_root"] == root


def test_resume_accepts_old_checkpoint_with_default_reference_root(pipeline_case: dict) -> None:
    case = pipeline_case
    run(case)
    state_path = case["output_dir"] / pipeline.STATE_FILE
    state = json.loads(state_path.read_bytes())
    del state["inputs"]["reference_root"]
    state_path.write_text(json.dumps(state))
    case["calls"].clear()
    run(case, resume=True)
    assert case["calls"] == ["contexts"]


def test_context_policy_change_rejects_resume_without_overwriting_previous_outputs(
    pipeline_case: dict,
) -> None:
    case = pipeline_case
    run(case)
    contexts_path = case["output_dir"] / "contexts.json"
    contexts = json.loads(contexts_path.read_bytes())
    del contexts["context_policy_version"]
    contexts_path.write_text(json.dumps(contexts))
    state_path = case["output_dir"] / pipeline.STATE_FILE
    state = json.loads(state_path.read_bytes())
    state["completed"]["contexts"]["sha256"] = pipeline._sha256(contexts_path)
    state_path.write_text(json.dumps(state))
    saved = {
        path: path.read_bytes() for path in case["output_dir"].iterdir() if path.is_file()
    }
    case["calls"].clear()
    with pytest.raises(pipeline.PipelineError, match="Context policy changed"):
        run(case, resume=True)
    assert case["calls"] == []
    assert all(path.read_bytes() == content for path, content in saved.items())


def test_report_failure_resumes_only_rendering(
    pipeline_case: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = pipeline_case
    original = pipeline.load_report

    def fail(path: Path, pipeline_stages: dict[str, str] | None = None) -> str:
        raise ReportError("Rendering failed.")

    monkeypatch.setattr(pipeline, "load_report", fail)
    with pytest.raises(pipeline.PipelineError, match="report"):
        run(case)
    assert (case["output_dir"] / "config-draft.json").is_file()
    failure_report = (case["output_dir"] / "report.md").read_text()
    assert "`report`) | 失敗 |" in failure_report
    assert "Rendering failed." in failure_report
    monkeypatch.setattr(pipeline, "load_report", original)
    monkeypatch.setattr(pipeline, "load_cloud_config", lambda: pytest.fail("No repeated API call"))
    case["calls"].clear()
    assert run(case, resume=True).is_file()
    assert case["calls"] == ["contexts"]
    assert "パイプライン実行診断" not in (case["output_dir"] / "report.md").read_text()


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
    for root in ("okf_root", "reference_root"):
        with pytest.raises(pipeline.PipelineError, match="inside sources"):
            pipeline.run_pipeline("test", output / "nested", **{root: output})
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
        "--catalog", str(case["catalog_path"]), "--generation-rounds", "2",
        "--selection-metadata-root", str(case["selection_metadata_root"]),
    ]
    assert pipeline.main(args) == 0
    captured = capsys.readouterr()
    assert "Report written:" in captured.out
    assert "[1/5] Running selection" in captured.err
    state = json.loads((case["output_dir"] / pipeline.STATE_FILE).read_bytes())
    assert state["generation_rounds"] == 2
    assert pipeline.main(args) == 1
    assert "already exist" in capsys.readouterr().err
    assert pipeline.main(args + ["--resume"]) == 0
    assert "Reusing" in capsys.readouterr().err
