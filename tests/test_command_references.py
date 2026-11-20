import json
import subprocess
import sys
from pathlib import Path

import pytest

from configbrain.command_references import CommandReferenceIndex, extract_command_chapter
from configbrain.convert_manual import ConversionError, convert_manual, main as convert_main
from configbrain.search_commands import main as search_main


DOCUMENT_ID = "catalyst9300-26-x-reference"
BOOK_URL = (
    "https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/"
    "software/release/26-x/command_reference/book.html"
)
CHAPTER = {
    "order": 1, "title": "VLAN Commands",
    "url": BOOK_URL.removesuffix(".html") + "/vlan_commands.html",
}
HTML = b"""
<html><body><div id="chapterContent">
<h1 class="title topictitle1">VLAN Commands</h1>
<nav class="related-links"><ul>
  <li><a href="vlan_commands.html#vlan">vlan</a></li>
  <li><a href="vlan_commands.html#next">next command</a></li>
</ul></nav>
<article class="topic reference nested1" id="vlan">
  <h2 class="title topictitle2">vlan</h2>
  <section class="body refbody">
    <section><p>Enter VLAN configuration mode.</p></section>
    <section class="section refsyn"><p>vlan <var>id</var></p></section>
    <h2 class="sectiontitle">Syntax Description</h2>
    <table><tr><td>id</td><td>VLAN identifier</td></tr></table>
    <section class="section command_default" id="default">
      <h3 class="sectiontitle">Command Default</h3><p>None</p>
    </section>
    <section class="section command_modes" id="modes">
      <h3 class="sectiontitle">Command Modes</h3><p>Global configuration (config)</p>
    </section>
    <h2 class="sectiontitle">Command History</h2>
    <table><tr><td>26.x</td><td>Available</td></tr></table>
    <section class="section usage_guidelines" id="guidelines">
      <h3 class="sectiontitle">Usage Guidelines</h3>
      <p>Extended VLANs must retain default characteristics.</p>
      <p>These configuration commands are available in VLAN configuration mode.</p>
      <ul><li id="media">
        <p><span class="keyword kwd">media</span> selects a media type.</p>
        <table role="note"><tr><td>Only Ethernet ports are supported.</td></tr></table>
        <ul><li><p><span class="keyword kwd">ethernet</span>
          is the default media type.</p></li></ul>
      </li></ul>
    </section>
    <section class="example command_examples">
      <h2 class="sectiontitle">Examples</h2><pre>vlan 120\n exit</pre>
    </section>
  </section>
</article>
<article class="topic reference nested1" id="next">
  <h2 class="title topictitle2">next command</h2>
  <section class="body refbody">
    <h2 class="sectiontitle">Command Modes</h2>
    <p><span>Privileged EXEC</span><br/>User EXEC</p>
    <h2 class="sectiontitle">Examples</h2><pre>not part of vlan</pre>
  </section>
</article>
</div></body></html>
"""


@pytest.fixture
def reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "schema_version": 2, "product": "catalyst9300", "release": "26.x",
        "documents": [{
            "document_id": DOCUMENT_ID, "document_type": "command_reference",
            "title": "Command Reference", "filename": "book.pdf",
            "url": BOOK_URL.removesuffix(".html") + ".pdf",
            "html_book_url": BOOK_URL, "html_status": "available",
            "chapters": [CHAPTER],
        }],
    }), encoding="utf-8")
    root = tmp_path / "references"
    source = tmp_path / "no-pdfs"
    monkeypatch.setattr("configbrain.command_references.fetch_chapter_page", lambda *args: HTML)
    return catalog, root, source


def convert(reference: tuple[Path, Path, Path]) -> dict:
    catalog, root, source = reference
    return convert_manual(
        catalog, DOCUMENT_ID, source, root.parent / "okf", reference_output_root=root
    )


def test_conversion_preserves_command_boundaries_sections_and_child_context(
    reference: tuple[Path, Path, Path],
) -> None:
    catalog, root, source = reference
    result = convert(reference)
    assert result["command_count"] == 2
    assert result["subcommand_count"] == 1
    assert result["unparsed_count"] == result["warning_count"] == 0
    assert not source.exists()
    assert not (root.parent / "okf").exists()
    assert (root / DOCUMENT_ID / "html/1.html").read_bytes() == HTML

    index = CommandReferenceIndex.load(catalog, DOCUMENT_ID, root)
    found = index.search("  VLAN ", mode="config")
    assert found["status"] == "unique_match"
    command = found["matches"][0]["command"]
    assert command["source"]["url"] == CHAPTER["url"] + "#vlan"
    assert command["source"]["locator"] == "#chapterContent > article:nth-of-type(1)"
    assert "not part of vlan" not in command["content_html"]
    assert "<table>" in command["content_html"]
    assert "<pre>vlan 120\n exit</pre>" in command["content_html"]
    sections = {section["kind"]: section for section in command["sections"]}
    assert "None" in sections["command_default"]["text"]
    assert "ethernet" not in sections["command_default"]["text"]
    assert "default media type" in sections["usage_guidelines"]["text"]
    assert "VLAN identifier" in sections["syntax_description"]["text"]
    assert "26.x" in sections["command_history"]["text"]
    assert "not part of vlan" not in sections["examples"]["text"]

    child = index.search("media", parent="vlan", mode="VLAN configuration")
    assert child["status"] == "unique_match"
    assert child["evidence_type"] == "stored_html"
    assert child["sources"][0]["html_sha256"]
    match = child["matches"][0]
    assert match["matched_subcommand"]["parent_command_id"] == command["command_id"]
    assert match["matched_subcommand"]["source"]["url"].endswith("#media")
    assert "default media type" in match["matched_subcommand"]["text"]
    assert "Only Ethernet ports" in match["matched_subcommand"]["text"]
    assert "Extended VLANs" in match["command"]["content_html"]
    assert index.search("media")["status"] == "candidate"
    assert index.search("media", parent="other")["status"] == "not_found"
    assert index.search("media", mode="Global configuration")["status"] == "not_found"
    assert index.search("next command", mode="Privileged EXEC")["status"] == "unique_match"
    assert index.search("next command", mode="User EXEC")["status"] == "unique_match"
    assert convert(reference)["status"] == "unchanged"


def test_modes_disambiguate_distinct_entries_with_the_same_name() -> None:
    html = HTML.replace(b"next command</h2>", b"vlan (other mode)</h2>")
    data = extract_command_chapter(html, CHAPTER)
    index = CommandReferenceIndex({
        "document_id": DOCUMENT_ID, "product": "catalyst9300", "release": "26.x",
        "extractor_version": 1, "chapters": [data],
    })
    assert index.search("vlan")["status"] == "ambiguous"
    assert index.search("vlan", mode="Privileged EXEC")["status"] == "unique_match"
    assert index.search("vlan (other mode)")["status"] == "unique_match"
    assert data["warnings"][0]["reason"] == "toc_title_mismatch"


def test_duplicate_anchors_are_unparsed_not_searchable(
    reference: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = HTML.replace(b'id="next"', b'id="vlan"')
    monkeypatch.setattr("configbrain.command_references.fetch_chapter_page", lambda *args: html)
    data = extract_command_chapter(html, CHAPTER)
    assert not data["commands"]
    assert sum(item["reason"] == "duplicate_anchor" for item in data["unparsed"]) == 2
    with pytest.raises(ConversionError, match="No unambiguous"):
        convert(reference)
    assert not (reference[1] / DOCUMENT_ID).exists()


@pytest.mark.parametrize("html", [
    b"<html>No chapter content</html>",
    b'<div id="chapterContent"></div><div id="chapterContent"></div>',
])
def test_missing_or_ambiguous_chapter_content_is_rejected(html: bytes) -> None:
    with pytest.raises(ConversionError, match="chapterContent"):
        extract_command_chapter(html, CHAPTER)


def test_nested_containers_and_dangling_toc_targets_are_explicit(
    reference: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = HTML.replace(
        b'<article class="topic reference nested1" id="vlan">',
        b'<div><article class="topic reference nested1" id="vlan">',
    ).replace(b"</article>", b"</article></div>", 1)
    data = extract_command_chapter(html, CHAPTER)
    assert [command["name"] for command in data["commands"]] == ["next command"]
    assert data["unparsed"][0]["reason"] == "nested_command_container"
    monkeypatch.setattr("configbrain.command_references.fetch_chapter_page", lambda *args: html)
    assert convert(reference)["unparsed_count"] == 1
    index = CommandReferenceIndex.load(reference[0], DOCUMENT_ID, reference[1])
    assert index.search("vlan")["status"] == "unparsed"
    assert index.search("next command")["coverage"] == "partial"
    data = extract_command_chapter(
        HTML.replace(b'href="vlan_commands.html#next"', b'href="vlan_commands.html#missing"'),
        CHAPTER,
    )
    assert any(item["reason"] == "toc_target_missing" for item in data["unparsed"])
    assert len(data["commands"]) == 2


@pytest.mark.parametrize("failure", ["network", "parse"])
def test_failed_update_preserves_every_previous_file(
    reference: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    convert(reference)
    directory = reference[1] / DOCUMENT_ID
    before = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*")
              if path.is_file()}

    def fail(*args: object) -> bytes:
        raise ConversionError("network unavailable")

    monkeypatch.setattr(
        "configbrain.command_references.fetch_chapter_page",
        fail if failure == "network" else lambda *args: b"<html>not a chapter</html>",
    )
    with pytest.raises(ConversionError):
        convert(reference)
    assert before == {path.relative_to(directory): path.read_bytes()
                      for path in directory.rglob("*") if path.is_file()}


@pytest.mark.parametrize("modified", ["html", "records", "metadata"])
def test_search_rejects_modified_sources_records_and_scope(
    reference: tuple[Path, Path, Path], modified: str,
) -> None:
    convert(reference)
    directory = reference[1] / DOCUMENT_ID
    if modified == "html":
        (directory / "html/1.html").write_bytes(HTML + b"changed")
    else:
        path = directory / "commands.json"
        data = json.loads(path.read_text())
        if modified == "metadata":
            data["release"] = "different"
        else:
            data["chapters"][0]["commands"][0]["name"] = "fabricated command"
        path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConversionError, match="mismatch|do not match"):
        CommandReferenceIndex.load(reference[0], DOCUMENT_ID, reference[1])


def test_reference_cli_and_query_error_reporting(
    reference: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    catalog, root, _ = reference
    assert convert_main([
        "--catalog", str(catalog), "--document-id", DOCUMENT_ID,
        "--reference-output", str(root),
    ]) == 0
    assert "2 commands, 1 subcommands" in capsys.readouterr().out
    args = ["--catalog", str(catalog), "--document-id", DOCUMENT_ID,
            "--reference-root", str(root), "--command", "media",
            "--mode", "VLAN configuration", "--parent", "vlan"]
    assert search_main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "unique_match"
    process = subprocess.run(
        [sys.executable, "-m", "configbrain.search_commands", *args],
        check=True, capture_output=True, text=True,
    )
    assert json.loads(process.stdout)["matches"][0]["command"]["name"] == "vlan"
    assert search_main([*args, "--command", " "]) == 1
    assert "must not be blank" in capsys.readouterr().err
    (root / DOCUMENT_ID / "html/1.html").unlink()
    assert search_main(args) == 1
    assert "Could not load command reference" in capsys.readouterr().err
