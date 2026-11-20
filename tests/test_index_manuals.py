import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

pytestmark = pytest.mark.usefixtures("mock_semantic_reviewer")

from configbrain.command_references import CommandReferenceIndex, extract_command_chapter
from configbrain.convert_manual import convert_manual
from configbrain.expand_context import ContextError, _supplement_commands, expand_context
from configbrain.generate_config import (
    REFERENCE_SYSTEM_PROMPT, GenerationError, _validate_reference_notes,
    build_evidence, generate_config,
)
from configbrain.reference_evidence import (
    ReferenceEvidenceError, _atom_id, register_reference_evidence, verify_reference_citations,
)
from configbrain.report import ReportError, render_report
from configbrain.pipeline import PipelineError, _validate_pipeline_sources, run_pipeline
from configbrain.select_manuals import CloudConfig, SelectionError
from configbrain.semantic_review import REPAIR_SYSTEM_PROMPT
from configbrain import semantic_review
from configbrain.verify_pdf import _normalized
from configbrain.index_manuals import (
    COLLECTION_ALIAS,
    EMBEDDING_MODEL,
    IndexingError,
    ManualChapter,
    ManualDocument,
    ManualCorpus,
    _active_collection,
    _dataset_fingerprint,
    _index_documents,
    build_chunks,
    extract_text_blocks,
    index_manuals,
    load_validated_corpus,
    main,
)
from configbrain.search_manuals import search_manuals


class WordTokenizer:
    def tokenize(self, text: str) -> list[str]:
        return text.split()

    def num_special_tokens_to_add(self, pair: bool = False) -> int:
        return 2

    def encode(self, text: str, *, add_special_tokens: bool = True) -> list[int]:
        token_count = len(text.split())
        return list(range(token_count + (2 if add_special_tokens else 0)))


class FakeEmbeddingModel:
    tokenizer = WordTokenizer()
    max_seq_length = 96

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def get_embedding_dimension(self) -> int:
        return 3

    def encode(
        self,
        sentences: list[str],
        *,
        batch_size: int,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
    ) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("embedding failed")
        return [[1.0, float(index + 1), 0.5] for index, _ in enumerate(sentences)]


def make_document(
    *,
    source_hash: str = "a" * 64,
    chapter_hash: str = "b" * 64,
    markdown: str | None = None,
) -> ManualDocument:
    chapter = ManualChapter(
        order=1,
        title="Configuring DHCP",
        html_url="https://www.cisco.com/example/configuring_dhcp.html",
        html_content_sha256=chapter_hash,
        pdf_page_index=12,
        pdf_mapping_status="matched",
        markdown=markdown
        or """## DHCP Snooping

Enable DHCP snooping on the switch.

<table>
<thead><tr><th>Step</th><th>Command</th><th>Purpose</th></tr></thead>
<tbody><tr><td>1</td><td>ip dhcp snooping</td><td>Enables snooping.</td></tr></tbody>
</table>

```text
Device#show ip dhcp snooping
```
""",
    )
    return ManualDocument(
        document_id="catalyst9300-test",
        title="IP Services Guide",
        product="catalyst9300",
        release="26.x",
        source_pdf_sha256=source_hash,
        chapters=(chapter,),
    )


def test_chunking_keeps_table_columns_and_source_metadata() -> None:
    document = make_document()
    blocks = extract_text_blocks(document.chapters[0].markdown)

    table_block = next(block for block in blocks if block.kind == "table")
    assert "Step: 1" in table_block.text
    assert "Command: ip dhcp snooping" in table_block.text
    assert "Purpose: Enables snooping." in table_block.text
    assert table_block.section_path == ("DHCP Snooping",)

    chunks = build_chunks(
        (document,),
        WordTokenizer(),
        FakeEmbeddingModel.max_seq_length,
        "generation",
    )
    assert all(len(WordTokenizer().encode(chunk.text)) <= 96 for chunk in chunks)
    assert any(chunk.payload["pdf_page_index"] == 12 for chunk in chunks)
    assert all(chunk.payload["pdf_page_scope"] == "chapter_start" for chunk in chunks)
    assert all("PDF chapter start page: 12" in chunk.text for chunk in chunks)
    assert any(
        any(reference.get("table_index") == 1 for reference in chunk.payload["source_refs"])
        for chunk in chunks
    )


def test_table_rows_survive_blank_lines_and_nested_tables() -> None:
    markdown = """## Procedure

<table>
<thead><tr><th>Step</th><th>Command</th><th>Purpose</th></tr></thead>
<tbody>
<tr><td>1</td><td>first</td><td>First step.</td></tr>

<tr><td>2</td><td><pre>second

example</pre></td><td>Second step.
<!-- </table> -->
<table><tr><th>Note</th></tr><tr><td>Nested detail</td></tr></table>
</td></tr>

<tr><td>3</td><td>third</td><td>Third step.</td></tr>
</tbody>
</table>

## Next section

After the table.
"""
    blocks = extract_text_blocks(markdown)
    rows = [block for block in blocks if block.kind == "table"]
    outer = [block for block in rows if block.source_refs[0]["table_index"] == 1]
    assert [block.source_refs[0]["row_index"] for block in outer] == [2, 3, 4]
    assert "Command: second example" in outer[1].text
    assert "Purpose: Second step. Note Nested detail" in outer[1].text
    assert all(block.section_path == ("Procedure",) for block in outer)
    assert all(block.source_refs[0]["okf_line_start"] == 3 for block in outer)
    assert all(block.source_refs[0]["okf_line_end"] == 17 for block in outer)
    assert any(block.source_refs[0]["table_index"] == 2 for block in rows)
    assert any(
        block.section_path[-1:] == ("Next section",) and "After the table." in block.text
        for block in blocks
    )


def test_unclosed_html_table_fails_instead_of_indexing_partial_rows() -> None:
    for markdown in (
        "## Procedure\n\n<table>\n<tr><td>Incomplete</td></tr>\n",
        "<table>\n</table><table>\n<tr><td>Incomplete</td></tr>\n",
        "<table\n",
    ):
        with pytest.raises(IndexingError, match="Unclosed HTML table"):
            extract_text_blocks(markdown)


def test_table_markup_in_a_code_fence_is_not_treated_as_a_table() -> None:
    blocks = extract_text_blocks("```html\n<table>\n```\n")
    assert len(blocks) == 1
    assert blocks[0].kind == "code"


def test_table_in_a_list_preserves_rows_after_a_blank_line() -> None:
    blocks = extract_text_blocks(
        "- <table><tr><th>Step</th></tr>\n"
        "  <tr><td>1</td></tr>\n\n"
        "  <tr><td>2</td></tr></table>\n"
    )
    rows = [block for block in blocks if block.kind == "table"]
    assert [row.source_refs[0]["row_index"] for row in rows] == [2, 3]
    assert "Step: 2" in rows[1].text


def test_unmapped_chapter_has_no_pdf_page_in_chunk_context() -> None:
    document = make_document()
    chapter = replace(document.chapters[0], pdf_page_index=None, pdf_mapping_status="unmapped")
    document = replace(document, chapters=(chapter,))
    chunks = build_chunks((document,), WordTokenizer(), 96, "generation")
    assert all(chunk.payload["pdf_page_scope"] == "unmapped" for chunk in chunks)
    assert all("PDF chapter start page:" not in chunk.text for chunk in chunks)


def test_local_qdrant_index_is_idempotent_and_replaces_active_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "qdrant"
    documents = (make_document(),)
    model = FakeEmbeddingModel()
    monkeypatch.setattr(
        "configbrain.index_manuals.load_validated_corpus",
        lambda *args: ManualCorpus(documents, ()),
    )
    monkeypatch.setattr(
        "configbrain.index_manuals._load_embedding_model",
        lambda model_name: model,
    )

    first = index_manuals(Path("catalog.json"), database_path=database_path)
    assert first.status == "created"

    second = index_manuals(Path("catalog.json"), database_path=database_path)
    assert second.status == "unchanged"
    assert model.calls == 1

    changed = (make_document(chapter_hash="c" * 64),)
    monkeypatch.setattr(
        "configbrain.index_manuals.load_validated_corpus",
        lambda *args: ManualCorpus(changed, ()),
    )
    updated = index_manuals(Path("catalog.json"), database_path=database_path)
    assert updated.status == "updated"
    assert updated.collection_name != first.collection_name

    client = QdrantClient(path=str(database_path))
    try:
        assert _active_collection(client) == updated.collection_name
        assert not client.collection_exists(first.collection_name)
        assert client.count(COLLECTION_ALIAS, exact=True).count == updated.point_count
    finally:
        client.close()


def test_failed_embedding_keeps_existing_qdrant_collection_active(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "qdrant"
    original_model = FakeEmbeddingModel()
    original = _index_documents(
        (make_document(),),
        database_path,
        model=original_model,
    )
    replacement_model = FakeEmbeddingModel()
    replacement_model.fail = True

    with pytest.raises(RuntimeError, match="embedding failed"):
        _index_documents(
            (make_document(chapter_hash="d" * 64),),
            database_path,
            model=replacement_model,
        )

    client = QdrantClient(path=str(database_path))
    try:
        assert _active_collection(client) == original.collection_name
        assert client.count(COLLECTION_ALIAS, exact=True).count == original.point_count
    finally:
        client.close()


@pytest.fixture
def mixed_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    catalog = tmp_path / "catalog.json"
    documents = []
    for kind, document_id in (
        ("configuration_guide", "vlan-guide"), ("command_reference", "vlan-reference"),
    ):
        book = (
            "https://www.cisco.com/c/en/us/td/docs/switches/catalyst9300/"
            f"release/26-x/{kind}/book"
        )
        documents.append({
            "document_id": document_id, "document_type": kind, "title": document_id,
            "category": kind, "filename": "book.pdf", "url": book + ".pdf",
            "html_book_url": book + ".html", "html_status": "available",
            "chapters": [{"order": 1, "title": "VLANs", "url": book + "/vlans.html"}],
        })
    catalog.write_text(json.dumps({
        "schema_version": 2, "product": "catalyst9300", "release": "26.x",
        "documents": documents,
    }))
    pdf_root = tmp_path / "pdf"
    pdf = pdf_root / "vlan-guide" / "book.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.7\nsynthetic test document\n")
    guide_html = b"""<div id="chapterContent"><h2>Procedure</h2>
    <table><tr><th>Step</th><th>Command</th><th>Purpose</th></tr>
    <tr><td>1</td><td><p>configure terminal</p><pre>Device#configure terminal</pre></td>
    <td>Enters global configuration mode.</td></tr>
    <tr><td>2</td><td><p>vlan <var>vlan-id</var></p><pre>Device(config)#vlan 120</pre></td>
    <td>Enters VLAN configuration mode.</td></tr>
    <tr><td>3</td><td><p>media { ethernet | fddi }</p>
    <pre>Device(config-vlan)#media ethernet</pre></td><td>Sets media type.</td></tr>
    </table></div>"""
    reference_html = b"""<div id="chapterContent">
    <article class="topic reference nested1" id="vlan"><h2 class="title">vlan</h2>
    <section class="body refbody"><h3 class="sectiontitle">Command Default</h3><p>None</p>
    <h3 class="sectiontitle">Command Modes</h3><p>Global configuration</p>
    <section class="usage_guidelines"><h3 class="sectiontitle">Usage Guidelines</h3>
    <p>Extended VLANs must retain default characteristics.</p>
    <p>These configuration commands are available in VLAN configuration mode.</p>
    <ul><li id="media"><p><span class="keyword">media</span> selects a media type.</p>
    <ul><li>ethernet (the default)</li></ul></li></ul>
    </section></section></article>
    <article class="topic reference nested1" id="neighbor"><h2 class="title">neighbor</h2>
    <section class="refbody"><h3 class="sectiontitle">Usage Guidelines</h3>
    <p>Unrelated command warning.</p></section></article></div>"""
    monkeypatch.setattr("configbrain.convert_manual.fetch_chapter_page", lambda *args: guide_html)
    monkeypatch.setattr(
        "configbrain.convert_manual.map_chapters_to_pdf_pages", lambda *args: (2, {1: [1]}),
    )
    monkeypatch.setattr(
        "configbrain.command_references.fetch_chapter_page", lambda *args: reference_html,
    )
    okf = tmp_path / "okf"
    references = tmp_path / "references"
    for document in documents:
        convert_manual(
            catalog, document["document_id"], pdf_root, okf,
            reference_output_root=references,
        )
    monkeypatch.setattr(
        "configbrain.index_manuals._load_embedding_model", lambda *args: FakeEmbeddingModel(),
    )
    return {
        "catalog_path": catalog, "okf_root": okf, "source_pdf_root": pdf_root,
        "reference_root": references, "database_path": tmp_path / "db",
    }


def write_mixed_selection(sources: dict, tmp_path: Path, document_ids: tuple[str, ...]) -> Path:
    catalog = json.loads(sources["catalog_path"].read_bytes())
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({
        "product": "catalyst9300", "release": "26.x", "requirement": "Create VLAN 120",
        "catalog_sha256": hashlib.sha256(sources["catalog_path"].read_bytes()).hexdigest(),
        "items": [{
            "requirement": "Create VLAN 120", "uncertainty": "Needs review.",
            "candidates": [{
                "document_id": document["document_id"], "chapter_order": 1,
                "chapter_title": "VLANs", "chapter_url": document["chapters"][0]["url"],
                "reason": "VLAN procedure.", "uncertainty": "Needs review.",
            } for document in catalog["documents"] if document["document_id"] in document_ids],
        }],
    }))
    return selection


def test_mixed_catalog_indexes_only_guides_and_expands_guide_search_hits(
    mixed_sources: dict, tmp_path: Path,
) -> None:
    sources = mixed_sources
    result = index_manuals(**sources)
    assert result.document_count == result.chapter_count == 1
    assert index_manuals(**sources).status == "unchanged"
    assert not (sources["okf_root"] / "vlan-reference").exists()
    assert not (sources["source_pdf_root"] / "vlan-reference").exists()
    catalog = json.loads(sources["catalog_path"].read_bytes())
    selection = write_mixed_selection(sources, tmp_path, ("vlan-guide",))
    search = search_manuals(
        selection, sources["catalog_path"], sources["database_path"],
        model=FakeEmbeddingModel(),
    )
    search_path = tmp_path / "search.json"
    search_path.write_text(json.dumps(search))
    context_sources = {key: value for key, value in sources.items() if key != "database_path"}
    contexts = expand_context(search_path, **context_sources)
    assert "vlan 120" in contexts["items"][0]["chapters"][0]["contexts"][0]["markdown"]
    assert contexts["pdf_verification_status"] == "not_verified"

    reference_html = sources["reference_root"] / "vlan-reference" / "html/1.html"
    raw = reference_html.read_bytes().replace(b"None", b"No default VLAN")
    commands_path = reference_html.parents[1] / "commands.json"
    commands = json.loads(commands_path.read_bytes())
    commands["chapters"] = [
        extract_command_chapter(raw, catalog["documents"][1]["chapters"][0])
    ]
    reference_html.write_bytes(raw)
    commands_path.write_text(json.dumps(commands))
    with pytest.raises(ContextError, match="generation differs"):
        expand_context(search_path, **context_sources)
    updated = index_manuals(**sources)
    assert updated.status == "updated"
    assert updated.collection_name != result.collection_name


@pytest.mark.parametrize("change", ["missing", "html", "commands", "release", "extractor_version"])
def test_invalid_reference_prevents_index_switch(
    mixed_sources: dict, change: str, capsys: pytest.CaptureFixture,
) -> None:
    sources = mixed_sources
    original = index_manuals(**sources)
    directory = sources["reference_root"] / "vlan-reference"
    commands_path = directory / "commands.json"
    if change == "missing":
        commands_path.unlink()
    elif change == "html":
        html_path = directory / "html/1.html"
        html_path.write_bytes(html_path.read_bytes() + b"\nchanged")
    else:
        data = json.loads(commands_path.read_bytes())
        if change == "commands":
            data["chapters"][0]["commands"][0]["name"] = "wrong command"
        elif change == "release":
            data["release"] = "other"
        else:
            data["extractor_version"] += 1
        commands_path.write_text(json.dumps(data))
    args = [
        option for key, value in sources.items()
        for option in (f"--{key.replace('_path', '').replace('_', '-')}", str(value))
    ]
    assert main(args) == 1
    assert "Invalid command reference" in capsys.readouterr().err
    client = QdrantClient(path=str(sources["database_path"]))
    try:
        assert _active_collection(client) == original.collection_name
        assert client.count(COLLECTION_ALIAS, exact=True).count == original.point_count
    finally:
        client.close()


def test_reference_only_catalog_validates_without_opening_qdrant(
    mixed_sources: dict,
) -> None:
    sources = mixed_sources
    catalog_path = sources["catalog_path"]
    catalog = json.loads(catalog_path.read_bytes())
    catalog["documents"] = catalog["documents"][1:]
    catalog_path.write_text(json.dumps(catalog))
    corpus = load_validated_corpus(
        catalog_path, sources["okf_root"], sources["source_pdf_root"], sources["reference_root"],
    )
    assert corpus.guides == ()
    assert corpus.references[0].search("vlan")["status"] == "unique_match"
    with pytest.raises(IndexingError, match="No validated OKF"):
        index_manuals(**sources)
    assert not sources["database_path"].exists()


def test_pipeline_preflight_accepts_both_validated_source_types_and_current_index(
    mixed_sources: dict,
) -> None:
    sources = mixed_sources
    result = index_manuals(**sources)
    checked = _validate_pipeline_sources(**sources)
    assert checked["configuration_guide_count"] == checked["command_reference_count"] == 1
    client = QdrantClient(path=str(sources["database_path"]))
    try:
        records, _ = client.scroll(result.collection_name, limit=1)
        assert checked["dataset_fingerprint"] == records[0].payload["dataset_fingerprint"]
    finally:
        client.close()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("guide_catalog_missing", "コンフィグガイド"),
        ("reference_catalog_missing", "コマンドリファレンス"),
        ("guide_files_missing", "OKF package is missing"),
        ("reference_files_missing", "Command reference is missing"),
        ("reference_html_changed", "Invalid command reference"),
        ("index_missing", "検索DBがありません"),
        ("index_stale", "検索索引が現在の資料"),
        ("guide_not_indexed", "ガイドの検索索引がありません"),
    ],
)
def test_pipeline_preflight_rejects_unavailable_sources_before_selection(
    mixed_sources: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    change: str, message: str,
) -> None:
    sources = mixed_sources
    if change != "index_missing":
        index_manuals(**sources)
    if change.endswith("_catalog_missing"):
        catalog = json.loads(sources["catalog_path"].read_bytes())
        kind = "configuration_guide" if change.startswith("guide_") else "command_reference"
        catalog["documents"] = [
            entry for entry in catalog["documents"] if entry["document_type"] != kind
        ]
        sources["catalog_path"].write_text(json.dumps(catalog))
    elif change == "guide_files_missing":
        package = sources["okf_root"] / "vlan-guide"
        package.rename(package.with_name("unavailable-guide"))
    elif change == "reference_files_missing":
        package = sources["reference_root"] / "vlan-reference"
        package.rename(package.with_name("unavailable-reference"))
    elif change == "reference_html_changed":
        path = sources["reference_root"] / "vlan-reference/html/1.html"
        path.write_bytes(path.read_bytes() + b"\nchanged")
    elif change in ("index_stale", "guide_not_indexed"):
        client = QdrantClient(path=str(sources["database_path"]))
        try:
            collection = _active_collection(client)
            records, _ = client.scroll(collection, limit=100)
            payload = (
                {"dataset_fingerprint": "obsolete"}
                if change == "index_stale" else {"document_id": "unregistered-guide"}
            )
            points = [record.id for record in records] if change == "guide_not_indexed" else [records[0].id]
            client.set_payload(collection, payload, points=points)
        finally:
            client.close()

    monkeypatch.setattr(
        "configbrain.pipeline.load_cloud_config", lambda: pytest.fail("Preflight must not load API config."),
    )
    output = tmp_path / "preflight-run"
    with pytest.raises(PipelineError, match=message):
        run_pipeline("VLAN 120を作成", output, **sources)
    state = json.loads((output / "pipeline-state.json").read_bytes())
    assert state["current_stage"] == "preflight" and state["completed"] == {}
    report = (output / "report.md").read_text()
    assert "事前確認に失敗" in report and message in report
    assert not (output / "selection.json").exists()
    if change == "index_missing":
        assert not sources["database_path"].exists()


def test_fingerprint_distinguishes_unspecified_and_explicit_guide_type() -> None:
    document = make_document()
    assert _dataset_fingerprint((document,), EMBEDDING_MODEL) != _dataset_fingerprint(
        (replace(document, document_type="configuration_guide"),), EMBEDDING_MODEL,
    )


@pytest.mark.parametrize("select_reference", [False, True])
def test_guide_media_lookup_preserves_parent_defaults_and_full_command(
    mixed_sources: dict, tmp_path: Path, select_reference: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = mixed_sources
    index_manuals(**sources)
    ids = ("vlan-guide", "vlan-reference") if select_reference else ("vlan-guide",)
    selection = write_mixed_selection(sources, tmp_path, ids)
    search = search_manuals(
        selection, sources["catalog_path"], sources["database_path"], limit=1,
        model=FakeEmbeddingModel(), reference_root=sources["reference_root"],
    )
    if select_reference:
        chapter = search["items"][0]["chapters"][1]
        assert chapter["status"] == "command_lookup_pending"
        assert chapter["matches"] == []
    search_path = tmp_path / "search.json"
    search_path.write_text(json.dumps(search))
    context_sources = {key: value for key, value in sources.items() if key != "database_path"}
    contexts = expand_context(search_path, **context_sources)
    lookup = contexts["items"][0]["command_references"]
    media = next(query for query in lookup["queries"] if query["name"] == "media")
    assert media["status"] == "unique_match"
    assert media["guide"]["mode"] == "VLAN configuration"
    assert media["guide"]["parent"] == "vlan"
    assert media["guide"]["mode_source"]["row_index"] == 3
    result = media["results"][0]
    match = result["matches"][0]
    assert "ethernet (the default)" in match["matched_subcommand"]["text"]
    assert "None" in next(
        section["text"] for section in match["command"]["sections"]
        if section["kind"] == "command_default"
    )
    assert "Extended VLANs" in match["command"]["content_html"]
    assert "Unrelated command warning" not in match["command"]["content_html"]
    assert media["adopted"][0]["retrieval_reason"] == (
        "selected_reference_chapter" if select_reference else "guide_command_supplement"
    )
    assert result["sources"][0]["html_sha256"]
    assert media["adopted"][0]["subcommand_source"]["anchor"] == "media"
    assert expand_context(search_path, **context_sources) == contexts
    evidence = build_evidence(contexts)
    assert [command["syntax"] for command in evidence["commands"].values()] == [
        "configure terminal", "vlan vlan-id", "media { ethernet | fddi }",
    ]
    assert all(
        "None" not in evidence["evidence_atoms"][key]["text"]
        for key in evidence["items"][0]["evidence_ids"]
    )

    contexts_path = tmp_path / "contexts.json"
    media["adopted"].clear()
    contexts_path.write_text(json.dumps(contexts))
    monkeypatch.setattr(
        "configbrain.generate_config.request_json",
        lambda *args: pytest.fail("Changed supplement must fail before generation."),
    )
    with pytest.raises(GenerationError, match="Contexts differ"):
        generate_config(
            contexts_path, search_path, sources["catalog_path"], sources["okf_root"],
            sources["source_pdf_root"], CloudConfig("https://example.test", "test-key", None),
            reference_root=sources["reference_root"],
        )


def test_reference_only_selection_needs_no_qdrant_or_embedding_model(
    mixed_sources: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = mixed_sources
    selection = write_mixed_selection(sources, tmp_path, ("vlan-reference",))
    for symbol in ("QdrantClient", "_load_embedding_model"):
        monkeypatch.setattr(
            f"configbrain.search_manuals.{symbol}",
            lambda *args, **kwargs: pytest.fail("Reference-only selection must not query Qdrant."),
        )
    search = search_manuals(
        selection, sources["catalog_path"], sources["database_path"],
        reference_root=sources["reference_root"],
    )
    assert search["dataset_fingerprint"] is search["collection_name"] is None
    assert search["items"][0]["status"] == "command_lookup_pending"
    assert not sources["database_path"].exists()
    path = tmp_path / "search.json"
    path.write_text(json.dumps(search))
    context_sources = {key: value for key, value in sources.items() if key != "database_path"}
    contexts = expand_context(path, **context_sources)
    assert contexts["items"][0]["command_references"]["status"] == "no_guide_commands"
    assert build_evidence(contexts)["commands"] == {}
    search["items"][0]["chapters"][0]["reference_metadata"]["extractor_version"] += 1
    path.write_text(json.dumps(search))
    with pytest.raises(ContextError, match="reference metadata"):
        expand_context(path, **context_sources)


@pytest.mark.parametrize(("case", "expected"), [
    ("unknown_mode", "candidate"), ("unknown_parent", "candidate"),
    ("different_mode", "not_found"), ("ambiguous", "ambiguous"),
    ("unparsed", "unparsed"), ("new_table", "candidate"),
    ("unknown_step", "candidate"), ("multiple_prompts", "candidate"),
    ("contradictory_transition", "candidate"), ("prompt_without_parent", "candidate"),
])
def test_uncertain_procedure_context_does_not_adopt_reference_commands(
    mixed_sources: dict, case: str, expected: str,
) -> None:
    sources = mixed_sources
    corpus = load_validated_corpus(
        sources["catalog_path"], sources["okf_root"],
        sources["source_pdf_root"], sources["reference_root"],
    )
    references = corpus.references
    header = "<table><tr><th>Step</th><th>Command</th><th>Purpose</th></tr>"
    transition = (
        "<tr><td>1</td><td>vlan <var>id</var></td>"
        "<td>Enters VLAN configuration mode.</td></tr>"
    )
    media = "<tr><td>2</td><td><p>media {{ ethernet | fddi }}</p>{}</td><td></td></tr>"
    if case == "unknown_mode":
        markdown = header + media.format("") + "</table>"
    elif case == "unknown_parent":
        markdown = (
            header + transition.replace("vlan <var>id</var>", "<var>command</var>")
            + media.format("") + "</table>"
        )
    elif case == "different_mode":
        markdown = header + media.format("<pre>Device(config-if)#media ethernet</pre>") + "</table>"
    elif case == "new_table":
        markdown = header + transition + "</table>" + header + media.format("") + "</table>"
    elif case == "unknown_step":
        markdown = header + transition + "<tr><td>?</td><td></td><td>Unknown step.</td></tr>" + media.format("") + "</table>"
    elif case == "multiple_prompts":
        markdown = (
            header + transition
            + media.format("<pre>Device(config-vlan)#media ethernet\nDevice(config-if)#exit</pre>")
            + "</table>"
        )
    elif case == "contradictory_transition":
        markdown = header + transition + media.format("<pre>Device(config-if)#media ethernet</pre>") + "</table>"
    elif case == "prompt_without_parent":
        markdown = header + media.format("<pre>Device(config-vlan)#media ethernet</pre>") + "</table>"
    else:
        markdown = header + transition + media.format("") + "</table>"
        second = deepcopy(references[0].data)
        if case == "ambiguous":
            second["document_id"] = "other-reference"
            references = (*references, CommandReferenceIndex(second))
        else:
            second["chapters"][0]["unparsed"].append({
                "name": "media", "reason": "duplicate_anchor", "source": {},
            })
            references = (CommandReferenceIndex(second),)
    chapter = {
        "document_id": "vlan-guide", "chapter_order": 1,
        "contexts": [{
            "context_id": "procedure", "manual_line_start": 1, "manual_line_end": 10,
            "markdown": markdown,
        }],
    }
    results = _supplement_commands([chapter], references, set())
    query = next(query for query in results["queries"] if query["name"] == "media")
    assert query["status"] == expected
    assert query["adopted"] == []
    registered = register_reference_evidence(
        {"product": "catalyst9300", "release": "26.x"},
        {"command_references": {"status": "queried", "queries": [query]}},
        {}, {}, sources["reference_root"],
    )
    assert registered["reference_evidence_ids"] == []


@pytest.fixture
def reference_generation_case(
    mixed_sources: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> dict:
    sources = mixed_sources
    index_manuals(**sources)
    selection = write_mixed_selection(sources, tmp_path, ("vlan-guide", "vlan-reference"))
    search = search_manuals(
        selection, sources["catalog_path"], sources["database_path"], limit=1,
        model=FakeEmbeddingModel(), reference_root=sources["reference_root"],
    )
    search_path = tmp_path / "search.json"
    search_path.write_text(json.dumps(search))
    contexts = expand_context(
        search_path, **{key: value for key, value in sources.items() if key != "database_path"},
    )
    contexts_path = tmp_path / "contexts.json"
    contexts_path.write_text(json.dumps(contexts))
    evidence = build_evidence(contexts, sources["reference_root"])
    guide_text = _normalized(" ".join(
        fragment
        for key in evidence["items"][0]["evidence_ids"]
        for fragment in evidence["evidence_atoms"][key]["pdf_match_fragments"]
    ))
    pdf_reads = []

    def pages(path: Path) -> list[str]:
        pdf_reads.append(path)
        assert path.name == "book.pdf" and path.parent.name == "vlan-guide"
        return [guide_text, ""]

    monkeypatch.setattr("configbrain.verify_pdf._extract_pages", pages)
    return {
        "sources": sources, "contexts": contexts, "contexts_path": contexts_path,
        "search_path": search_path, "evidence": evidence, "pdf_reads": pdf_reads,
    }


def guide_plan_response(prompt: dict) -> dict:
    configuration = []
    for command_id, command in prompt["commands"].items():
        syntax = command["syntax"]
        cli = (
            "vlan 120" if syntax.startswith("vlan ") else
            "media ethernet" if syntax.startswith("media ") else syntax
        )
        configuration.append({
            "command_id": command_id, "cli": cli,
            "bindings": {
                parameter: {"value": "120", "origin": "requirement"}
                for parameter in command["parameters"]
            },
        })
    return {"items": [{
        "item_id": "item-1", "status": "supported", "depends_on": [],
        "assessment": "設定手順を保持します。", "configuration": configuration,
        "templates": [], "verification": [], "verification_templates": [],
        "evidence": [], "constraints": [], "missing_information": [], "assumptions": [],
        "withheld_steps": [], "inference_candidates": [],
    }], "next_checks": []}


def reference_note_response(prompt: dict) -> dict:
    child_id = next(
        key for key in prompt["reference_evidence_ids"]
        if prompt["evidence_atoms"][key]["locator"]["kind"] == "reference_subcommand"
    )
    return {"reference_notes": [{
        "kind": "default", "explanation": "Ethernetはデフォルトです。CLIは変更しません。",
        "conditions": ["新規VLANなら明示設定を省略可能。既存値が不明なら省略判断は未確定です。"],
        "evidence_ids": [child_id],
    }]}


@pytest.mark.parametrize("outcome", [
    "default", "mixed", "failure", "conflict", "retry", "request_retry",
])
def test_reference_explanations_preserve_cli_and_separate_html_from_pdf(
    reference_generation_case: dict, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    case = reference_generation_case
    sources = case["sources"]
    calls = []
    invalid_responses = []
    fail_notes = outcome == "failure"
    rounds = {"retry": 2, "request_retry": 3}.get(outcome, 1)

    def request(system_prompt: str, prompt: dict, config: CloudConfig):
        calls.append(system_prompt)
        if system_prompt == REFERENCE_SYSTEM_PROMPT:
            assert "commands" not in prompt
            child_atom = next(
                atom for atom in prompt["evidence_atoms"].values()
                if atom["locator"]["kind"] == "reference_subcommand"
            )
            parent_id = child_atom["source_id"]
            assert prompt["sources"][parent_id]["command_name"] == "vlan"
            assert any(
                atom["source_id"] == parent_id
                and atom["locator"]["kind"] == "reference_command"
                and "None" in atom["text"]
                and "Extended VLANs must retain default characteristics." in atom["text"]
                for atom in prompt["evidence_atoms"].values()
            )
            assert prompt["plan"]["configuration"] == [
                "configure terminal", "vlan 120", "media ethernet",
            ]
            note = reference_note_response(prompt)
            if outcome == "conflict":
                note["reference_notes"][0].update(
                    kind="conflict", explanation="両資料の適用条件に矛盾がないか確認が必要です。",
                )
            if outcome in ("mixed", "conflict", "retry", "request_retry"):
                note["reference_notes"][0]["evidence_ids"].append(prompt["guide_evidence_ids"][0])
            fail_attempt = fail_notes or (
                outcome in ("retry", "request_retry") and len(calls) == 2
            )
            if fail_attempt:
                if fail_notes:
                    note = {"configuration": [{
                        "source_id": "unaccepted-source", "evidence_id": "unaccepted-evidence",
                        "quote": "A rejected response must not be verified or applied.",
                    }]}
                else:
                    note["reference_notes"][0]["evidence_ids"] = [prompt["guide_evidence_ids"][0]]
                invalid_responses.append(deepcopy(note))
            elif outcome in ("retry", "request_retry"):
                feedback = prompt["validation_feedback"]
                assert feedback["previous_response"] == invalid_responses[0]
                assert "Reference note 1 (1-based)" in feedback["error"]
                assert "At least one eligible reference evidence ID" in feedback["error"]
                assert prompt["guide_evidence_ids"][0] in feedback["error"]
                if outcome == "request_retry" and len(calls) == 3:
                    raise SelectionError("Cloud LLM request failed.")
            return note, "notes-model"
        assert all(key.startswith("evidence-v1-") for key in prompt["evidence_atoms"])
        assert all(key.startswith("source-") for key in prompt["sources"])
        return guide_plan_response(prompt), "guide-model"

    monkeypatch.setattr("configbrain.generate_config.request_json", request)
    result = generate_config(
        case["contexts_path"], case["search_path"], sources["catalog_path"],
        sources["okf_root"], sources["source_pdf_root"],
        CloudConfig("https://example.test", "test-key", None),
        generation_rounds=rounds,
        reference_root=sources["reference_root"],
    )
    assert len(calls) == rounds + 1
    item = result["items"][0]
    assert [command["cli"] for command in item["configuration"]] == [
        "configure terminal", "vlan 120", "media ethernet",
    ]
    assert result["pdf_verification_status"] == "verified"
    assert len(case["pdf_reads"]) == 2
    assert item["reference_status"] == ("failed" if fail_notes else "complete")
    history = result["reference_generation_requests"]
    assert len(history) == item["reference_generation_attempts"]
    assert history[-1]["outcome"] == ("validation_failed" if fail_notes else "complete")
    assert history[-1]["model"] == "notes-model"
    if invalid_responses:
        assert history[0]["response"] == invalid_responses[0]
        assert history[0]["error"] in item["reference_generation_errors"][0]
    assert result["generation_status"] == ("partial" if fail_notes else "complete")
    report = render_report(result)
    if fail_notes:
        assert "reference_generation_error" in item
        assert "補足生成未完了" in report
        assert result["html_verification_status"] == "not_verified"
    else:
        assert result["html_verification_status"] == "verified"
        citation = item["reference_notes"][0]["citations"][0]
        assert citation["html_verification"]["status"] == "matched"
        assert "pdf_verification" not in citation
        assert "source_pdf_path" not in result["sources"][citation["source_id"]]
        assert "pdf_page_index" not in result["sources"][citation["source_id"]]
        assert "省略判断は未確定" in report
        assert "保存HTML照合済み（PDF照合ではありません）" in report
        assert "原文位置: [コマンド項目]" in report
        assert "/ [引用位置](" in report
        assert "vlans.html#media)" in report
        if outcome in ("retry", "request_retry"):
            assert item["reference_generation_attempts"] == rounds
            assert len(item["reference_generation_errors"]) == rounds - 1
            assert "補足応答の再検証履歴" in report
            if outcome == "request_retry":
                assert history[1]["outcome"] == "request_failed"
                assert "response" not in history[1]
                assert "model" not in history[1]
        if outcome in ("mixed", "conflict", "retry", "request_retry"):
            assert item["reference_notes"][0]["citations"][1]["pdf_verification"]["status"] == "matched"
            assert "html_verification" not in item["reference_notes"][0]["citations"][1]
            invalid = deepcopy(result)
            invalid["items"][0]["reference_notes"][0]["citations"] = [
                item["reference_notes"][0]["citations"][1],
            ]
            with pytest.raises(ReportError):
                render_report(invalid)
        if outcome == "conflict":
            assert "資料間の矛盾" in report
            assert result["fulfillment_status"] == "incomplete"
        invalid = deepcopy(result)
        # Exercise the source-role guard separately from review freshness.
        invalid.pop("semantic_reviews")
        invalid["items"][0]["configuration"][0]["citations"] = [citation]
        with pytest.raises(ReportError, match="explanation-only"):
            render_report(invalid)
        invalid = deepcopy(result)
        invalid["items"][0]["reference_notes"][0]["citations"][0]["pdf_verification"] = {
            "status": "matched", "page_indexes": [1], "fragment_count": 1,
            "method": "normalized_text_fragments",
        }
        with pytest.raises(ReportError, match="HTML-verified"):
            render_report(invalid)
        if outcome == "default":
            for change in ("missing_html", "html_hash", "global_status", "invalid_source"):
                invalid = deepcopy(result)
                citation = invalid["items"][0]["reference_notes"][0]["citations"][0]
                if change == "missing_html":
                    citation.pop("html_verification")
                elif change == "html_hash":
                    citation["html_verification"]["html_sha256"] = "0" * 64
                elif change == "global_status":
                    invalid["html_verification_status"] = "not_verified"
                else:
                    invalid["sources"][citation["source_id"]] = None
                with pytest.raises(ReportError):
                    render_report(invalid)


def test_semantic_repair_rechecks_mixed_citations_without_changing_cli(
    reference_generation_case: dict, monkeypatch: pytest.MonkeyPatch,
    mock_semantic_reviewer: list,
) -> None:
    case = reference_generation_case
    sources = case["sources"]
    approve = semantic_review.request_json
    semantic_calls = []

    def generate(system_prompt, prompt, config):
        response = (
            reference_note_response(prompt) if system_prompt == REFERENCE_SYSTEM_PROMPT
            else guide_plan_response(prompt)
        )
        return response, "generation-model"

    def review(system_prompt, prompt, config):
        semantic_calls.append(system_prompt)
        if system_prompt == REPAIR_SYSTEM_PROMPT:
            response = reference_note_response(prompt)["reference_notes"][0]
            response["explanation"] = "デフォルトの適用には既存か新規かの状態確認が必要です。"
            response["conditions"] = ["既存VLANの現在の媒体属性は未確認です。"]
            guide_id = next(
                eid for eid in prompt["evidence_atoms"] if eid.startswith("evidence-v1-")
            )
            response["evidence_ids"].append(guide_id)
            return {
                "replacements": [{
                    "target_id": prompt["requested_target_ids"][0],
                    "replacement": response,
                }],
                "unresolved_target_ids": [],
            }, "semantic-repair-model"
        response, model = approve(system_prompt, prompt, config)
        reference_targets = {
            target["target_id"] for target in prompt["targets"]
            if target["field"] == "reference_notes"
        }
        reference_units = {
            unit["unit_id"] for unit in prompt["units"] if unit["target_id"] in reference_targets
        }
        for result in response["reviews"]:
            if result["unit_id"] in reference_units:
                for claim in result["claims"]:
                    if len(semantic_calls) == 1:
                        claim["support"] = "insufficient_evidence"
                    else:
                        claim.update(
                            applicability="unknown", conditions=["既存か新規かと現在の媒体属性"],
                        )
        return response, model

    monkeypatch.setattr("configbrain.generate_config.request_json", generate)
    monkeypatch.setattr(semantic_review, "request_json", review)
    draft = generate_config(
        case["contexts_path"], case["search_path"], sources["catalog_path"],
        sources["okf_root"], sources["source_pdf_root"],
        CloudConfig("https://unused.test", "not-a-real-key", None),
        generation_rounds=2, reference_root=sources["reference_root"],
    )
    item = draft["items"][0]
    assert len(semantic_calls) == 3
    assert [entry["cli"] for entry in item["configuration"]] == [
        "configure terminal", "vlan 120", "media ethernet",
    ]
    assert len(case["pdf_reads"]) == 3
    assert draft["html_verification_status"] == draft["pdf_verification_status"] == "verified"
    note = item["reference_notes"][0]
    assert note["citations"][0]["html_verification"]["status"] == "matched"
    assert "pdf_verification" not in note["citations"][0]
    assert note["citations"][1]["pdf_verification"]["status"] == "matched"
    assert "html_verification" not in note["citations"][1]
    assert item["semantic_review_pending"] is True
    assert draft["generation_status"] == "complete"
    assert draft["fulfillment_status"] == "incomplete"
    assert "支持・適用条件未確認" in render_report(draft)


@pytest.mark.parametrize("change", [
    "conditions", "foreign", "guide_only", "conflict_only_reference",
    "empty", "duplicate", "other_item_guide",
])
def test_reference_notes_reject_ineligible_or_unconditional_claims(
    reference_generation_case: dict, change: str,
) -> None:
    evidence = reference_generation_case["evidence"]
    original = evidence["items"][0]
    response = reference_note_response({
        "reference_evidence_ids": original["reference_evidence_ids"],
        "evidence_atoms": evidence["evidence_atoms"],
    })
    note = response["reference_notes"][0]
    if change == "conditions":
        note["conditions"] = []
    elif change == "foreign":
        note["evidence_ids"] = ["reference-evidence-v1-" + "0" * 64]
    elif change == "guide_only":
        note["evidence_ids"] = original["evidence_ids"][:1]
    elif change == "empty":
        note["evidence_ids"] = []
    elif change == "duplicate":
        note["evidence_ids"] *= 2
    elif change == "other_item_guide":
        note["evidence_ids"].append(original["evidence_ids"][0])
        original = {**original, "evidence_ids": original["evidence_ids"][1:]}
    else:
        note["kind"] = "conflict"
    with pytest.raises(GenerationError):
        _validate_reference_notes(response, original, evidence)


def test_reference_diagnostics_identify_all_invalid_notes_and_cited_ids(
    reference_generation_case: dict,
) -> None:
    evidence = reference_generation_case["evidence"]
    original = evidence["items"][0]
    response = reference_note_response({
        "reference_evidence_ids": original["reference_evidence_ids"],
        "evidence_atoms": evidence["evidence_atoms"],
    })
    response["reference_notes"].append(deepcopy(response["reference_notes"][0]))
    response["reference_notes"][0]["evidence_ids"].append("foreign-evidence")
    guide_id = original["evidence_ids"][0]
    response["reference_notes"][1]["evidence_ids"] = [guide_id]
    with pytest.raises(GenerationError) as error:
        _validate_reference_notes(response, original, evidence)
    message = str(error.value)
    assert "Reference note 1 (1-based)" in message
    assert "Unknown or outside-item evidence_ids: ['foreign-evidence']" in message
    assert "Reference note 2 (1-based)" in message
    assert "At least one eligible reference evidence ID" in message
    assert guide_id in message


@pytest.mark.parametrize("change", ["html", "quote", "locator", "neighbor", "extractor", "source_id"])
def test_reference_citation_verification_rejects_modified_or_foreign_spans(
    reference_generation_case: dict, change: str,
) -> None:
    case = reference_generation_case
    evidence = case["evidence"]
    key = next(
        key for key in evidence["items"][0]["reference_evidence_ids"]
        if evidence["evidence_atoms"][key]["locator"]["kind"] == "reference_subcommand"
    )
    atom = evidence["evidence_atoms"][key]
    source = evidence["sources"][atom["source_id"]]
    citation = {
        "evidence_id": key, "source_id": atom["source_id"],
        "quote": atom["text"], "locator": deepcopy(atom["locator"]),
    }
    draft = {"sources": evidence["sources"], "items": [{"reference_notes": [{"citations": [citation]}]}]}
    if change == "html":
        path = Path(source["html_path"])
        path.write_bytes(path.read_bytes() + b"\nchanged")
    elif change == "quote":
        citation["quote"] += " changed"
    elif change == "locator":
        citation["locator"]["sources"][0]["anchor"] = "neighbor"
    elif change == "extractor":
        source["extractor_version"] += 1
    elif change == "source_id":
        source["document_id"] = "other-document"
    else:
        chapter = extract_command_chapter(Path(source["html_path"]).read_bytes(), {
            "order": 1, "title": source["chapter_title"], "url": source["chapter_url"],
        })
        neighbor = next(command for command in chapter["commands"] if command["name"] == "neighbor")
        foreign = deepcopy(atom)
        foreign["text"] = neighbor["sections"][0]["text"]
        foreign["locator"] = {
            "kind": "reference_section", "section_index": 1,
            "section_kind": neighbor["sections"][0]["kind"],
            "sources": neighbor["sections"][0]["sources"],
        }
        new_id = _atom_id(source, foreign["text"], foreign["locator"])
        foreign["evidence_id"] = new_id
        evidence["evidence_atoms"][new_id] = foreign
        citation.update(evidence_id=new_id, quote=foreign["text"], locator=foreign["locator"])
    with pytest.raises(ReferenceEvidenceError):
        verify_reference_citations(draft, evidence)
