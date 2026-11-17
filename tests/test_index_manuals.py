from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from configbrain.index_manuals import (
    COLLECTION_ALIAS,
    ManualChapter,
    ManualDocument,
    _active_collection,
    _index_documents,
    build_chunks,
    extract_text_blocks,
    index_manuals,
)


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
    assert any(
        any(reference.get("table_index") == 1 for reference in chunk.payload["source_refs"])
        for chunk in chunks
    )


def test_local_qdrant_index_is_idempotent_and_replaces_active_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "qdrant"
    documents = (make_document(),)
    model = FakeEmbeddingModel()
    monkeypatch.setattr(
        "configbrain.index_manuals.load_validated_manuals",
        lambda *args: documents,
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
        "configbrain.index_manuals.load_validated_manuals",
        lambda *args: changed,
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
