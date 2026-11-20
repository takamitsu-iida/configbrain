import hashlib
import json
from pathlib import Path

import pytest

from configbrain.convert_manual import convert_manual
from configbrain.expand_context import ContextError, expand_chapter, expand_context, main
from configbrain.index_manuals import (
    EMBEDDING_MODEL,
    ManualChapter,
    _context,
    _dataset_fingerprint,
    extract_text_blocks,
    load_validated_manuals,
)


HTML = b"""
<html><body>
<div id="chapterTitleDiv"><h1>Configuring VLANs</h1></div>
<div id="chapterContent">
  <h2>Prerequisites for VLANs</h2><p>Chapter prerequisite.</p>
  <h2>Restrictions for VLANs</h2><p>Chapter restriction.</p>
  <h2>Information About VLANs</h2>
    <h3>VLAN Configuration Guidelines</h3><p>Chapter guideline.</p>
  <h2>How to Configure VLANs</h2><p>Ancestor introduction.</p>
    <h3>Assigning Ports</h3><p>Task introduction.</p>
      <h4>Before you begin</h4><p>Task prerequisite.</p>
      <h4>Procedure</h4>
      <table><thead><tr><th>Step</th><th>Command</th></tr></thead>
      <tbody><tr><td>1</td><td>first-command</td></tr>
      <tr><td>2</td><td>middle-command</td></tr>
      <tr><td>3</td><td>last-command</td></tr></tbody></table>
      <p>Inline warning after the table.</p>
      <pre><code>show example
  indented-output</code></pre>
      <h4>What to do next</h4><p>Verify the result.</p>
    <h3>Other Task</h3>
      <h4>Before you begin</h4><p>Unrelated prerequisite.</p>
      <h4>Procedure</h4><p>Unrelated command.</p>
      <h4>Warning</h4><p>Unrelated task warning.</p>
</div></body></html>
"""


@pytest.fixture
def context_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path, Path]:
    catalog = tmp_path / "catalog.json"
    book = "https://www.cisco.com/c/en/us/td/docs/switches/catalyst9300/release/26-x/test/guide"
    url = book + "/configuring_vlans.html"
    catalog.write_text(json.dumps({
        "schema_version": 1, "product": "catalyst9300", "release": "26.x",
        "documents": [{
            "document_id": "vlan-guide", "title": "VLAN guide",
            "category": "Configuration Guides", "filename": "guide.pdf",
            "url": book + ".pdf", "html_book_url": book + ".html",
            "html_status": "available",
            "chapters": [{"order": 1, "title": "Configuring VLANs", "url": url}],
        }],
    }))
    pdf_root = tmp_path / "pdf"
    pdf = pdf_root / "vlan-guide" / "guide.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.7\nsynthetic test document\n")
    monkeypatch.setattr("configbrain.convert_manual.fetch_chapter_page", lambda *args: HTML)
    monkeypatch.setattr(
        "configbrain.convert_manual.map_chapters_to_pdf_pages", lambda *args: (20, {1: [5]}),
    )
    okf = tmp_path / "okf"
    convert_manual(catalog, "vlan-guide", pdf_root, okf)
    documents = load_validated_manuals(catalog, okf, pdf_root)
    document = documents[0]
    chapter = document.chapters[0]
    generation = _dataset_fingerprint(documents, EMBEDDING_MODEL)
    block = next(b for b in extract_text_blocks(chapter.markdown) if "middle-command" in b.text)
    text = f"passage: {_context(document, chapter, block.section_path)}\n\n{block.text}"
    hit = {
        "point_id": "test-point", "rank": 1, "score": 0.9,
        "document_id": document.document_id, "document_title": document.title,
        "product": document.product, "release": document.release,
        "source_pdf_sha256": document.source_pdf_sha256,
        "chapter_order": chapter.order, "chapter_title": chapter.title,
        "chapter_url": chapter.html_url, "html_content_sha256": chapter.html_content_sha256,
        "pdf_page_index": chapter.pdf_page_index, "pdf_mapping_status": chapter.pdf_mapping_status,
        "pdf_page_scope": "chapter_start", "dataset_fingerprint": generation,
        "embedding_model": EMBEDDING_MODEL, "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "section_path": list(block.section_path), "source_kind": block.kind,
        "source_refs": list(block.source_refs), "chunk_index": 1,
    }
    candidate = {
        "document_id": document.document_id, "chapter_order": chapter.order,
        "chapter_title": chapter.title, "chapter_url": chapter.html_url,
        "reason": "Relevant.", "uncertainty": "Not yet verified.",
        "status": "retrieved", "selection_rank": 1, "matches": [hit],
    }
    results = tmp_path / "search.json"
    results.write_text(json.dumps({
        "schema_version": 1, "product": document.product, "release": document.release,
        "requirement": "Assign an access port", "embedding_model": EMBEDDING_MODEL,
        "evidence_status": "candidates_only", "dataset_fingerprint": generation,
        "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
        "items": [
            {"requirement": "Assign port", "uncertainty": "Needs review.",
             "status": "retrieved", "chapters": [candidate]},
            {"requirement": "Unknown feature", "uncertainty": "No chapter.",
             "status": "no_candidates", "chapters": []},
        ],
    }))
    return results, catalog, okf, pdf_root


def test_recovers_full_task_and_context_from_a_single_middle_step(
    context_data: tuple[Path, Path, Path, Path],
) -> None:
    results, _, okf, _ = context_data
    original = json.loads(results.read_bytes())
    assert "first-command" not in original["items"][0]["chapters"][0]["matches"][0]["text"]
    expanded = expand_context(*context_data)
    chapter = expanded["items"][0]["chapters"][0]
    contexts = chapter["contexts"]
    task = next(context for context in contexts if "task" in context["roles"])
    text = task["markdown"]
    assert all(value in text for value in (
        "Task prerequisite.", "first-command", "middle-command", "last-command",
        "Inline warning after the table.", "Verify the result.", "  indented-output",
        "<table>", "</table>", "```",
    ))
    assert text.index("first-command") < text.index("middle-command") < text.index("last-command")
    joined = "\n".join(context["markdown"] for context in contexts)
    assert all(value in joined for value in (
        "Chapter prerequisite.", "Chapter restriction.", "Chapter guideline.",
        "Ancestor introduction.",
    ))
    assert "Unrelated" not in joined
    manual = (okf / "vlan-guide" / "manual.md").read_text().splitlines(keepends=True)
    for context in contexts:
        actual = "".join(manual[context["manual_line_start"] - 1:context["manual_line_end"]])
        assert actual.rstrip("\n") == context["markdown"].rstrip("\n")
    assert set(chapter["matches"][0]["context_ids"]) == {c["context_id"] for c in contexts}
    assert chapter["pdf_page_scope"] == "chapter_start"
    assert chapter["pdf_page_index"] == 5
    assert expanded["pdf_verification_status"] == "not_verified"
    assert expanded["evidence_status"] == "candidates_only"
    assert expanded["items"][1] == {
        **original["items"][1], "command_references": {"status": "unavailable", "queries": []},
    }


def test_deduplicates_task_contexts_but_retains_all_search_hit_links(
    context_data: tuple[Path, Path, Path, Path],
) -> None:
    path = context_data[0]
    data = json.loads(path.read_bytes())
    hits = data["items"][0]["chapters"][0]["matches"]
    hits.append({**hits[0], "point_id": "second-hit", "rank": 2})
    path.write_text(json.dumps(data))
    chapter = expand_context(*context_data)["items"][0]["chapters"][0]
    assert len(chapter["matches"]) == 2
    assert len([c for c in chapter["contexts"] if "task" in c["roles"]]) == 1
    assert chapter["matches"][0]["context_ids"] == chapter["matches"][1]["context_ids"]


def test_fenced_headings_do_not_end_a_procedure_and_duplicate_paths_fail_closed() -> None:
    markdown = (
        "# Chapter\n\n## Task\n\n### Procedure\n\n"
        "```text\n# Not a heading\n\nlast-command\n```\n\n"
        "## Task\n\n### Procedure\n\nOther procedure.\n"
    )
    chapter = ManualChapter(1, "Chapter", "https://www.cisco.com/ch.html", "b" * 64, None, "unmapped", markdown)
    hit = {"point_id": "point", "rank": 1, "score": 0.5,
           "section_path": ["Chapter", "Task", "Procedure"],
           "source_refs": [{"kind": "text"}]}
    with pytest.raises(ContextError, match="ambiguous"):
        expand_chapter([hit], chapter, 1)
    hit["source_refs"] = [{"kind": "code", "okf_line_start": 7, "okf_line_end": 11}]
    _, contexts = expand_chapter([hit], chapter, 1)
    assert "last-command" in contexts[0]["markdown"]
    assert "Other procedure." not in contexts[0]["markdown"]


@pytest.mark.parametrize(
    "change", ["catalog", "generation", "pdf_hash", "text_hash", "section", "rank", "status"],
)
def test_stale_or_invalid_search_output_is_rejected(
    context_data: tuple[Path, Path, Path, Path], change: str,
) -> None:
    path, catalog, okf, pdf = context_data
    data = json.loads(path.read_bytes())
    hit = data["items"][0]["chapters"][0]["matches"][0]
    if change == "catalog":
        data["catalog_sha256"] = "0" * 64
    elif change == "generation":
        data["dataset_fingerprint"] = "0" * 64
    elif change == "pdf_hash":
        hit["source_pdf_sha256"] = "0" * 64
    elif change == "text_hash":
        hit["text"] += " changed"
    elif change == "section":
        hit["section_path"] = ["Missing section"]
    elif change == "rank":
        hit["rank"] = True
    else:
        data["items"][0]["chapters"][0]["status"] = "no_matches"
    path.write_text(json.dumps(data))
    with pytest.raises(ContextError):
        expand_context(path, catalog, okf, pdf)


@pytest.mark.parametrize("source", ["manual", "pdf"])
def test_modified_source_fails_and_preserves_previous_cli_output(
    context_data: tuple[Path, Path, Path, Path], tmp_path: Path,
    capsys: pytest.CaptureFixture, source: str,
) -> None:
    results, catalog, okf, pdf = context_data
    output = tmp_path / "contexts.json"
    args = [
        "--search-results", str(results), "--catalog", str(catalog),
        "--okf-root", str(okf), "--source-pdf-root", str(pdf), "--output", str(output),
    ]
    assert main(args) == 0
    saved = output.read_bytes()
    path = okf / "vlan-guide" / "manual.md" if source == "manual" else pdf / "vlan-guide" / "guide.pdf"
    path.write_bytes(path.read_bytes() + b"\nchanged")
    assert main(args) == 1
    assert output.read_bytes() == saved
    assert "Context expansion failed" in capsys.readouterr().err


def test_cli_stdout_and_protected_output_paths(
    context_data: tuple[Path, Path, Path, Path], capsys: pytest.CaptureFixture,
) -> None:
    results, catalog, okf, pdf = context_data
    args = ["--search-results", str(results), "--catalog", str(catalog),
            "--okf-root", str(okf), "--source-pdf-root", str(pdf)]
    capsys.readouterr()
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["pdf_verification_status"] == "not_verified"
    references = results.parent / "references"
    references.mkdir()
    commands = references / "commands.json"
    commands.write_text("existing reference")
    args += ["--reference-root", str(references)]
    for protected in (
        results, catalog, okf / "vlan-guide" / "manual.md",
        pdf / "vlan-guide" / "guide.pdf", commands,
    ):
        saved = protected.read_bytes()
        assert main(args + ["--output", str(protected)]) == 1
        assert protected.read_bytes() == saved
