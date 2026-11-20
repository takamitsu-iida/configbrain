"""Search selected guide chapters and defer reference chapters to command lookup."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient, models

from configbrain.command_references import CommandReferenceIndex
from configbrain.manual_errors import ConversionError
from configbrain.index_manuals import (
    COLLECTION_ALIAS,
    EMBEDDING_MODEL,
    EmbeddingModel,
    IndexingError,
    _active_collection,
    _load_embedding_model,
)
from configbrain.select_manuals import (
    SelectionError,
    _write_output,
    load_catalog,
    validate_selection,
)


class SearchError(RuntimeError):
    """Raised when selected chapters cannot be searched safely."""


IPV4_CIDR = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}(?!\d)")


def _load_selection(path: Path, catalog_path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        selection = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SearchError(f"Could not read selection JSON: {path}") from error
    if not isinstance(selection, dict):
        raise SearchError("Selection must be a JSON object.")
    if selection.get("product") != "catalyst9300" or selection.get("release") != "26.x":
        raise SearchError("Selection must target catalyst9300 / 26.x.")
    if not isinstance(selection.get("requirement"), str) or not selection["requirement"].strip():
        raise SearchError("Selection requirement must be a nonempty string.")
    catalog = load_catalog(catalog_path, selection["product"], selection["release"])
    if selection.get("catalog_sha256") != catalog.sha256:
        raise SearchError("Selection catalog SHA-256 does not match the current catalog.")
    items = validate_selection(selection, catalog)
    document_types = {
        entry["document_id"]: entry.get("document_type") for entry in catalog.documents
    }
    for original_item, item in zip(selection["items"], items, strict=True):
        for original, candidate in zip(
            original_item["candidates"], item["candidates"], strict=True
        ):
            if any(
                original.get(key) != candidate[key]
                for key in ("chapter_title", "chapter_url")
            ):
                raise SearchError("Selected chapter title or URL does not match the catalog.")
            if document_types[candidate["document_id"]] is not None:
                candidate["document_type"] = document_types[candidate["document_id"]]
    return {**selection, "items": items}, hashlib.sha256(raw).hexdigest()


def _chapter_filter(
    selection: dict[str, Any], candidate: dict[str, Any]
) -> models.Filter:
    fields = {
        "product": selection["product"],
        "release": selection["release"],
        "document_id": candidate["document_id"],
        "chapter_order": candidate["chapter_order"],
        "chapter_url": candidate["chapter_url"],
    }
    return models.Filter(
        must=[
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in fields.items()
        ]
    )


def _query_text(requirement: str) -> str:
    query = f"query: {requirement}"
    if IPV4_CIDR.search(requirement):
        query += " IP address interface subnet mask"
    return query


def _validate_payload(payload: dict[str, Any], candidate: dict[str, Any]) -> None:
    required = (
        "dataset_fingerprint", "document_id", "document_title", "product", "release",
        "source_pdf_sha256", "chapter_order", "chapter_title", "chapter_url",
        "html_content_sha256", "pdf_page_index", "pdf_mapping_status", "pdf_page_scope",
        "section_path", "source_kind", "source_refs", "chunk_index", "text_sha256",
        "embedding_model", "text",
    )
    if any(key not in payload for key in required):
        raise SearchError("Index is missing source metadata; reindex the manuals.")
    if payload["embedding_model"] != EMBEDDING_MODEL:
        raise SearchError("Index embedding model does not match the search model.")
    if payload["chapter_title"] != candidate["chapter_title"]:
        raise SearchError("Indexed chapter title does not match the selected catalog.")
    expected_scope = "unmapped" if payload["pdf_page_index"] is None else "chapter_start"
    if payload["pdf_page_scope"] != expected_scope:
        raise SearchError("Index PDF page scope is invalid; reindex the manuals.")
    if (
        not isinstance(payload["text"], str) or not payload["text"].strip()
        or not isinstance(payload["source_refs"], list) or not payload["source_refs"]
        or not isinstance(payload["section_path"], list)
    ):
        raise SearchError("Index contains invalid text or source metadata.")


def _item_status(chapters: list[dict[str, Any]]) -> str:
    if not chapters:
        return "no_candidates"
    if any(chapter["matches"] for chapter in chapters):
        return "retrieved"
    if any(chapter["status"] == "command_lookup_pending" for chapter in chapters):
        return "command_lookup_pending"
    return "no_matches"


def _reference_chapter(
    candidate: dict[str, Any], rank: int, metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        **candidate, "document_type": "command_reference", "selection_rank": rank,
        "status": "command_lookup_pending", "search_method": "guide_command_lookup",
        "reference_metadata": metadata, "matches": [],
    }


def search_manuals(
    selection_path: Path,
    catalog_path: Path = Path("data/catalog/c9300-iosxe26.json"),
    database_path: Path = Path("data/qdrant/db"),
    limit: int = 20,
    *,
    model: EmbeddingModel | None = None,
    reference_root: Path = Path("data/command_references"),
) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise SearchError("Search limit must be a positive integer.")
    selection, selection_sha256 = _load_selection(selection_path, catalog_path)
    reference_metadata: dict[str, dict[str, Any]] = {}
    items = selection["items"]
    for item in items:
        for candidate in item["candidates"]:
            if candidate.get("document_type") != "command_reference":
                continue
            document_id = candidate["document_id"]
            if document_id not in reference_metadata:
                try:
                    reference_metadata[document_id] = CommandReferenceIndex.load(
                        catalog_path, document_id, reference_root,
                    ).fingerprint_metadata()
                except ConversionError as error:
                    raise SearchError(f"Invalid command reference: {error}") from error
    has_guides = any(
        candidate.get("document_type") != "command_reference"
        for item in items for candidate in item["candidates"]
    )
    client = None
    try:
        collection = None
        if has_guides:
            if not (database_path / "meta.json").is_file():
                raise SearchError(f"Qdrant database does not exist: {database_path}")
            client = QdrantClient(path=str(database_path))
            collection = _active_collection(client)
            if collection is None:
                raise SearchError(f"Qdrant alias does not exist: {COLLECTION_ALIAS}")
        filters: dict[tuple[str, int], models.Filter] = {}
        generation: str | None = None
        for item in items:
            for candidate in item["candidates"]:
                if candidate.get("document_type") == "command_reference":
                    continue
                if client is None or collection is None:
                    raise SearchError("Guide search requires an active Qdrant collection.")
                key = (candidate["document_id"], candidate["chapter_order"])
                if key in filters:
                    continue
                chapter_filter = _chapter_filter(selection, candidate)
                records, _ = client.scroll(
                    collection, scroll_filter=chapter_filter, limit=1,
                    with_payload=True, with_vectors=False,
                )
                if not records:
                    raise SearchError(
                        f"Selected chapter is not indexed: {key[0]}/{key[1]}. "
                        "Reindex the current catalog."
                    )
                payload = records[0].payload or {}
                _validate_payload(payload, candidate)
                fingerprint = payload["dataset_fingerprint"]
                if not isinstance(fingerprint, str) or not fingerprint:
                    raise SearchError("Index dataset fingerprint is invalid.")
                if generation is not None and generation != fingerprint:
                    raise SearchError("Selected chapters belong to different index generations.")
                generation = fingerprint
                filters[key] = chapter_filter

        embedding_model = (model or _load_embedding_model(EMBEDDING_MODEL)) if filters else None
        results: list[dict[str, Any]] = []
        for item in items:
            chapters: list[dict[str, Any]] = []
            vector = None
            guide_candidates = [
                candidate for candidate in item["candidates"]
                if candidate.get("document_type") != "command_reference"
            ]
            if guide_candidates and embedding_model is not None:
                query = _query_text(item["requirement"])
                if len(embedding_model.tokenizer.encode(query)) > embedding_model.max_seq_length:
                    raise SearchError("Requirement item exceeds the embedding model context.")
                vectors = embedding_model.encode(
                    [query], batch_size=1, normalize_embeddings=True,
                    convert_to_numpy=True, show_progress_bar=False,
                )
                if len(vectors) != 1:
                    raise SearchError("Search model returned an invalid embedding count.")
                vector = [float(value) for value in vectors[0]]
                if (
                    len(vector) != embedding_model.get_embedding_dimension()
                    or not vector or not all(math.isfinite(value) for value in vector)
                    or not any(vector)
                ):
                    raise SearchError("Search model returned an invalid embedding vector.")
            for rank, candidate in enumerate(item["candidates"], start=1):
                if candidate.get("document_type") == "command_reference":
                    chapters.append(_reference_chapter(
                        candidate, rank, reference_metadata[candidate["document_id"]],
                    ))
                    continue
                if client is None or collection is None or vector is None:
                    raise SearchError("Guide search requires an active embedding model and index.")
                key = (candidate["document_id"], candidate["chapter_order"])
                hits = client.query_points(
                    collection_name=collection, query=vector,
                    query_filter=filters[key], limit=limit,
                    with_payload=True, with_vectors=False,
                ).points
                matches = []
                for hit_rank, hit in enumerate(hits, start=1):
                    payload = hit.payload or {}
                    _validate_payload(payload, candidate)
                    if payload["dataset_fingerprint"] != generation:
                        raise SearchError("Search results contain mixed index generations.")
                    if not math.isfinite(hit.score):
                        raise SearchError("Qdrant returned a non-finite score.")
                    matches.append({
                        "rank": hit_rank, "point_id": str(hit.id),
                        "score": hit.score, **payload,
                    })
                chapters.append({
                    **candidate, "selection_rank": rank,
                    "status": "retrieved" if matches else "no_matches",
                    "matches": matches,
                })
            results.append({
                "requirement": item["requirement"],
                "uncertainty": item["uncertainty"],
                "status": _item_status(chapters),
                "chapters": chapters,
            })
        return {
            "schema_version": 1,
            "product": selection["product"], "release": selection["release"],
            "requirement": selection["requirement"],
            "catalog_sha256": selection["catalog_sha256"],
            "selection_sha256": selection_sha256,
            "embedding_model": EMBEDDING_MODEL,
            "collection_name": collection, "dataset_fingerprint": generation,
            "limit_per_chapter": limit,
            "evidence_status": "candidates_only",
            "items": results,
        }
    finally:
        if client is not None:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog/c9300-iosxe26.json"))
    parser.add_argument("--database", type=Path, default=Path("data/qdrant/db"))
    parser.add_argument("--reference-root", type=Path, default=Path("data/command_references"))
    parser.add_argument("--limit", type=int, default=20, help="Maximum hits per selected chapter.")
    parser.add_argument("--output", type=Path, help="Write JSON instead of stdout.")
    args = parser.parse_args(argv)
    try:
        if args.output:
            output = args.output.resolve()
            if (
                output in {args.selection.resolve(), args.catalog.resolve(), Path(".env").resolve()}
                or output.is_relative_to(args.database.resolve())
                or output.is_relative_to(args.reference_root.resolve())
            ):
                raise SearchError("Output must not overwrite an input, configuration, or database file.")
        result = search_manuals(
            args.selection, args.catalog, args.database, args.limit,
            reference_root=args.reference_root,
        )
        content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            _write_output(args.output, content)
        else:
            sys.stdout.write(content)
    except (SearchError, SelectionError, IndexingError, OSError, RuntimeError, ValueError) as error:
        print(f"Manual search failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
