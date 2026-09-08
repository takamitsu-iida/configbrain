from collections.abc import Sequence

from qdrant_client import QdrantClient

from app.ingestion.chunker import Chunk
from app.retrieval.indexer import DocumentMetadata, QdrantIndexer
from app.retrieval.searcher import QdrantSearcher


class FakeEmbeddingProvider:
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] if "vlan" in text.lower() else [0.0, 1.0, 0.0] for text in texts]


def test_searcher_returns_citations_and_applies_metadata_filters() -> None:
    client = QdrantClient(":memory:")
    provider = FakeEmbeddingProvider()
    QdrantIndexer(client, "documents", provider).index_chunks(
        [
            Chunk("doc-vlan", 42, "Configuring VLANs", "vlan 100", "configuration_example"),
            Chunk("doc-int", 7, "Interfaces", "interface GigabitEthernet1/0/1", "configuration_example"),
        ],
        DocumentMetadata(
            document_title="VLAN Guide",
            source_url="https://example.test/vlan.pdf",
        ),
    )
    searcher = QdrantSearcher(client, "documents", provider)

    results = searcher.search("How do I configure VLAN 100?", limit=1, product="catalyst9300")

    assert len(results) == 1
    assert results[0].text == "vlan 100"
    assert results[0].metadata["document_id"] == "doc-vlan"
    assert results[0].metadata["page_number"] == 42
    assert results[0].metadata["section_title"] == "Configuring VLANs"
    assert results[0].metadata["source_url"] == "https://example.test/vlan.pdf"


def test_searcher_rejects_invalid_questions() -> None:
    searcher = QdrantSearcher(QdrantClient(":memory:"), "documents", FakeEmbeddingProvider())

    try:
        searcher.search("  ")
    except ValueError as error:
        assert str(error) == "question must not be empty"
    else:
        raise AssertionError("expected ValueError")


def test_searcher_reranks_different_configuration_targets() -> None:
    class SameVectorProvider:
        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0] for _ in texts]

    client = QdrantClient(":memory:")
    provider = SameVectorProvider()
    QdrantIndexer(client, "documents", provider).index_chunks(
        [
            Chunk("normal", 1, "Creating VLANs", "Device(config)# vlan 100", "configuration_example"),
            Chunk("pvlan", 2, "Configuring PVLANs", "Device(config)# pvlan community", "configuration_example"),
            Chunk("svi", 3, "Configuring Interface VLANs", "Device(config)# interface vlan 100", "configuration_example"),
        ],
        DocumentMetadata(),
    )

    results = QdrantSearcher(client, "documents", provider).search(
        "VLAN 100を作成する方法は？", limit=3
    )

    assert [result.metadata["document_id"] for result in results] == [
        "normal",
        "svi",
        "pvlan",
    ]