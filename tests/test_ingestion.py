from collections.abc import Sequence
from pathlib import Path

import pymupdf
from qdrant_client import QdrantClient

from app.ingestion.chunker import SemanticChunker
from app.ingestion.chunker import HtmlChunker
from app.ingestion.html_loader import HtmlSection
from app.ingestion.pdf_loader import PdfLoader
from app.retrieval.indexer import DocumentMetadata, QdrantIndexer


class FakeEmbeddingProvider:
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0, 0.5] for text in texts]


def test_pdf_ingestion_creates_searchable_points_with_required_metadata(tmp_path: Path) -> None:
    pdf_path = tmp_path / "manual.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Configuring VLANs\n"
        "Create a VLAN before assigning an access port.\n"
        "vlan 100\n"
        " name USERS\n",
    )
    document.save(pdf_path)
    document.close()

    pages = PdfLoader(pdf_path, "doc-1").load()
    chunks = SemanticChunker().chunk_pages(pages)
    client = QdrantClient(":memory:")
    indexer = QdrantIndexer(client, "documents", FakeEmbeddingProvider())

    indexed = indexer.index_chunks(
        chunks,
        DocumentMetadata(
            document_title="Test VLAN Guide",
            source_url="https://example.test/manual.pdf",
        ),
    )

    assert indexed == len(chunks)
    points, _ = client.scroll("documents", limit=100, with_vectors=True)
    assert len(points) == len(chunks)
    assert all(point.vector for point in points)

    required_fields = {
        "document_id",
        "vendor",
        "product",
        "os",
        "os_version",
        "document_title",
        "document_version",
        "section_title",
        "page_number",
        "source_url",
        "content_type",
        "ingested_at",
    }
    for point in points:
        assert required_fields <= point.payload.keys()
        assert point.payload["document_id"] == "doc-1"
        assert point.payload["page_number"] == 1
        assert point.payload["document_title"] == "Test VLAN Guide"
        assert point.payload["source_url"] == "https://example.test/manual.pdf"
        assert point.payload["text"]


def test_html_ingestion_registers_section_url_metadata() -> None:
    section = HtmlSection(
        document_id="doc-html",
        section_url="https://example.test/guide/vlans.html#creating",
        section_title="Creating VLANs",
        text="Create a VLAN.",
        headings=("Creating VLANs",),
        code_blocks=("vlan 100",),
        tables=(),
    )
    chunks = HtmlChunker().chunk_sections([section])
    client = QdrantClient(":memory:")

    indexed = QdrantIndexer(client, "html_documents", FakeEmbeddingProvider()).index_chunks(
        chunks,
        DocumentMetadata(document_title="HTML VLAN Guide", source_url=section.section_url),
    )

    assert indexed == 1
    points, _ = client.scroll("html_documents", limit=10)
    assert {point.payload["section_url"] for point in points} == {section.section_url}
    assert all(point.payload["page_number"] is None for point in points)