#!/usr/bin/env -S uv run python
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from qdrant_client import QdrantClient

from app.ingestion.chunker import SemanticChunker
from app.ingestion.pdf_loader import PdfLoader
from app.retrieval.indexer import DocumentMetadata, OpenAIEmbeddingProvider, QdrantIndexer
from app.settings import get_settings


def load_manual_records(root: Path) -> list[dict[str, object]]:
    metadata_path = root / "data" / "manuals.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Manual metadata not found: {metadata_path}")
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def select_manuals(root: Path, pdf: Path | None, all_manuals: bool) -> list[dict[str, object]]:
    records = load_manual_records(root)
    if pdf is not None:
        resolved = pdf if pdf.is_absolute() else root / pdf
        for record in records:
            if root / "data" / "raw" / str(record["filename"]) == resolved:
                return [record]
        raise ValueError(f"PDF is not listed in data/manuals.json: {resolved}")
    if all_manuals:
        return records
    raise ValueError("Specify --pdf PATH or --all")


def index_manuals(
    root: Path,
    records: list[dict[str, object]],
    *,
    qdrant_url: str | None,
    qdrant_path: Path,
    collection: str,
    embedding_model: str,
    api_key: str | None,
    batch_size: int,
    dry_run: bool,
    recreate: bool,
) -> int:
    prepared: list[tuple[dict[str, object], list]] = []
    chunker = SemanticChunker()
    for record in records:
        pdf_path = root / "data" / "raw" / str(record["filename"])
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        pages = PdfLoader(pdf_path, str(record["document_id"])).load()
        chunks = chunker.chunk_pages(pages)
        prepared.append((record, chunks))
        print(f"Prepared {record['document_id']}: pages={len(pages)} chunks={len(chunks)}")

    total = sum(len(chunks) for _, chunks in prepared)
    if dry_run:
        return total

    client = QdrantClient(url=qdrant_url) if qdrant_url else QdrantClient(path=str(qdrant_path))
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")
    if recreate and client.collection_exists(collection):
        client.delete_collection(collection)
    provider = OpenAIEmbeddingProvider(api_key=api_key, model=embedding_model)
    indexer = QdrantIndexer(client, collection, provider)
    for record, chunks in prepared:
        metadata = DocumentMetadata(
            document_title=str(record["title"]),
            source_url=str(record["url"]),
        )
        indexed = indexer.index_chunks(chunks, metadata, batch_size=batch_size)
        print(f"Indexed {record['document_id']}: points={indexed}")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the ConfigBrain Qdrant index")
    parser.add_argument("--pdf", type=Path, help="Index one PDF listed in data/manuals.json")
    parser.add_argument("--all", action="store_true", help="Index all downloaded manuals")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--qdrant-url",
        help="Use a Qdrant server instead of the local persistent store",
    )
    parser.add_argument(
        "--qdrant-path",
        type=Path,
        help="Local persistent Qdrant path (default: data/qdrant)",
    )
    parser.add_argument("--collection")
    parser.add_argument("--embedding-model")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--dry-run", action="store_true", help="Parse and chunk without external services")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete the existing collection before indexing",
    )
    args = parser.parse_args()
    if len(sys.argv) == 1:
        parser.print_help()
        return 0
    if args.pdf and args.all:
        parser.error("--pdf and --all cannot be used together")

    settings = get_settings()
    root = args.root.resolve()
    records = select_manuals(root, args.pdf, args.all)
    total = index_manuals(
        root,
        records,
        qdrant_url=args.qdrant_url or settings.qdrant_url,
        qdrant_path=(args.qdrant_path or root / settings.qdrant_path).resolve(),
        collection=args.collection or settings.qdrant_collection,
        embedding_model=args.embedding_model or settings.embedding_model,
        api_key=settings.openai_api_key,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        recreate=args.recreate,
    )
    print(f"Completed: chunks={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
