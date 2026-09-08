#!/usr/bin/env -S uv run python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from qdrant_client import QdrantClient

from app.retrieval.indexer import OpenAIEmbeddingProvider
from app.retrieval.searcher import QdrantSearcher, search_result_payload
from app.settings import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Search the ConfigBrain Qdrant index")
    parser.add_argument("question", help="Natural-language question to search")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--collection")
    parser.add_argument("--product")
    parser.add_argument("--os-version")
    parser.add_argument("--qdrant-url")
    parser.add_argument("--qdrant-path", type=Path)
    args = parser.parse_args()

    settings = get_settings()
    root = Path.cwd()
    client = (
        QdrantClient(url=args.qdrant_url or settings.qdrant_url)
        if args.qdrant_url or settings.qdrant_url
        else QdrantClient(path=str((args.qdrant_path or root / settings.qdrant_path).resolve()))
    )
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required")
    searcher = QdrantSearcher(
        client,
        args.collection or settings.qdrant_collection,
        OpenAIEmbeddingProvider(settings.openai_api_key, settings.embedding_model),
    )
    results = searcher.search(
        args.question,
        limit=args.top_k,
        product=args.product,
        os_version=args.os_version,
    )
    print(json.dumps({"question": args.question, "results": search_result_payload(results)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())