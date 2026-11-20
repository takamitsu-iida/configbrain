"""Extract and validate source-linked metadata used only for chapter discovery."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from configbrain.index_manuals import (
    ManualChapter,
    TextBlock,
    extract_text_blocks,
    manual_markdown_parser,
)

METADATA_SCHEMA_VERSION = 1
MAX_SEGMENT_CHARACTERS = 6000
HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


class SelectionMetadataError(RuntimeError):
    """Raised when selection metadata is malformed or cannot be read."""


def _line_range(source_refs: list[dict[str, Any]]) -> tuple[int, int]:
    starts = [
        ref["okf_line_start"]
        for ref in source_refs
        if type(ref.get("okf_line_start")) is int
    ]
    ends = [
        ref["okf_line_end"]
        for ref in source_refs
        if type(ref.get("okf_line_end")) is int
    ]
    if not starts or not ends:
        raise SelectionMetadataError("An extracted section has no source line range.")
    return min(starts), max(ends)


def _portable_source_refs(source_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for source_ref in source_refs:
        portable = {
            key: value
            for key, value in source_ref.items()
            if key not in {"okf_line_start", "okf_line_end"}
        }
        if type(source_ref.get("okf_line_start")) is int:
            portable["chapter_line_start"] = source_ref["okf_line_start"]
        if type(source_ref.get("okf_line_end")) is int:
            portable["chapter_line_end"] = source_ref["okf_line_end"]
        result.append(portable)
    return result


def _bounded_blocks(blocks: tuple[TextBlock, ...]) -> list[TextBlock]:
    bounded = []
    for block in blocks:
        start = 0
        while len(block.text) - start > MAX_SEGMENT_CHARACTERS:
            end = start + MAX_SEGMENT_CHARACTERS
            boundary = max(
                block.text.rfind("\n", start + 1, end),
                block.text.rfind(" ", start + 1, end),
            )
            if boundary <= start:
                boundary = end
            bounded.append(TextBlock(
                text=block.text[start:boundary],
                kind=block.kind,
                section_path=block.section_path,
                source_refs=block.source_refs,
                source_range=block.source_range,
            ))
            start = boundary
        if start < len(block.text):
            bounded.append(TextBlock(
                text=block.text[start:],
                kind=block.kind,
                section_path=block.section_path,
                source_refs=block.source_refs,
                source_range=block.source_range,
            ))
    return bounded


def _heading_locations(markdown: str) -> list[dict[str, Any]]:
    parser = manual_markdown_parser()
    paths: list[str] = []
    pending: tuple[int, list[int] | None] | None = None
    headings: list[dict[str, Any]] = []
    for token in parser.parse(markdown):
        if token.type == "heading_open":
            pending = (int(token.tag.removeprefix("h")), token.map)
        elif token.type == "inline" and pending is not None:
            level, line_map = pending
            title = " ".join(
                BeautifulSoup(parser.renderInline(token.content), "html.parser")
                .get_text(" ", strip=True)
                .split()
            )
            paths = paths[: level - 1]
            if title:
                paths.append(title)
                if line_map:
                    headings.append({
                        "section_path": list(paths),
                        "source_range": {
                            "chapter_line_start": line_map[0] + 1,
                            "chapter_line_end": line_map[1],
                        },
                    })
            pending = None
    return headings


def extract_selection_segments(chapter: ManualChapter) -> list[dict[str, Any]]:
    """Return bounded section/procedure units with chapter-relative source ranges."""
    blocks = _bounded_blocks(extract_text_blocks(chapter.markdown))
    heading_locations = _heading_locations(chapter.markdown)
    grouped: list[tuple[tuple[str, ...], list[TextBlock]]] = []
    for block in blocks:
        if not grouped or grouped[-1][0] != block.section_path:
            grouped.append((block.section_path, []))
        grouped[-1][1].append(block)

    segments: list[dict[str, Any]] = []
    for section_path, section_blocks in grouped:
        batches: list[list[TextBlock]] = []
        current: list[TextBlock] = []
        current_size = 0
        for block in section_blocks:
            block_size = len(block.text)
            if current and current_size + block_size > MAX_SEGMENT_CHARACTERS:
                batches.append(current)
                current = []
                current_size = 0
            current.append(block)
            current_size += block_size
        if current:
            batches.append(current)

        for batch in batches:
            source_refs = [
                {
                    **source_ref,
                    **(block.source_range or {}),
                }
                for block in batch
                for source_ref in block.source_refs
            ]
            line_start, line_end = _line_range(source_refs)
            locations = [
                heading["source_range"]
                for heading in heading_locations
                if section_path[: len(heading["section_path"])]
                == tuple(heading["section_path"])
            ]
            segments.append({
                "section_id": f"c{chapter.order}-s{len(segments) + 1}",
                "section_path": list(section_path),
                "heading_locations": locations,
                "source_range": {
                    "chapter_line_start": line_start,
                    "chapter_line_end": line_end,
                },
                "source_refs": _portable_source_refs(source_refs),
                "source_text": "\n\n".join(block.text for block in batch),
            })
    return segments


def _read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SelectionMetadataError(f"Could not read {description}: {path}") from error
    if not isinstance(value, dict):
        raise SelectionMetadataError(f"{description} must be a JSON object: {path}")
    return value


def _require_text(value: Any, description: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SelectionMetadataError(f"{description} must be a nonempty string.")
    return value.strip()


def _validate_linked_texts(value: Any, section_ids: set[str], description: str) -> None:
    if not isinstance(value, list) or len(value) > 8:
        raise SelectionMetadataError(f"{description} must be an array.")
    for item in value:
        if not isinstance(item, dict) or set(item) != {"text", "section_ids"}:
            raise SelectionMetadataError(f"Invalid {description} entry.")
        text = _require_text(item["text"], f"{description} text")
        if len(text) > 200:
            raise SelectionMetadataError(f"{description} text is too long.")
        linked_ids = item["section_ids"]
        if (
            not isinstance(linked_ids, list)
            or not linked_ids
            or any(
                not isinstance(section_id, str) or section_id not in section_ids
                for section_id in linked_ids
            )
            or len(linked_ids) != len(set(linked_ids))
        ):
            raise SelectionMetadataError(f"Invalid source link in {description}.")


def validate_selection_metadata(value: dict[str, Any]) -> None:
    if value.get("schema_version") != METADATA_SCHEMA_VERSION:
        raise SelectionMetadataError("Unsupported selection metadata schema version.")
    _require_text(value.get("document_id"), "Metadata document ID")
    for key in ("source_manual_sha256", "source_pdf_sha256"):
        if not isinstance(value.get(key), str) or not HASH_PATTERN.fullmatch(value[key]):
            raise SelectionMetadataError(f"Invalid {key} in selection metadata.")
    _require_text(value.get("model"), "Metadata model")
    chapters = value.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise SelectionMetadataError("Metadata chapters must be a nonempty array.")
    seen_orders: set[int] = set()
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise SelectionMetadataError("Metadata chapter must be an object.")
        order = chapter.get("order")
        if type(order) is not int or order < 1 or order in seen_orders:
            raise SelectionMetadataError("Invalid or duplicate metadata chapter order.")
        seen_orders.add(order)
        _require_text(chapter.get("title"), "Metadata chapter title")
        _require_text(chapter.get("source_url"), "Metadata chapter source URL")
        if not isinstance(chapter.get("html_content_sha256"), str) or not HASH_PATTERN.fullmatch(
            chapter["html_content_sha256"]
        ):
            raise SelectionMetadataError("Invalid chapter HTML hash in selection metadata.")
        chapter_summary = _require_text(chapter.get("summary"), "Chapter metadata summary")
        if len(chapter_summary) > 500:
            raise SelectionMetadataError("Chapter metadata summary is too long.")
        sections = chapter.get("sections")
        if not isinstance(sections, list) or not sections:
            raise SelectionMetadataError("Metadata sections must be a nonempty array.")
        section_ids: set[str] = set()
        for section in sections:
            if not isinstance(section, dict):
                raise SelectionMetadataError("Metadata section must be an object.")
            section_id = _require_text(section.get("section_id"), "Section ID")
            if section_id in section_ids:
                raise SelectionMetadataError("Duplicate metadata section ID.")
            section_ids.add(section_id)
            path = section.get("section_path")
            if not isinstance(path, list) or any(
                not isinstance(part, str) or not part.strip() for part in path
            ):
                raise SelectionMetadataError("Invalid metadata section path.")
            source_range = section.get("source_range")
            if (
                not isinstance(source_range, dict)
                or type(source_range.get("chapter_line_start")) is not int
                or type(source_range.get("chapter_line_end")) is not int
                or source_range["chapter_line_start"] < 1
                or source_range["chapter_line_end"] < source_range["chapter_line_start"]
            ):
                raise SelectionMetadataError("Invalid metadata source range.")
            refs = section.get("source_refs")
            if not isinstance(refs, list) or not refs:
                raise SelectionMetadataError("Metadata source references must be nonempty.")
            section_summary = _require_text(section.get("summary"), "Section metadata summary")
            if len(section_summary) > 300:
                raise SelectionMetadataError("Section metadata summary is too long.")
            for key in ("operations", "answerable_questions"):
                values = section.get(key)
                if (
                    not isinstance(values, list)
                    or len(values) > 3
                    or any(
                        not isinstance(text, str)
                        or not text.strip()
                        or len(text) > 150
                        for text in values
                    )
                ):
                    raise SelectionMetadataError(f"Invalid section {key}.")
        _validate_linked_texts(chapter.get("operations"), section_ids, "chapter operations")
        _validate_linked_texts(
            chapter.get("answerable_questions"), section_ids, "chapter questions"
        )


def load_selection_metadata(
    documents: list[dict[str, Any]],
    chapter_catalog: dict[tuple[str, int], dict[str, Any]],
    metadata_root: Path,
    okf_root: Path,
) -> tuple[dict[str, dict[int, dict[str, Any]]], list[str]]:
    """Load only metadata that still matches its converted manual and catalog."""
    if metadata_root.is_symlink():
        raise SelectionMetadataError(
            f"Selection metadata root must not be a symlink: {metadata_root}"
        )
    if metadata_root.exists() and not metadata_root.is_dir():
        raise SelectionMetadataError(
            f"Selection metadata root is not a directory: {metadata_root}"
        )
    loaded: dict[str, dict[int, dict[str, Any]]] = {}
    warnings: list[str] = []
    for document in documents:
        if document.get("document_type", "configuration_guide") != "configuration_guide":
            continue
        document_id = document["document_id"]
        metadata_path = metadata_root / f"{document_id}.json"
        if metadata_path.is_symlink():
            raise SelectionMetadataError(f"Unsafe selection metadata path: {metadata_path}")
        if not metadata_path.exists():
            warnings.append(
                f"Selection metadata is missing for {document_id}; chapter titles only."
            )
            continue
        if not metadata_path.is_file():
            raise SelectionMetadataError(f"Unsafe selection metadata path: {metadata_path}")
        metadata = _read_json(metadata_path, "selection metadata")
        validate_selection_metadata(metadata)
        if metadata["document_id"] != document_id:
            raise SelectionMetadataError(f"Metadata document ID mismatch: {document_id}")
        package_dir = okf_root / document_id
        manual_path = package_dir / "manual.md"
        provenance_path = package_dir / "provenance.json"
        if (
            package_dir.is_symlink()
            or manual_path.is_symlink()
            or provenance_path.is_symlink()
            or not manual_path.is_file()
            or not provenance_path.is_file()
        ):
            warnings.append(
                f"Selection metadata cannot be verified for {document_id}; chapter titles only."
            )
            continue
        try:
            manual_sha256 = hashlib.sha256(manual_path.read_bytes()).hexdigest()
        except OSError as error:
            raise SelectionMetadataError(f"Could not read converted manual: {manual_path}") from error
        provenance = _read_json(provenance_path, "manual provenance")
        source_pdf = provenance.get("source_pdf")
        source_pdf_sha256 = source_pdf.get("sha256") if isinstance(source_pdf, dict) else None
        if (
            metadata["source_manual_sha256"] != manual_sha256
            or metadata["source_pdf_sha256"] != source_pdf_sha256
        ):
            warnings.append(
                f"Selection metadata is stale for {document_id}; chapter titles only."
            )
            continue
        provenance_chapters = provenance.get("chapters")
        if not isinstance(provenance_chapters, list):
            warnings.append(
                f"Selection metadata is stale for {document_id}; chapter titles only."
            )
            continue
        provenance_by_order = {
            chapter.get("order"): chapter
            for chapter in provenance_chapters
            if isinstance(chapter, dict)
        }
        if len(provenance_by_order) != len(provenance_chapters):
            warnings.append(
                f"Selection metadata is stale for {document_id}; chapter titles only."
            )
            continue
        metadata_by_order = {
            chapter["order"]: chapter for chapter in metadata["chapters"]
        }
        expected_chapters = document.get("chapters", [])
        if len(metadata_by_order) != len(expected_chapters):
            warnings.append(
                f"Selection metadata is stale for {document_id}; chapter titles only."
            )
            continue
        stale = False
        for expected in expected_chapters:
            order = expected["order"]
            generated = metadata_by_order.get(order)
            current = provenance_by_order.get(order)
            catalog_chapter = chapter_catalog.get((document_id, order))
            if (
                generated is None
                or current is None
                or catalog_chapter is None
                or generated["title"] != expected["title"]
                or generated["title"] != current.get("title")
                or generated["source_url"] != catalog_chapter.get("url")
                or generated["source_url"] != current.get("html_url")
                or generated["html_content_sha256"] != current.get("html_content_sha256")
            ):
                stale = True
                break
        if stale:
            warnings.append(
                f"Selection metadata is stale for {document_id}; chapter titles only."
            )
            continue
        loaded[document_id] = metadata_by_order
    return loaded, warnings


def metadata_document(
    document_id: str,
    manual_bytes: bytes,
    source_pdf_sha256: str,
    model: str,
    chapters: list[dict[str, Any]],
) -> dict[str, Any]:
    value = {
        "schema_version": METADATA_SCHEMA_VERSION,
        "document_id": document_id,
        "source_manual_sha256": hashlib.sha256(manual_bytes).hexdigest(),
        "source_pdf_sha256": source_pdf_sha256,
        "model": model,
        "chapters": chapters,
    }
    validate_selection_metadata(value)
    return value
