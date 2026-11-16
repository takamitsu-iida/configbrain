import hashlib
import json
from pathlib import Path
import sys

import pytest

from scripts.extract_rule_candidates import (
    RuleExtractionError,
    build_candidates,
    main,
    section_body,
)
from scripts.rag import split_markdown_sections


MANUAL = """\
# Configuration Guide

## Configuring ports

Use the interface interface-id command in global configuration mode.
Then use switchport mode access in interface configuration mode.
"""


def source():
    section = split_markdown_sections(
        MANUAL, "guide", "Configuration Guide", "26",
        product="catalyst9300", release="26.x", source_url="https://example.test/guide.pdf",
    )[0]
    return section


def proposal() -> dict:
    return {
        "rules": [{
            "purpose": "Configure a port",
            "commands": [{
                "syntax": "interface interface-id",
                "syntax_quote": "interface interface-id",
                "mode": "global configuration",
                "mode_quote": "global configuration mode",
                "parameters": [{
                    "name": "interface-id",
                    "description": "interface identifier",
                    "quote": "interface interface-id",
                }],
            }],
            "conditions": [{
                "description": "Uses interface configuration mode",
                "quote": "interface configuration mode",
            }],
            "unresolved": [],
        }],
        "unresolved": [],
    }


def test_extraction_records_unverified_candidates_and_exact_provenance() -> None:
    result = build_candidates(
        source(), MANUAL, hashlib.sha256(MANUAL.encode()).hexdigest(), "descriptor-hash",
        proposal(),
    )
    command = result["rules"][0]["commands"][0]
    assert result["status"] == result["rules"][0]["status"] == "candidate"
    assert result["source"]["citation_id"] == "guide:section-0001"
    assert result["source"]["product"] == "catalyst9300"
    assert result["source"]["release"] == "26.x"
    assert result["source"]["source_url"] == "https://example.test/guide.pdf"
    assert result["source"]["manual_sha256"] == hashlib.sha256(MANUAL.encode()).hexdigest()
    assert all(command["syntax_check"].values())
    assert all(command["mode_check"].values())
    assert all(command["parameters"][0]["quote_check"].values())
    assert result["issues"] == []
    assert "保証しません" in result["notice"]


def test_extraction_reports_fabricated_quote_and_unrelated_parameter() -> None:
    value = proposal()
    command = value["rules"][0]["commands"][0]
    command["syntax_quote"] = "interface interface-id in fabricated text"
    command["parameters"][0]["name"] = "vlan-id"
    result = build_candidates(source(), MANUAL, "manual-hash", "descriptor-hash", value)
    assert not result["rules"][0]["commands"][0]["syntax_check"]["in_manual"]
    assert not result["rules"][0]["commands"][0]["parameters"][0]["quote_check"]["name_in_syntax"]
    assert len(result["issues"]) == 2
    assert result["status"] == "candidate"


def test_heading_added_by_index_cannot_be_used_as_source_quote() -> None:
    value = proposal()
    value["rules"][0]["commands"][0]["syntax_quote"] = (
        "Configuring ports\n\n## Configuring ports\n\nUse the interface interface-id"
    )
    result = build_candidates(source(), MANUAL, "hash", "hash", value)
    assert not result["rules"][0]["commands"][0]["syntax_check"]["in_section"]
    assert result["issues"]
    assert not section_body(source()).startswith("Configuring ports\n\n")


def test_missing_mode_is_explicitly_unresolved() -> None:
    value = proposal()
    command = value["rules"][0]["commands"][0]
    command["mode"] = None
    command["mode_quote"] = None
    result = build_candidates(source(), MANUAL, "hash", "hash", value)
    assert any("モードが不明" in issue for issue in result["issues"])


def test_invalid_response_is_rejected() -> None:
    value = proposal()
    value["rules"][0]["commands"][0]["mode_quote"] = None
    with pytest.raises(RuleExtractionError, match="mode_quote"):
        build_candidates(source(), MANUAL, "hash", "hash", value)


def test_cli_saves_candidate_and_reports_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "candidates.json"
    value = proposal()
    value["unresolved"] = ["Related sections were not supplied"]
    monkeypatch.setattr(
        "scripts.extract_rule_candidates.load_source",
        lambda path: ([source()], MANUAL, "manual-hash", "descriptor-hash"),
    )
    monkeypatch.setattr("scripts.extract_rule_candidates.load_config", lambda: object())
    monkeypatch.setattr("scripts.extract_rule_candidates.extract_candidates", lambda section, config: value)
    monkeypatch.setattr(
        sys, "argv",
        ["extract_rule_candidates", str(tmp_path), "--section", "guide:section-0001", "--output", str(output)],
    )

    assert main() == 2
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["unresolved"] == ["Related sections were not supplied"]
    assert saved["status"] == "candidate"
    assert "未解決" in capsys.readouterr().err
    original = output.read_text(encoding="utf-8")
    assert main() == 1
    assert output.read_text(encoding="utf-8") == original


def test_list_sections_never_calls_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "scripts.extract_rule_candidates.load_source",
        lambda path: ([source()], MANUAL, "manual-hash", "descriptor-hash"),
    )
    def fail() -> None:
        raise AssertionError("LLM must not be used to list sections")
    monkeypatch.setattr("scripts.extract_rule_candidates.load_config", fail)
    monkeypatch.setattr(
        sys, "argv",
        ["extract_rule_candidates", str(tmp_path), "--list-sections", "--heading-contains", "ports"],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)[0]["citation_id"] == "guide:section-0001"


def test_invalid_llm_response_does_not_create_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "candidates.json"
    monkeypatch.setattr(
        "scripts.extract_rule_candidates.load_source",
        lambda path: ([source()], MANUAL, "manual-hash", "descriptor-hash"),
    )
    monkeypatch.setattr("scripts.extract_rule_candidates.load_config", lambda: object())
    monkeypatch.setattr(
        "scripts.extract_rule_candidates.extract_candidates",
        lambda section, config: {"rules": "not an array", "unresolved": []},
    )
    monkeypatch.setattr(
        sys, "argv",
        ["extract_rule_candidates", str(tmp_path), "--section", "guide:section-0001", "--output", str(output)],
    )
    assert main() == 1
    assert not output.exists()
    assert "rules" in capsys.readouterr().err


def test_real_package_to_candidate_with_mocked_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "guide"
    package.mkdir()
    manual = package / "manual.md"
    manual.write_text(MANUAL, encoding="utf-8")
    descriptor = {
        "profile": "data-package",
        "name": "guide",
        "title": "Configuration Guide",
        "version": "26",
        "configbrain": {"catalogProduct": "catalyst9300", "release": "26.x"},
        "resources": [{
            "name": "manual",
            "path": "manual.md",
            "mediatype": "text/markdown",
            "bytes": manual.stat().st_size,
            "hash": "sha256:" + hashlib.sha256(manual.read_bytes()).hexdigest(),
        }],
    }
    descriptor_path = package / "datapackage.json"
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    captured: list[dict[str, str]] = []

    def fake_chat(messages, config):
        captured.extend(messages)
        return proposal()

    monkeypatch.setattr("scripts.extract_rule_candidates._chat_json", fake_chat)
    monkeypatch.setattr("scripts.extract_rule_candidates.load_config", lambda: object())
    output = tmp_path / "candidates.json"
    monkeypatch.setattr(
        sys, "argv",
        ["extract_rule_candidates", str(package), "--section", "guide:section-0001", "--output", str(output)],
    )

    assert main() == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["source"]["manual_sha256"] == hashlib.sha256(manual.read_bytes()).hexdigest()
    assert result["source"]["descriptor_sha256"] == hashlib.sha256(descriptor_path.read_bytes()).hexdigest()
    assert result["rules"][0]["commands"][0]["syntax"] == "interface interface-id"
    assert "interface interface-id" in captured[-1]["content"]
