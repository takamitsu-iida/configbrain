"""Reproduce the chapter-deduplicated Qdrant baseline for this evaluation."""

import json
from pathlib import Path

import torch
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
ALIAS = "c9300_iosxe26_manuals"
MODEL = "intfloat/multilingual-e5-small"
CHUNK_LIMIT = 1000


def main() -> None:
    data = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))
    items = [
        (case, item)
        for case in data["cases"]
        for item in case["items"]
    ]
    queries = [
        f"query: {case['requirement']}\n対象項目: {item['focus']}"
        for case, item in items
    ]
    torch.set_num_threads(2)
    model = SentenceTransformer(MODEL, device="cpu", local_files_only=True)
    vectors = model.encode(
        queries, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
    )
    results = []
    client = QdrantClient(path=str(ROOT / "data/qdrant/db"))
    try:
        aliases = {a.alias_name: a.collection_name for a in client.get_aliases().aliases}
        collection = aliases[ALIAS]
        count = client.count(collection_name=ALIAS, exact=True).count
        for (case, item), query, vector in zip(items, queries, vectors, strict=True):
            points = client.query_points(
                collection_name=ALIAS,
                query=vector.tolist(),
                limit=CHUNK_LIMIT,
                with_payload=True,
                with_vectors=False,
            ).points
            seen = set()
            candidates = []
            for rank, point in enumerate(points, start=1):
                payload = point.payload
                if payload is None:
                    raise ValueError(f"Missing payload for point {point.id}")
                if payload["product"] != data["product"] or payload["release"] != data["release"]:
                    raise ValueError(f"Unexpected product or release for point {point.id}")
                if payload["embedding_model"] != MODEL:
                    raise ValueError(f"Unexpected model for point {point.id}")
                chapter = (payload["document_id"], payload["chapter_url"])
                if chapter in seen:
                    continue
                seen.add(chapter)
                candidates.append({
                    "document_id": payload["document_id"],
                    "chapter_order": payload["chapter_order"],
                    "chapter_url": payload["chapter_url"],
                    "score": point.score,
                    "first_chunk_rank": rank,
                    "point_id": str(point.id),
                })
                if len(candidates) == 5:
                    break
            results.append({
                "case_id": case["id"],
                "item_id": item["id"],
                "query": query,
                "retrieved_chunks": len(points),
                "candidates": candidates,
            })
    finally:
        client.close()
    output = {
        "alias": ALIAS,
        "collection": collection,
        "point_count": count,
        "model": MODEL,
        "chunk_limit": CHUNK_LIMIT,
        "results": results,
    }
    (HERE / "qdrant-run.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Recorded {len(results)} queries against {count} points in {collection}")


if __name__ == "__main__":
    main()
