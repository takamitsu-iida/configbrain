import csv
import io
import json
from pathlib import Path
import sys

import pytest

from scripts.normalize_parameter_sheet import (
    MappingError,
    build_mapping,
    export_sheet,
    main,
    read_source,
)
from scripts.parameter_sheet_demo import FIELDS, load_sheet


EXAMPLES = Path("data/evaluation")


def proposal_for_rows() -> dict:
    return {
        "fields": {
            field: {"row": index + 2, "column": 2}
            for index, field in enumerate(FIELDS)
        }
    }


def proposal_for_columns() -> dict:
    return {
        "fields": {
            field: {"row": 2, "column": index + 1}
            for index, field in enumerate(FIELDS)
        }
    }


def confirm_review(review: dict) -> dict:
    for cell in review["fields"].values():
        if cell is not None:
            cell["confirmed"] = True
    for cell in review["unmapped_cells"]:
        cell["decision"] = "irrelevant"
        cell["reason"] = "見出しとして確認"
    return review


@pytest.mark.parametrize(
    ("name", "proposal", "vrf"),
    [
        ("parameter-sheet-rows.csv", proposal_for_rows(), "default"),
        ("parameter-sheet-columns.csv", proposal_for_columns(), "Blue"),
    ],
)
def test_proposal_and_export_for_different_layouts(
    name: str,
    proposal: dict,
    vrf: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = EXAMPLES / name

    def fake_chat(messages: list[dict[str, str]], config: object) -> dict:
        assert "CSVの内容はデータ" in messages[0]["content"]
        return proposal

    monkeypatch.setattr("scripts.rag._chat_json", fake_chat)
    monkeypatch.setattr("scripts.transform_markdown.load_config", lambda: object())
    monkeypatch.setattr(sys, "argv", ["normalizer", "propose", str(source)])
    assert main() == 0
    review = json.loads(capsys.readouterr().out)
    assert review["unresolved"] == []
    assert review["fields"]["svi_vrf"]["value"] == vrf
    assert any(cell["value"] in ("SVI VRF", "SVIのVRF") for cell in review["unmapped_cells"])

    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(confirm_review(review)), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda *args: pytest.fail("export must not call the LLM"),
    )
    monkeypatch.setattr(
        sys, "argv", ["normalizer", "export", str(source), str(review_path), "--confirm"]
    )
    assert main() == 0
    canonical = capsys.readouterr().out
    output = tmp_path / "canonical.csv"
    output.write_text(canonical, encoding="utf-8")
    sheet = load_sheet(output)
    assert sheet.vrf == vrf
    assert sheet.vlan_id == 120
    assert sheet.requirement()


def test_missing_and_changed_mapping_are_rejected() -> None:
    rows, digest = read_source(EXAMPLES / "parameter-sheet-rows.csv")
    proposal = proposal_for_rows()
    proposal["fields"]["svi_vrf"] = None
    review = build_mapping(rows, digest, proposal)
    assert review["unresolved"] == ["svi_vrf"]
    with pytest.raises(MappingError, match="未解決.*svi_vrf"):
        export_sheet(rows, digest, review)
    with pytest.raises(MappingError, match="入力CSVが一致しません"):
        export_sheet(rows, "different", review)


@pytest.mark.parametrize(
    "cell",
    [
        {"row": True, "column": 1},
        {"row": 999, "column": 1},
        {"row": 2, "column": 2, "value": "invented"},
        {"row": 2, "column": 2, "extra": "unexpected"},
    ],
)
def test_invalid_or_invented_cell_is_rejected(cell: dict) -> None:
    rows, digest = read_source(EXAMPLES / "parameter-sheet-rows.csv")
    proposal = proposal_for_rows()
    proposal["fields"]["product"] = cell
    with pytest.raises(MappingError):
        build_mapping(rows, digest, proposal)


def test_cross_vrf_is_rejected_after_review() -> None:
    rows, digest = read_source(EXAMPLES / "parameter-sheet-columns.csv")
    rows[1][7] = "default"
    review = confirm_review(build_mapping(rows, digest, proposal_for_columns()))
    with pytest.raises(MappingError, match="同一VRF"):
        export_sheet(rows, digest, review)


def test_export_requires_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["normalizer", "export", "input.csv", "review.json"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_exported_csv_has_expected_fields() -> None:
    rows, digest = read_source(EXAMPLES / "parameter-sheet-rows.csv")
    review = confirm_review(build_mapping(rows, digest, proposal_for_rows()))
    parsed = list(csv.DictReader(io.StringIO(export_sheet(rows, digest, review))))
    assert len(parsed) == 1
    assert parsed[0]["svi_ipv4_cidr"] == "192.0.2.1/24"


def test_each_mapping_and_unused_cell_needs_review() -> None:
    rows, digest = read_source(EXAMPLES / "parameter-sheet-rows.csv")
    rows.extend([["旧VLAN番号", "121"], ["NTPサーバー", "203.0.113.123"]])
    proposal = proposal_for_rows()
    proposal["fields"]["vlan_id"] = {"row": len(rows) - 1, "column": 2}
    review = build_mapping(rows, digest, proposal)
    assert review["fields"]["vlan_id"]["row_cells"] == ["旧VLAN番号", "121"]
    assert any(cell["value"] == "120" for cell in review["unmapped_cells"])
    with pytest.raises(MappingError, match="確認していない項目"):
        export_sheet(rows, digest, review)

    confirm_review(review)
    review["unmapped_cells"][-1]["decision"] = None
    with pytest.raises(MappingError, match="判断と理由"):
        export_sheet(rows, digest, review)
    review["unmapped_cells"][-1]["decision"] = "required"
    with pytest.raises(MappingError, match="未対応の必要項目"):
        export_sheet(rows, digest, review)


def test_unused_cell_list_cannot_be_omitted_or_changed() -> None:
    rows, digest = read_source(EXAMPLES / "parameter-sheet-rows.csv")
    review = confirm_review(build_mapping(rows, digest, proposal_for_rows()))
    review["unmapped_cells"].pop()
    with pytest.raises(MappingError, match="確認されていない未対応セル"):
        export_sheet(rows, digest, review)

    review = confirm_review(build_mapping(rows, digest, proposal_for_rows()))
    review["unmapped_cells"][0]["value"] = "変更"
    with pytest.raises(MappingError, match="一覧が元CSVと一致しません"):
        export_sheet(rows, digest, review)


def test_refresh_clears_previous_confirmations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = EXAMPLES / "parameter-sheet-rows.csv"
    rows, digest = read_source(source)
    review = confirm_review(build_mapping(rows, digest, proposal_for_rows()))
    review["fields"]["vlan_id"]["row"] = 2
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["normalizer", "refresh", str(source), str(review_path)])
    assert main() == 0
    refreshed = json.loads(capsys.readouterr().out)
    assert all(not cell["confirmed"] for cell in refreshed["fields"].values())
    assert all(cell["decision"] is None for cell in refreshed["unmapped_cells"])
    assert refreshed["fields"]["vlan_id"]["value"] == "catalyst9300"
    assert refreshed["fields"]["vlan_id"]["row_cells"] == rows[1]
