import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts.compare_html_rules import (
    ComparisonError,
    main,
    parse_html,
    render_fragment,
    select_fragment,
    summarize,
)


HTML = b"""\
<!doctype html>
<html><body><section id="procedure">
<h2>Configure a port</h2>
<!-- not source text -->
<p>Use <b>interface interface-id</b> in global configuration mode.</p>
<table><caption>Steps</caption><tr><th>Command</th><th>Purpose</th></tr>
<tr><td rowspan="2">interface interface-id<br><pre>Device(config)# interface Gi1
Device(config-if)# switchport mode access</pre></td><td>Enter the interface</td></tr>
</table><img src="diagram.png"></section></body></html>
"""


def baseline() -> dict:
    return {
        "status": "candidate",
        "source": {
            "document_id": "guide",
            "citation_id": "guide:section-0001",
            "title": "Guide",
            "product": "catalyst9300",
            "release": "26.x",
            "source_url": "https://example.test/guide.pdf",
        },
        "rules": [{
            "commands": [{
                "syntax": "interface interface-id",
                "syntax_check": {
                    "in_section": True, "in_manual": True, "syntax_in_quote": True,
                },
                "mode": None,
            }],
        }],
        "issues": ["mode unknown"],
    }


def proposal() -> dict:
    return {
        "rules": [{
            "purpose": "Configure a port",
            "commands": [{
                "syntax": "interface interface-id",
                "syntax_quote": "interface interface-id",
                "mode": "global configuration",
                "mode_quote": "global configuration mode",
                "parameters": [],
            }],
            "conditions": [],
            "unresolved": [],
        }],
        "unresolved": [],
    }


def test_html_structure_and_selection_are_preserved() -> None:
    soup = parse_html(HTML)
    body, warnings = render_fragment(select_fragment(soup, "#procedure"))
    assert "Use interface interface-id in global configuration mode." in body
    assert "[caption]\nSteps\n[/caption]" in body
    assert "[row]\n[td rowspan=2]\ninterface interface-id" in body
    assert "Device(config)# interface Gi1\nDevice(config-if)# switchport mode access" in body
    assert "not source text" not in body
    assert warnings
    with pytest.raises(ComparisonError, match="要素1件"):
        select_fragment(soup, "td")
    with pytest.raises(ComparisonError, match="入れ子"):
        render_fragment(select_fragment(
            parse_html(b'<table id="outer"><tr><td><table></table></td></tr></table>'),
            "#outer",
        ))


def test_preview_does_not_call_llm_and_reports_selected_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    html = tmp_path / "guide.html"
    pdf = tmp_path / "pdf.json"
    html.write_bytes(HTML)
    pdf.write_text(json.dumps(baseline()), encoding="utf-8")
    monkeypatch.setattr("scripts.compare_html_rules.load_config", lambda: pytest.fail("unexpected LLM"))
    monkeypatch.setattr(
        sys, "argv",
        ["compare_html_rules", str(html), "--selector", "#procedure", "--pdf-result", str(pdf),
         "--source-url", "https://example.test/guide.html", "--preview"],
    )
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["pdf_source"]["citation_id"] == "guide:section-0001"
    assert "[td rowspan=2]" in result["normalized_text"]
    assert result["warnings"]


def test_comparison_uses_existing_extractor_and_preserves_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = tmp_path / "guide.html"
    pdf = tmp_path / "pdf.json"
    output = tmp_path / "comparison.json"
    html.write_bytes(HTML)
    pdf.write_text(json.dumps(baseline()), encoding="utf-8")
    seen = []

    def fake_chat(messages, config):
        seen.append(messages)
        return proposal()

    monkeypatch.setattr("scripts.compare_html_rules.load_config", lambda: SimpleNamespace(model="test-model"))
    monkeypatch.setattr("scripts.extract_rule_candidates._chat_json", fake_chat)
    monkeypatch.setattr(
        sys, "argv",
        ["compare_html_rules", str(html), "--selector", "#procedure", "--pdf-result", str(pdf),
         "--source-url", "https://example.test/guide.html", "--output", str(output)],
    )
    assert main() == 2  # The image must be reviewed.
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["pdf_candidates"] == baseline()
    assert result["summary"]["pdf"]["mode_unknown"] == 1
    assert result["summary"]["html"]["mode_unknown"] == 0
    assert result["html_candidates"]["rules"][0]["commands"][0]["syntax_check"]["in_manual"]
    assert result["html_candidates"]["source"]["selector"] == "#procedure"
    assert "descriptor_sha256" not in result["html_candidates"]["source"]
    assert len(seen) == 1
    assert "[td rowspan=2]" in json.loads(seen[0][-1]["content"])["body"]
    assert main() == 1
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("html", "syntax"),
    [
        ("<p>vlan <i>vlan-id</i></p>", "vlan vlan-id"),
        ("<table><tr><td>switchport mode access</td><td>Access port</td></tr></table>",
         "switchport mode access"),
        ("<pre>interface vlan 99\n ip address 192.0.2.1 255.255.255.0</pre>",
         "interface vlan 99\n ip address 192.0.2.1 255.255.255.0"),
        ("<p><b>ip helper-address</b> <i>address</i></p>", "ip helper-address address"),
    ],
)
def test_representative_feature_syntax_survives_rendering(html: str, syntax: str) -> None:
    soup = parse_html(f"<section>{html}</section>".encode())
    body, _ = render_fragment(select_fragment(soup, "section"))
    assert syntax in body


def test_malformed_baseline_is_rejected_before_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    html = tmp_path / "guide.html"
    pdf = tmp_path / "pdf.json"
    output = tmp_path / "comparison.json"
    html.write_bytes(HTML)
    broken = baseline()
    broken["rules"][0]["commands"][0]["syntax_check"] = {}
    pdf.write_text(json.dumps(broken), encoding="utf-8")
    monkeypatch.setattr("scripts.compare_html_rules.load_config", lambda: pytest.fail("unexpected LLM"))
    monkeypatch.setattr(
        sys, "argv",
        ["compare_html_rules", str(html), "--selector", "#procedure", "--pdf-result", str(pdf),
         "--source-url", "https://example.test/guide.html", "--output", str(output)],
    )
    assert main() == 1
    assert not output.exists()
    assert "不正" in capsys.readouterr().err
    with pytest.raises(ComparisonError):
        summarize(broken)
