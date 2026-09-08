#!/usr/bin/env -S uv run python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from qdrant_client import QdrantClient

from app.ingestion.html_blocks import write_html_blocks, write_llm_candidates
from app.ingestion.chunker import HtmlChunker
from app.ingestion.html_loader import HtmlLoader
from app.retrieval.indexer import DocumentMetadata, OpenAIEmbeddingProvider, QdrantIndexer
from app.settings import get_settings


def load_manual_records(root: Path) -> list[dict[str, object]]:
    metadata_path = root / "data" / "manuals.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Manual metadata not found: {metadata_path}")
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def html_entry_url(record: dict[str, object]) -> str:
    return str(record["url"]).removesuffix(".pdf") + ".html"


def index_html_manuals(
    root: Path,
    records: list[dict[str, object]],
    *,
    qdrant_url: str | None,
    qdrant_path: Path,
    collection: str,
    embedding_model: str,
    api_key: str | None,
    recreate: bool,
    dry_run: bool,
    rendered: bool,
) -> int:
    prepared: list[tuple[dict[str, object], list]] = []
    all_sections = []
    chunker = HtmlChunker()
    for record in records:
        entry_url = html_entry_url(record)
        loader = (
            HtmlLoader.rendered(
                entry_url,
                str(record["document_id"]),
                storage_dir=root / "data" / "raw" / "html",
                rendered_storage_dir=root / "data" / "raw" / "rendered_html",
                metadata_path=root / "data" / "raw" / "html_fetches.jsonl",
            )
            if rendered
            else HtmlLoader(
                entry_url,
                str(record["document_id"]),
                storage_dir=root / "data" / "raw" / "html",
                metadata_path=root / "data" / "raw" / "html_fetches.jsonl",
            )
        )
        sections = loader.load()
        all_sections.extend(sections)
        chunks = chunker.chunk_sections(sections)
        prepared.append((record, chunks))
        print(
            f"Prepared {record['document_id']}: sections={len(sections)} chunks={len(chunks)}"
        )

    total = sum(len(chunks) for _, chunks in prepared)
    block_path = root / "data" / "processed" / "html_blocks.jsonl"
    write_html_blocks(block_path, all_sections)
    write_llm_candidates(
        root / "data" / "processed" / "html_llm_candidates.jsonl",
        all_sections,
    )
    if dry_run:
        return total
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")

    client = QdrantClient(url=qdrant_url) if qdrant_url else QdrantClient(path=str(qdrant_path))
    if recreate and client.collection_exists(collection):
        client.delete_collection(collection)
    provider = OpenAIEmbeddingProvider(api_key=api_key, model=embedding_model)
    indexer = QdrantIndexer(client, collection, provider)
    for record, chunks in prepared:
        indexer.index_chunks(
            chunks,
            DocumentMetadata(
                document_title=str(record["title"]),
                source_url=html_entry_url(record),
            ),
        )
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the ConfigBrain HTML Qdrant index")
    parser.add_argument("--all", action="store_true", help="Index all configured HTML manuals")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--qdrant-url")
    parser.add_argument("--qdrant-path", type=Path)
    parser.add_argument("--collection")
    parser.add_argument("--embedding-model")
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rendered", action="store_true", help="Use headless Playwright DOM retrieval")
    args = parser.parse_args()
    if not args.all:
        parser.error("Specify --all")

    settings = get_settings()
    root = args.root.resolve()
    total = index_html_manuals(
        root,
        load_manual_records(root),
        qdrant_url=args.qdrant_url or settings.qdrant_url,
        qdrant_path=(args.qdrant_path or root / settings.qdrant_path).resolve(),
        collection=args.collection or settings.html_qdrant_collection,
        embedding_model=args.embedding_model or settings.embedding_model,
        api_key=settings.openai_api_key,
        recreate=args.recreate,
        dry_run=args.dry_run,
        rendered=args.rendered,
    )
    print(f"Completed: chunks={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())