import copy
import json
from pathlib import Path

import pytest

from configbrain.report import (
    ReportError, main, render_pipeline_failure_report, render_report,
)


PIPELINE_STAGES = {
    "selection": "completed",
    "search": "completed",
    "contexts": "completed",
    "generation": "completed_with_warnings",
    "report": "completed",
}


def test_report_preserves_commands_templates_and_traceable_evidence(report_draft: dict) -> None:
    text = render_report(report_draft)
    assert "```text\nvlan 120\n```" in text
    assert "interface <INTERFACE_ID>\nswitchport access vlan 120" in text
    assert "show interfaces <INTERFACE_ID> switchport" in text
    assert "追加情報が必要 (needs_input)" in text
    assert "Target physical port." in text and "Manual restriction." in text
    expected_evidence_id = "evidence-v1-" + "a" * 64
    assert (
        f"[source-1](#source-1) / 根拠ID `{expected_evidence_id}`"
        " / Step 2 / 範囲内の表 1・行 3"
    ) in text
    assert "章内行: 10–20" in text and "OKF行: 100–110" in text
    assert "章先頭の物理ページ 35（手順のページではありません）" in text
    assert "PDF SHA-256: " + "b" * 64 in text
    assert "推論候補（根拠付き設定とは別・未検証）" in text
    assert "Saving was not requested." in text
    assert "Invalid binding shape." in text
    assert "実機投入不可" in text


def test_report_includes_pipeline_stage_statuses(report_draft: dict) -> None:
    text = render_report(report_draft, pipeline_stages=PIPELINE_STAGES)
    assert "## パイプライン実行状況" in text
    assert "| 選定 (`selection`) | 成功 |" in text
    assert "| コンフィグ案生成 (`generation`) | 警告あり |" in text
    assert "| レポート生成 (`report`) | 成功 |" in text


def test_report_displays_per_citation_pdf_verification(report_draft: dict) -> None:
    report_draft["pdf_verification_status"] = "partial"
    report_draft["pdf_verification_version"] = 1
    command_citation = report_draft["items"][0]["configuration"][0]["citations"][0]
    command_citation["quote"] = "Source command text."
    command_citation["pdf_verification"] = {
        "status": "matched", "page_indexes": [47], "fragment_count": 3,
        "method": "normalized_text_fragments",
    }
    constraint_citation = report_draft["items"][0]["constraints"][0]["citations"][0]
    constraint_citation["evidence_id"] = "evidence-v1-" + "c" * 64
    constraint_citation["pdf_verification"] = {
        "status": "not_found", "page_indexes": [], "fragment_count": 1,
        "method": "normalized_text_fragments",
    }

    text = render_report(report_draft)

    assert "引用照合に未一致または複数候補" in text
    assert "PDF物理ページ 47（テキスト照合）" in text
    assert "PDFテキスト未一致" in text


def test_report_rejects_inconsistent_global_pdf_verification(
    report_draft: dict,
) -> None:
    report_draft["pdf_verification_status"] = "verified"
    report_draft["pdf_verification_version"] = 1
    command_citation = report_draft["items"][0]["configuration"][0]["citations"][0]
    command_citation.update(
        quote="Source command text.",
        pdf_verification={
            "status": "matched", "page_indexes": [47], "fragment_count": 1,
            "method": "normalized_text_fragments",
        },
    )
    constraint_citation = report_draft["items"][0]["constraints"][0]["citations"][0]
    constraint_citation["evidence_id"] = "evidence-v1-" + "c" * 64
    constraint_citation["pdf_verification"] = {
        "status": "not_found", "page_indexes": [], "fragment_count": 1,
        "method": "normalized_text_fragments",
    }

    with pytest.raises(ReportError, match="Global PDF verification"):
        render_report(report_draft)


def test_report_separates_generation_success_from_incomplete_fulfillment(
    report_draft: dict,
) -> None:
    report_draft["generation_status"] = "complete"
    text = render_report(report_draft)

    assert (
        "**要件充足判定: 未完了（追加情報・未充足項目あり） (`incomplete`)**"
        in text
    )

    report_draft["fulfillment_status"] = "complete"
    with pytest.raises(ReportError, match="fulfillment_status"):
        render_report(report_draft)


def test_report_marks_all_supported_items_as_complete(report_draft: dict) -> None:
    report_draft["items"][0].update(
        status="supported", missing_information=[], assumptions=[],
        templates=[], verification_templates=[], withheld_steps=[],
    )
    report_draft["fulfillment_status"] = "complete"
    text = render_report(report_draft)
    assert "**要件充足判定: 全項目が supported（判定上） (`complete`)**" in text


def test_report_displays_generation_round_count(report_draft: dict) -> None:
    report_draft["generation_rounds"] = 3
    report_draft["generation_rounds_used"] = 2
    report_draft["validation_repairs"] = [{
        "item_id": "item-1", "round": 2, "validation_error": "Invalid response.",
    }]
    text = render_report(report_draft)
    assert "生成レビュー: 2/3 ラウンド" in text
    assert "item-1 (round 2): Invalid response." in text


@pytest.mark.parametrize(
    ("generation_rounds", "generation_rounds_used"),
    [(0, 0), (3, 4), (3, None)],
)
def test_report_rejects_invalid_generation_round_counts(
    report_draft: dict, generation_rounds: int, generation_rounds_used: int | None,
) -> None:
    report_draft["generation_rounds"] = generation_rounds
    report_draft["generation_rounds_used"] = generation_rounds_used
    with pytest.raises(ReportError, match="round counts"):
        render_report(report_draft)


def test_pipeline_failure_report_shows_failed_and_unstarted_stages() -> None:
    statuses = {
        "selection": "completed",
        "search": "completed",
        "contexts": "failed",
        "generation": "not_started",
        "report": "not_started",
    }
    text = render_pipeline_failure_report("Create VLAN 120.", statuses, "Context error.")
    assert "| 検索 (`search`) | 成功 |" in text
    assert "| 文脈取得 (`contexts`) | 失敗 |" in text
    assert "| コンフィグ案生成 (`generation`) | 未実行 |" in text
    assert "Context error." in text
    assert "未検証の設定コマンドを含みません" in text


def test_report_handles_unsupported_items_without_dropping_them(report_draft: dict) -> None:
    item = report_draft["items"][0]
    item.update(
        status="not_found", configuration=[], templates=[], verification=[],
        verification_templates=[], evidence=[], constraints=[], withheld_steps=[],
        inference_candidates=[],
    )
    text = render_report(report_draft)
    assert "根拠未発見 (not_found)" in text
    assert "## item-1" in text and "Target physical port." in text


def test_report_marks_failed_generation_as_unassessed(report_draft: dict) -> None:
    report_draft["generation_status"] = "partial"
    item = report_draft["items"][0]
    item.update(
        status=None, generation_error="Generation for item-1 failed: response invalid.",
        configuration=[], templates=[], verification=[], verification_templates=[],
    )
    text = render_report(report_draft)
    assert "自動生成未完了" in text
    assert "要件充足判定: 未判定（生成エラー等） (`undetermined`)" in text
    assert "生成未完了 (generation_failed)" in text
    assert "充足状態は判定されていません" in text
    assert "response invalid." in text
    assert "vlan 120" not in text


def test_report_displays_cross_item_consistency_warnings(report_draft: dict) -> None:
    report_draft["generation_status"] = "partial"
    first = report_draft["items"][0]
    first.update(status="supported", missing_information=[], assumptions=[])
    first["configuration"][0]["cli"] = "ip address 192.0.2.1 255.255.255.0"
    second = copy.deepcopy(first)
    second.update(
        item_id="item-2", requirement="Assign IPv4 address 192.0.2.1/24",
        status="not_found", assessment="No eligible source command.",
        configuration=[], templates=[], verification=[], verification_templates=[],
        missing_information=[], assumptions=[], constraints=[], evidence=[],
        withheld_steps=[], inference_candidates=[],
    )
    report_draft["items"].append(second)
    report_draft["consistency_warnings"] = [{
        "item_id": "item-1", "related_item_id": "item-2",
        "message": "A command in item-1 uses values also requested by item-2.",
    }]
    text = render_report(report_draft)
    assert "項目間の整合性警告" in text
    assert "項目間に整合性確認が必要な警告があります" in text
    assert "一部の要件項目は自動生成未完了" not in text
    assert "item-1" in text and "item-2" in text


def test_report_displays_requirement_command_graph(report_draft: dict) -> None:
    report_draft["items"][0]["depends_on"] = []
    evidence_id = report_draft["items"][0]["configuration"][0]["citations"][0][
        "evidence_id"
    ]
    report_draft["requirement_graph"] = {
        "requirements": [{
            "item_id": "item-1", "status": "needs_input", "depends_on": [],
        }],
        "commands": [{
            "node_id": "command-node-1", "command_id": "command-1",
            "cli": "vlan 120", "role": "configuration",
            "owner_item_id": "item-1", "required_by": ["item-1"],
            "evidence_ids": [evidence_id], "bindings": {},
        }],
        "edges": [{
            "from": "item-1", "to": "command-node-1", "kind": "owns",
        }],
    }

    text = render_report(report_draft)

    assert "## 要件・コマンド依存グラフ" in text
    assert "### コマンドの所有・共有関係" in text
    assert "| vlan 120 | 設定 | [item-1](#item-1) | [item-1](#item-1) |" in text
    assert "generation_status" not in text


@pytest.mark.parametrize("change", ["status", "commands", "generation_status"])
def test_report_rejects_unsafe_partial_generation(
    report_draft: dict, change: str,
) -> None:
    report_draft["generation_status"] = "partial"
    item = report_draft["items"][0]
    item["generation_error"] = "Generation failed."
    if change == "status":
        item["status"] = "supported"
    elif change == "commands":
        item["configuration"] = [{"cli": "vlan 120"}]
    else:
        report_draft["generation_status"] = "complete"
        item["status"] = None
    with pytest.raises(ReportError):
        render_report(report_draft)


def test_report_shows_cidr_derivation(report_draft: dict) -> None:
    report_draft["items"][0]["configuration"][0]["bindings"] = {
        "subnet-mask": {
            "value": "255.255.255.0",
            "derivation": {"kind": "ipv4_cidr_netmask", "source": "192.0.2.1/24"},
        },
    }
    text = render_report(report_draft)
    assert "192.0.2.1/24 から計算したマスク 255.255.255.0" in text


def test_report_shows_interface_case_normalization(report_draft: dict) -> None:
    report_draft["items"][0]["configuration"][0]["normalizations"] = [{
        "kind": "interface_case", "original_cli": "interface fortygigabitethernet1/0/1",
    }]
    text = render_report(report_draft)
    assert "インターフェース名の大文字小文字を要件の表記に統一" in text
    assert "元のCLI: interface fortygigabitethernet1/0/1" in text


@pytest.mark.parametrize(
    "change", [
        "unknown_source", "invalid_evidence_id", "unsafe_flags",
        "missing_cli", "missing_items", "show_as_config",
    ],
)
def test_report_rejects_malformed_or_unsafe_drafts(report_draft: dict, change: str) -> None:
    if change == "unknown_source":
        report_draft["sources"] = {}
    elif change == "invalid_evidence_id":
        report_draft["items"][0]["configuration"][0]["citations"][0][
            "evidence_id"
        ] = "not-a-canonical-id"
    elif change == "unsafe_flags":
        report_draft["ready_to_apply"] = True
    elif change == "missing_cli":
        report_draft["items"][0]["configuration"][0].pop("cli")
    elif change == "show_as_config":
        report_draft["items"][0]["configuration"][0]["cli"] = "show ip route"
    else:
        report_draft.pop("items")
    with pytest.raises(ReportError):
        render_report(report_draft)


def test_report_escapes_text_and_uses_safe_code_fences(report_draft: dict) -> None:
    report_draft["requirement"] = "<script>alert(1)</script>\n# injected | column"
    report_draft["items"][0]["configuration"][0]["cli"] = "name ```"
    text = render_report(report_draft)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "\\# injected \\| column" in text
    assert "````text\nname ```\n````" in text


def test_report_cli_preserves_existing_output_and_protects_sources(
    report_draft: dict, tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(report_draft))
    output = tmp_path / "report.md"
    args = ["--draft", str(path), "--output", str(output)]
    assert main(args) == 0
    saved = output.read_bytes()
    path.write_text("{}")
    assert main(args) == 1
    assert output.read_bytes() == saved
    assert "Report generation failed" in capsys.readouterr().err
    source = tmp_path / "manual.md"
    source.write_text("original")
    report_draft["sources"]["source-1"]["manual_path"] = str(source)
    path.write_text(json.dumps(report_draft))
    assert main(["--draft", str(path), "--output", str(source)]) == 1
    assert source.read_text() == "original"
    assert main(["--draft", str(path), "--output", str(path)]) == 1
