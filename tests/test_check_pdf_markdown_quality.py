import json
from pathlib import Path
import sys

import pypdfium2
import pytest

from scripts.check_pdf_markdown_quality import (
    PdfLine,
    QualityError,
    build_report,
    main,
    markdown_commands,
    markdown_tables,
    pdf_commands,
)


def procedure() -> list[PdfLine]:
    return [
        PdfLine(1, "Command or Action Purpose", 100, 100, 94, "Command or Action"),
        PdfLine(1, "Step 1", 50, 90, 84),
        PdfLine(1, "interface vlan vlan-id Enter interface", 100, 90, 84, "interface vlan vlan-id"),
        PdfLine(1, "Device(config)# interface vlan 99", 100, 75, 70),
        PdfLine(1, "Step 2", 50, 60, 54),
        PdfLine(1, "ip address ip-address subnet-mask Set address", 100, 60, 54,
                "ip address ip-address subnet-mask"),
        PdfLine(1, "Device(config-if)# ip address", 100, 45, 40),
        PdfLine(1, "10.0.0.1 255.255.255.0", 101, 37, 32),
    ]


def test_pdf_command_continuations_keep_page_and_uncertainty() -> None:
    found = pdf_commands(procedure())
    assert found == [
        {"command": "interface vlan 99", "first_line": "interface vlan 99", "page": 1,
         "continuation_uncertain": False},
        {"command": "ip address 10.0.0.1 255.255.255.0", "first_line": "ip address",
         "page": 1, "continuation_uncertain": True},
    ]


def test_detects_missing_added_and_misplaced_command_syntax() -> None:
    markdown = [
        "| Step | Command or Action | Purpose |",
        "| --- | --- | --- |",
        "| Step 1 | ip address ip-address subnet-mask | Interface |",
        "| Step 2 | interface vlan vlan-id | Address |",
        "```console",
        "Device(config)# interface vlan 99",
        "Device(config-if)# ip address",
        "shutdown",
        "```",
    ]
    report = build_report(procedure(), [], markdown, 10)
    findings = report["command_findings"]
    assert any(item["kind"] == "missing_or_truncated" and "10.0.0.1" in item["command"]
               for item in findings)
    assert any(item["kind"] == "possible_truncated_continuation" and item["command"] == "ip address"
               for item in findings)
    assert any(item["kind"] == "added_or_duplicated" and item["command"] == "shutdown"
               and item["markdown_lines"] == [17] for item in findings)
    assert {item["step"] for item in report["table_findings"]
            if item["kind"] == "possible_step_command_mismatch"} == {"1", "2"}
    assert report["status"] == "review_required"


def test_consistent_example_is_not_reported_as_changed() -> None:
    markdown = [
        "| Step | Command or Action | Purpose |",
        "| --- | --- | --- |",
        "| Step 1 | interface vlan vlan-id | Enter interface |",
        "| Step 2 | ip address ip-address subnet-mask | Set address |",
        "```console",
        "Device(config)# interface vlan 99",
        "Device(config-if)# ip address 10.0.0.1 255.255.255.0",
        "```",
    ]
    lines = procedure()[:-2] + [
        PdfLine(1, "Device(config-if)# ip address 10.0.0.1 255.255.255.0", 100, 45, 40),
    ]
    report = build_report(lines, [], markdown, 1)
    assert report["status"] == "no_anomalies_detected"
    assert not report["command_findings"]
    assert not report["table_findings"]
    assert not report["step_findings"]


def test_detects_broken_table_and_missing_or_reordered_steps() -> None:
    broken = [
        "| Step | Command or Action Purpose | extra | extra |",
        "| --- | --- | --- | --- |",
        "| Step 2 | ip address ip-address subnet-mask |",
        "| Step 1 | interface vlan vlan-id | x | x |",
        "```console",
        "Device(config)# interface vlan 99",
        "Device(config-if)# ip address 10.0.0.1 255.255.255.0",
        "```",
    ]
    report = build_report(procedure(), [], broken, 1)
    kinds = {item["kind"] for item in report["table_findings"]}
    assert {"inconsistent_column_count", "merged_command_purpose_header",
            "possible_fragmented_procedure_table"} <= kinds
    assert report["step_findings"][0]["kind"] == "possible_reordered_steps"
    missing = build_report(procedure(), [], broken[:3], 1)
    assert any(item.get("step") == "1" and item["markdown_count"] == 0
               for item in missing["step_findings"])


def test_missing_table_syntax_is_reported_even_without_a_prompt() -> None:
    report = build_report(procedure(), [], [
        "| Step | Command or Action | Purpose |",
        "| --- | --- | --- |",
        "| Step 1 | interface vlan vlan-id | Enter interface |",
        "| Step 2 | omitted | Set address |",
        "```console",
        "Device(config)# interface vlan 99",
        "Device(config-if)# ip address 10.0.0.1 255.255.255.0",
        "```",
    ], 1)
    assert any(item["kind"] == "possible_missing_table_command" and item["step"] == "Step 2"
               for item in report["table_findings"])


def test_step_labels_in_command_cells_still_validate_command_association() -> None:
    markdown = [
        "| Command or Action | Example | Purpose |",
        "| --- | --- | --- |",
        "| Step 1 ip address ip-address subnet-mask | Device(config)# interface vlan 99 | Interface |",
        "| Step 2 interface vlan vlan-id | Device(config-if)# ip address 10.0.0.1 255.255.255.0 | Address |",
    ]
    report = build_report(procedure(), [], markdown, 1)
    assert {item["step"] for item in report["table_findings"]
            if item["kind"] == "possible_step_command_mismatch"} == {"1", "2"}
    assert not report["step_findings"]
    assert not report["command_findings"]


def test_tables_inside_code_and_invalid_fence_are_not_silently_accepted() -> None:
    lines = ["```text", "| A | B |", "| --- | --- |", "```"]
    assert markdown_tables(lines, 1) == []
    with pytest.raises(QualityError, match="閉じていない"):
        markdown_commands(["```console", "Device# enable"], 1)


def test_cli_empty_pdf_page_is_unverifiable_and_output_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "source.pdf"
    markdown = tmp_path / "manual.md"
    output = tmp_path / "report.json"
    document = pypdfium2.PdfDocument.new()
    document.new_page(200, 200).close()
    document.save(pdf)
    document.close()
    markdown.write_text("No readable example.\n", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv",
        ["check_pdf_markdown_quality", str(pdf), str(markdown), "--pdf-pages", "1:1",
         "--markdown-lines", "1:1", "--output", str(output)],
    )
    assert main() == 2
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "review_required"
    assert any("テキストレイヤー" in issue for issue in result["limitations"])
    original = output.read_bytes()
    assert main() == 1
    assert output.read_bytes() == original


def test_cli_rejects_out_of_range_input_without_creating_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "source.pdf"
    markdown = tmp_path / "manual.md"
    output = tmp_path / "report.json"
    document = pypdfium2.PdfDocument.new()
    document.new_page(200, 200).close()
    document.save(pdf)
    document.close()
    markdown.write_text("text\n", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv",
        ["check_pdf_markdown_quality", str(pdf), str(markdown), "--pdf-pages", "1:1",
         "--markdown-lines", "1:2", "--output", str(output)],
    )
    assert main() == 1
    assert not output.exists()
