import json
from pathlib import Path

import pytest

from configbrain.report import ReportError, main, render_report


def test_report_preserves_commands_templates_and_traceable_evidence(report_draft: dict) -> None:
    text = render_report(report_draft)
    assert "```text\nvlan 120\n```" in text
    assert "interface <INTERFACE_ID>\nswitchport access vlan 120" in text
    assert "show interfaces <INTERFACE_ID> switchport" in text
    assert "追加情報が必要 (needs_input)" in text
    assert "Target physical port." in text and "Manual restriction." in text
    assert "[source-1](#source-1) / Step 2 / 範囲内の表 1・行 3" in text
    assert "章内行: 10–20" in text and "OKF行: 100–110" in text
    assert "章先頭の物理ページ 35（手順のページではありません）" in text
    assert "PDF SHA-256: " + "b" * 64 in text
    assert "推論候補（根拠付き設定とは別・未検証）" in text
    assert "Saving was not requested." in text
    assert "Invalid binding shape." in text
    assert "実機投入不可" in text


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


@pytest.mark.parametrize("change", ["unknown_source", "unsafe_flags", "missing_cli", "missing_items"])
def test_report_rejects_malformed_or_unsafe_drafts(report_draft: dict, change: str) -> None:
    if change == "unknown_source":
        report_draft["sources"] = {}
    elif change == "unsafe_flags":
        report_draft["ready_to_apply"] = True
    elif change == "missing_cli":
        report_draft["items"][0]["configuration"][0].pop("cli")
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
