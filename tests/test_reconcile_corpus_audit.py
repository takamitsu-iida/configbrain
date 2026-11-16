import json
from pathlib import Path

import pytest

from scripts.check_pdf_markdown_quality import PdfLine, QualityError
from scripts.reconcile_corpus_audit import (
    command_scope, contains_command, reconcile_command, reconcile_table,
    run_reconciliation, source_command, table_pdf_pages, text_matches,
)


def source() -> list[PdfLine]:
    return [
        PdfLine(2, "Previous Procedure", 100, 700, 690),
        PdfLine(2, "Device(config)# vlan 99 Enables a VLAN.", 100, 600, 590,
                "Device(config)# vlan 99"),
        PdfLine(2, "Following Procedure", 100, 400, 390),
    ]


def manual(body: str) -> list[str]:
    return [
        '# <span id="page-1-0"></span>Previous Procedure',
        body,
        '# <span id="page-1-1"></span>Following Procedure',
        "Device(config)# vlan 99",
    ]


def candidate() -> tuple[dict, dict]:
    return (
        {"page": 2, "mappings": {"extracted": {"markdown_lines": [1, 2]}}},
        {"command": "vlan 99 Enables a VLAN.", "markdown_lines": [1, 2]},
    )


def test_column_contamination_is_dismissed_only_with_local_intact_example() -> None:
    page, finding = candidate()
    result = reconcile_command(page, finding, source(),
                               manual("Device(config)# vlan 99"), manual("Device(config)# vlan 99"))
    assert result["verdict"] == "audit_false_positive"
    assert result["reason"] == "purpose_column_contamination"
    assert result["pdf_command"]["command"] == "vlan 99"


def test_same_command_in_another_section_does_not_dismiss_missing_example() -> None:
    page, finding = candidate()
    result = reconcile_command(page, finding, source(), manual("Example is missing."),
                               manual("Example is missing."))
    assert result["verdict"] == "confirmed_text_difference"
    assert result["likely_stage"] == "pdf_to_extracted"
    assert result["package_scope"]["lines"] == [1, 2]


def test_cell_fragmentation_and_prompt_loss_are_not_reported_as_intact() -> None:
    page, finding = candidate()
    for body, reason in [
        ("| Device(config)# vlan | 99 |", "command_split_across_table_cells_or_rows"),
        ("| Step 1 | `vlan 99` |", "example_prompt_or_block_structure_lost"),
    ]:
        result = reconcile_command(page, finding, source(), manual(body), manual(body))
        assert result["verdict"] == "confirmed_formatting_difference"
        assert result["reason"] == reason


def test_numeric_punctuation_and_prose_do_not_match_as_commands() -> None:
    assert not contains_command("Device# vlan 999", "vlan 99")
    assert not contains_command("Device# ping 101.1.1", "ping 10.1.1.1")
    assert not text_matches(["Use vlan 99 in this feature."], [1, 1], "vlan 99")[0]
    assert contains_command("Device# show bgp * 239.1.1.1", "show bgp * 239.1.1.1")


def test_multiple_matching_examples_and_missing_headings_remain_unresolved() -> None:
    page, finding = candidate()
    duplicated = manual("Device(config)# vlan 99\nDevice(config)# vlan 99")
    duplicated = "\n".join(duplicated).splitlines()
    result = reconcile_command(page, finding, source(), duplicated, duplicated)
    assert result["verdict"] == "unresolved"
    result = reconcile_command(page, finding, source(), ["No headings or example."],
                               ["No headings or example."])
    assert result["verdict"] == "unresolved"


def test_pdf_command_and_output_join_is_not_a_confirmed_loss() -> None:
    lines = [
        PdfLine(2, "Previous Procedure", 100, 700, 690),
        PdfLine(2, "Leaf# show ipv6 mld vlan 101IP address :", 100, 600, 590),
        PdfLine(2, "Following Procedure", 100, 400, 390),
    ]
    page, finding = candidate()
    finding["command"] = "show ipv6 mld vlan 101IP address :"
    target = "\n".join(manual("Leaf# show ipv6 mld vlan 101\n```text\nIP address :\n```")).splitlines()
    result = reconcile_command(page, finding, lines, target, target)
    assert result["verdict"] == "audit_false_positive"
    assert result["package_matches"][0]["multiline"] is True


def test_output_header_matching_prompt_regex_is_not_a_command() -> None:
    lines = [PdfLine(2, "Port# Port Length OK", 100, 600, 590)]
    page, finding = candidate()
    finding["command"] = "Port Length OK"
    result = reconcile_command(page, finding, lines, [], [])
    assert result["verdict"] == "audit_false_positive"
    assert result["reason"] == "output_header_misidentified_as_prompt"


def test_alternative_audit_context_prevents_claiming_absence_from_moved_heading() -> None:
    page, finding = candidate()
    target = ["Device(config)# vlan 99"] + manual("No example here.")[:3]
    finding["markdown_lines"] = [1, 4]
    result = reconcile_command(page, finding, source(), target, target)
    assert result["verdict"] == "unresolved"
    assert result["alternative_context_matches"]


def test_missing_text_without_a_known_pdf_command_column_remains_unresolved() -> None:
    page, finding = candidate()
    pdf_lines = [
        PdfLine(line.page, line.text, line.x, line.top, line.bottom) for line in source()
    ]
    result = reconcile_command(page, finding, pdf_lines, manual("No example."),
                               manual("No example."))
    assert result["verdict"] == "unresolved"


def test_syntax_row_does_not_make_an_intact_prompt_example_ambiguous() -> None:
    target = ["| Step 1 | `vlan 99` | Purpose |", "| Device(config)# vlan 99 | |"]
    intact, fragmented = text_matches(target, [1, 2], "vlan 99")
    assert len(intact) == 1
    assert intact[0]["line"] == 2
    assert not fragmented


def test_command_wrap_is_recorded_with_coordinate_evidence() -> None:
    lines = [
        PdfLine(1, "Device# copy running-config", 100, 100, 94, "Device# copy running-config"),
        PdfLine(1, "startup-config", 100, 89, 83, "startup-config"),
    ]
    original, record = source_command(lines, "copy running-config")
    assert original == lines[0]
    assert record["command"] == "copy running-config startup-config"
    assert record["continuation_uncertain"] is True


def test_table_variation_keeps_row_and_pdf_evidence_without_claiming_text_loss() -> None:
    lines = [
        "| Command or Action | Purpose |",
        "| --- | --- |",
        "| Device# show configuration |",
        "| ```console |",
        "| |",
    ]
    finding = {
        "line": 1, "columns": 2, "separator_columns": 2,
        "row_width_mismatches": [
            {"line": number, "columns": 1} for number in [3, 4, 5]
        ],
    }
    result = reconcile_table(finding, lines, {
        1: [PdfLine(1, "Device# show configuration", 100, 100, 94)],
    }, [1])
    assert result["verdict"] == "confirmed_column_count_variation"
    assert result["source_correspondence"] == "cell_text_matched"
    assert result["row_classifications"] == {"short_row": 1, "embedded_code_fence": 1, "empty_stub": 1}
    assert result["markdown_lines"] == [1, 5]
    assert result["pdf_matched_pages"] == [1]


def test_table_page_selection_prefers_anchor_spans_to_broad_context() -> None:
    pages = [
        {"page": 1, "mappings": {"package": {"anchor_lines": [1, 5], "markdown_lines": [1, 30]}}},
        {"page": 2, "mappings": {"package": {"anchor_lines": [10, 20], "markdown_lines": [4, 40]}}},
    ]
    assert table_pdf_pages(pages, [12, 16]) == [2]


def test_refuses_stale_audit_and_existing_output(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "manifest.json").write_text(json.dumps({
        "status": "running", "data_root": str(tmp_path / "product"),
    }), encoding="utf-8")
    output = tmp_path / "result"
    with pytest.raises(QualityError, match="completed"):
        run_reconciliation(audit, output)
    assert not output.exists()
