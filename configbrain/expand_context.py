"""Recover full manual sections and contextual cautions from search results."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from configbrain.index_manuals import (
    CHAPTER_MARKER,
    EMBEDDING_MODEL,
    IndexingError,
    ManualChapter,
    ManualDocument,
    _dataset_fingerprint,
    _visible_html_text,
    load_validated_manuals,
    manual_markdown_parser,
)
from configbrain.search_manuals import SearchError, _validate_payload
from configbrain.select_manuals import (
    SelectionError,
    _write_output,
    load_catalog,
    validate_selection,
)


class ContextError(RuntimeError):
    """Raised when search hits cannot be traced to the current manual source."""


PROCEDURE_TITLES = {"procedure", "summary steps", "detailed steps"}
CAUTION_TITLE = re.compile(
    r"\b(prerequisites?|restrictions?|guidelines?|limitations?|cautions?|"
    r"warnings?|notes?|important|considerations?)\b|^before you begin$",
    re.IGNORECASE,
)


@dataclass
class Section:
    path: tuple[str, ...]
    level: int
    start: int
    body_start: int
    end: int
    parent: Section | None = field(default=None, repr=False)
    children: list[Section] = field(default_factory=list)

    @property
    def is_task(self) -> bool:
        return any(
            child.path and child.path[-1].casefold() in PROCEDURE_TITLES
            for child in self.children
        )


def chapter_sections(markdown: str) -> list[Section]:
    parser = manual_markdown_parser()
    root = Section((), 0, 0, 0, len(markdown.splitlines()))
    sections = [root]
    stack = [root]
    path: list[str] = []
    tokens = parser.parse(markdown)
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.map is None:
            continue
        level = int(token.tag.removeprefix("h"))
        title = _visible_html_text(parser.renderInline(tokens[index + 1].content))
        # Match the section paths already stored by the indexer, including skipped levels.
        path = path[:level - 1]
        if title:
            path.append(title)
        while stack[-1].level >= level:
            stack.pop().end = token.map[0]
        section = Section(tuple(path), level, token.map[0], token.map[1], root.end, stack[-1])
        stack[-1].children.append(section)
        sections.append(section)
        stack.append(section)
    return sections


def _ancestors(section: Section) -> list[Section]:
    result = []
    parent = section.parent
    while parent is not None:
        result.append(parent)
        parent = parent.parent
    return result


def _locate_section(hit: dict[str, Any], sections: list[Section]) -> Section:
    path = hit["section_path"]
    if not all(isinstance(part, str) and part for part in path):
        raise ContextError("Search hit section path is invalid.")
    candidates = [section for section in sections if section.path == tuple(path)]
    references = hit["source_refs"]
    if not all(isinstance(reference, dict) for reference in references):
        raise ContextError("Search hit source references are invalid.")
    ranges = []
    for reference in references:
        if "okf_line_start" in reference or "okf_line_end" in reference:
            start, end = reference.get("okf_line_start"), reference.get("okf_line_end")
            if (
                type(start) is not int or type(end) is not int
                or start < 1 or end < start
            ):
                raise ContextError("Search hit source line range is invalid.")
            ranges.append((start, end))
    if ranges:
        candidates = [
            section for section in candidates
            if all(section.body_start < start <= end <= section.end for start, end in ranges)
        ]
    if len(candidates) != 1:
        raise ContextError(
            f"Search section is missing or ambiguous in OKF: {' > '.join(path)}"
        )
    return candidates[0]


def expand_chapter(
    hits: list[dict[str, Any]], chapter: ManualChapter, manual_body_line: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lines = chapter.markdown.splitlines(keepends=True)
    sections = chapter_sections(chapter.markdown)
    fragments: dict[tuple[int, int], dict[str, Any]] = {}
    expanded_hits: list[dict[str, Any]] = []

    def add(section: Section, end: int, role: str) -> str:
        key = (section.start, end)
        if key not in fragments:
            context_id = f"lines-{section.start + 1}-{end}"
            fragments[key] = {
                "context_id": context_id, "section_path": list(section.path),
                "roles": [], "chapter_line_start": section.start + 1,
                "chapter_line_end": end,
                "manual_line_start": manual_body_line + section.start,
                "manual_line_end": manual_body_line + end - 1,
                "markdown": "".join(lines[section.start:end]),
            }
        fragment = fragments[key]
        if role not in fragment["roles"]:
            fragment["roles"].append(role)
        return fragment["context_id"]

    for hit in hits:
        section = _locate_section(hit, sections)
        ancestors = _ancestors(section)
        scope = next(
            (node for node in [section, *ancestors] if node.is_task), section
        )
        context_ids = [add(scope, scope.end, "task" if scope.is_task else "matched_section")]
        for ancestor in _ancestors(scope):
            intro_end = ancestor.children[0].start if ancestor.children else ancestor.end
            if "".join(lines[ancestor.body_start:intro_end]).strip():
                context_ids.append(add(ancestor, intro_end, "ancestor_introduction"))
        scope_ancestors = _ancestors(scope)
        for caution in sections[1:]:
            if not caution.path or not CAUTION_TITLE.search(caution.path[-1]):
                continue
            if scope.start <= caution.start and caution.end <= scope.end:
                continue
            other_task = any(
                node.is_task and node is not scope
                and all(node is not ancestor for ancestor in scope_ancestors)
                for node in _ancestors(caution)
            )
            if not other_task:
                context_ids.append(add(caution, caution.end, "chapter_caution_candidate"))
        expanded_hits.append({
            "point_id": hit["point_id"], "rank": hit["rank"], "score": hit["score"],
            "section_path": hit["section_path"], "source_refs": hit["source_refs"],
            "context_ids": list(dict.fromkeys(context_ids)),
        })
    return expanded_hits, [fragments[key] for key in sorted(fragments)]


def _validate_hit(
    hit: dict[str, Any], candidate: dict[str, Any], document: ManualDocument,
    chapter: ManualChapter, generation: str,
) -> None:
    _validate_payload(hit, candidate)
    expected = {
        "document_id": document.document_id, "document_title": document.title,
        "product": document.product, "release": document.release,
        "source_pdf_sha256": document.source_pdf_sha256,
        "chapter_order": chapter.order, "chapter_title": chapter.title,
        "chapter_url": chapter.html_url, "html_content_sha256": chapter.html_content_sha256,
        "pdf_page_index": chapter.pdf_page_index, "pdf_mapping_status": chapter.pdf_mapping_status,
        "dataset_fingerprint": generation,
    }
    if any(hit.get(key) != value for key, value in expected.items()):
        raise ContextError("Search hit source metadata does not match the current OKF/PDF.")
    if hashlib.sha256(hit["text"].encode("utf-8")).hexdigest() != hit["text_sha256"]:
        raise ContextError("Search hit text SHA-256 does not match.")
    if not isinstance(hit.get("point_id"), str) or not hit["point_id"]:
        raise ContextError("Search hit point ID is invalid.")
    score = hit.get("score")
    if type(score) not in (int, float) or not math.isfinite(score):
        raise ContextError("Search hit score is invalid.")


def expand_context(
    results_path: Path,
    catalog_path: Path = Path("data/catalog/c9300-iosxe26.json"),
    okf_root: Path = Path("data/okf"),
    source_pdf_root: Path = Path("data/source_manuals"),
) -> dict[str, Any]:
    try:
        raw = results_path.read_bytes()
        search = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextError(f"Could not read search results: {results_path}") from error
    if (
        not isinstance(search, dict) or type(search.get("schema_version")) is not int
        or search["schema_version"] != 1 or search.get("evidence_status") != "candidates_only"
        or search.get("embedding_model") != EMBEDDING_MODEL
    ):
        raise ContextError("Input must be search_manuals schema_version 1 results.")
    catalog = load_catalog(catalog_path, "catalyst9300", "26.x")
    if (
        search.get("catalog_sha256") != catalog.sha256
        or search.get("product") != catalog.product or search.get("release") != catalog.release
    ):
        raise ContextError("Search catalog, product or release does not match.")
    if not isinstance(search.get("requirement"), str) or not search["requirement"].strip():
        raise ContextError("Search requirement is invalid.")
    items = search.get("items")
    if not isinstance(items, list) or not items:
        raise ContextError("Search results must contain requirement items.")
    selection_items = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("chapters"), list):
            raise ContextError("Search item must contain a chapters array.")
        selection_items.append({**item, "candidates": item["chapters"]})
    validated = validate_selection({"items": selection_items}, catalog)
    documents = load_validated_manuals(catalog_path, okf_root, source_pdf_root)
    generation = _dataset_fingerprint(documents, EMBEDDING_MODEL)
    has_candidates = any(item["chapters"] for item in items)
    if search.get("dataset_fingerprint") != (generation if has_candidates else None):
        raise ContextError("Search index generation differs from the current corpus; rerun indexing/search.")
    by_id = {document.document_id: document for document in documents}
    pdf_filenames = {
        entry["document_id"]: entry["filename"]
        for entry in json.loads(catalog_path.read_bytes())["documents"]
    }
    offsets: dict[str, dict[str, int]] = {}
    expanded_items = []
    for item, validated_item in zip(items, validated, strict=True):
        expanded_chapters = []
        for candidate, validated_candidate in zip(
            item["chapters"], validated_item["candidates"], strict=True
        ):
            if any(
                candidate.get(key) != validated_candidate[key]
                for key in ("chapter_title", "chapter_url")
            ):
                raise ContextError("Search chapter does not match the catalog.")
            document = by_id[candidate["document_id"]]
            chapter = next(
                (chapter for chapter in document.chapters if chapter.order == candidate["chapter_order"]),
                None,
            )
            if chapter is None or chapter.html_url != candidate["chapter_url"]:
                raise ContextError("Search chapter is missing from the current OKF.")
            hits = candidate.get("matches")
            if not isinstance(hits, list):
                raise ContextError("Search chapter must contain a matches array.")
            for rank, hit in enumerate(hits, 1):
                if not isinstance(hit, dict) or type(hit.get("rank")) is not int or hit["rank"] != rank:
                    raise ContextError("Search hit ranks must be consecutive positive integers.")
                _validate_hit(hit, candidate, document, chapter, generation)
            status = "retrieved" if hits else "no_matches"
            if candidate.get("status") != status:
                raise ContextError("Search chapter status does not match its hits.")
            document_id = document.document_id
            manual_path = okf_root / document_id / "manual.md"
            if document_id not in offsets:
                manual = manual_path.read_text(encoding="utf-8")
                offsets[document_id] = {}
                for marker in CHAPTER_MARKER.finditer(manual):
                    body_start = marker.end()
                    body_start += len(manual[body_start:]) - len(manual[body_start:].lstrip())
                    offsets[document_id][marker.group("url")] = manual.count("\n", 0, body_start) + 1
            matches, contexts = expand_chapter(hits, chapter, offsets[document_id][chapter.html_url])
            filename = pdf_filenames[document_id]
            expanded_chapters.append({
                **{key: value for key, value in candidate.items() if key != "matches"},
                "document_title": document.title,
                "source_pdf_sha256": document.source_pdf_sha256,
                "html_content_sha256": chapter.html_content_sha256,
                "pdf_page_index": chapter.pdf_page_index,
                "pdf_mapping_status": chapter.pdf_mapping_status,
                "pdf_page_scope": "unmapped" if chapter.pdf_page_index is None else "chapter_start",
                "manual_path": str(manual_path.resolve()),
                "manual_sha256": hashlib.sha256(manual_path.read_bytes()).hexdigest(),
                "source_pdf_path": str((source_pdf_root / document_id / filename).resolve()),
                "matches": matches, "contexts": contexts,
            })
        expected_status = (
            "no_candidates" if not expanded_chapters else
            "retrieved" if any(chapter["matches"] for chapter in expanded_chapters) else "no_matches"
        )
        if item.get("status") != expected_status:
            raise ContextError("Search item status does not match its chapters.")
        expanded_items.append({**item, "chapters": expanded_chapters})
    return {
        **search, "search_results_sha256": hashlib.sha256(raw).hexdigest(),
        "context_policy": "full_task_with_ancestor_introductions_and_chapter_caution_candidates",
        "pdf_verification_status": "not_verified",
        "limitations": [
            "Chapter caution headings identify review candidates, not proven applicability or completeness.",
            "Cross-references outside the retrieved sections are not followed automatically.",
            "PDF hashes are checked, but PDF content and exact procedure pages are not verified.",
        ],
        "items": expanded_items,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-results", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog/c9300-iosxe26.json"))
    parser.add_argument("--okf-root", type=Path, default=Path("data/okf"))
    parser.add_argument("--source-pdf-root", type=Path, default=Path("data/source_manuals"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output:
            output = args.output.resolve()
            if (
                output in {args.search_results.resolve(), args.catalog.resolve(), Path(".env").resolve()}
                or any(output.is_relative_to(root.resolve()) for root in (
                    args.okf_root, args.source_pdf_root, Path("data/qdrant"),
                ))
            ):
                raise ContextError("Output must not overwrite an input, source, configuration or database file.")
        result = expand_context(args.search_results, args.catalog, args.okf_root, args.source_pdf_root)
        content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            _write_output(args.output, content)
        else:
            sys.stdout.write(content)
    except (ContextError, SearchError, SelectionError, IndexingError, OSError, ValueError) as error:
        print(f"Context expansion failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
