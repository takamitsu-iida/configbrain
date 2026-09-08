from collections.abc import Sequence
from qdrant_client import QdrantClient

from app.ingestion.chunker import Chunk
from app.retrieval.indexer import DocumentMetadata, QdrantIndexer


class FakeEmbeddingProvider:
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0, 0.5] for text in texts]


def test_indexer_generates_vectors_and_preserves_metadata() -> None:
    client = QdrantClient(":memory:")
    indexer = QdrantIndexer(client, "test_collection", FakeEmbeddingProvider())
    chunks = [
        Chunk("doc-1", 4, "VLANs", "vlan 100", "configuration_example"),
        Chunk("doc-1", 5, "VLANs", "show vlan brief", "command_reference"),
    ]

    indexed = indexer.index_chunks(
        chunks,
        DocumentMetadata(document_title="VLAN Configuration Guide", source_url="https://example.test/vlan.pdf"),
    )

    assert indexed == 2
    points, _ = client.scroll("test_collection", limit=2, with_vectors=True)
    assert len(points) == 2
    assert all(len(point.vector) == 3 for point in points)
    assert points[0].payload["document_id"] == "doc-1"
    assert points[0].payload["page_number"] in {4, 5}
    assert points[0].payload["source_url"] == "https://example.test/vlan.pdf"


def test_indexer_upsert_is_idempotent_for_same_chunks() -> None:
    client = QdrantClient(":memory:")
    indexer = QdrantIndexer(client, "test_collection", FakeEmbeddingProvider())
    chunk = Chunk("doc-1", 1, "Overview", "same text", "concept")

    indexer.index_chunks([chunk], DocumentMetadata())
    indexer.index_chunks([chunk], DocumentMetadata())

    records, _ = client.scroll("test_collection", limit=10)
    assert len(records) == 1


def test_indexer_ids_are_stable_when_batching_or_order_changes() -> None:
    client = QdrantClient(":memory:")
    indexer = QdrantIndexer(client, "test_collection", FakeEmbeddingProvider())
    chunks = [
        Chunk("doc-1", 1, "Overview", "first", "concept"),
        Chunk("doc-1", 2, "VLANs", "second", "concept"),
    ]

    indexer.index_chunks(chunks, DocumentMetadata(), batch_size=1)
    indexer.index_chunks(list(reversed(chunks)), DocumentMetadata(), batch_size=64)

    records, _ = client.scroll("test_collection", limit=10)
    assert len(records) == 2
