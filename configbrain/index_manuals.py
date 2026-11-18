"""Index validated OKF manual packages in a persistent local Qdrant database."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import uuid
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol, Sequence

from bs4 import BeautifulSoup
from frictionless import Package
from markdown_it import MarkdownIt
from markdown_it.rules_block.state_block import StateBlock
from qdrant_client import QdrantClient, models

EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
COLLECTION_ALIAS = "c9300_iosxe26_manuals"
COLLECTION_PREFIX = "cb_c9300_iosxe26_"
INDEX_FORMAT_VERSION = 2
EMBEDDING_BATCH_SIZE = 32
UPSERT_BATCH_SIZE = 128
MAX_SEQUENCE_MARGIN = 4
CHAPTER_MARKER = re.compile(
    r"^## (?P<title>[^\n]+)\n\n"
    r"> HTML source: \[[^\]]+\]\((?P<url>https?://[^)\n]+)\)\n"
    r"> PDF page index: (?P<page>[^\n]+)\n\n",
    re.MULTILINE,
)


class IndexingError(RuntimeError):
    """Raised when a validated manual corpus cannot be indexed safely."""


class Tokenizer(Protocol):
    def tokenize(self, text: str) -> list[str]: ...

    def num_special_tokens_to_add(self, pair: bool = False) -> int: ...

    def encode(
        self, text: str, *, add_special_tokens: bool = True
    ) -> list[int]: ...


class EmbeddingModel(Protocol):
    tokenizer: Tokenizer
    max_seq_length: int

    def get_embedding_dimension(self) -> int: ...

    def encode(
        self,
        sentences: Sequence[str],
        *,
        batch_size: int,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
    ) -> Any: ...


@dataclass(frozen=True)
class ManualChapter:
    order: int
    title: str
    html_url: str
    html_content_sha256: str
    pdf_page_index: int | None
    pdf_mapping_status: str
    markdown: str


@dataclass(frozen=True)
class ManualDocument:
    document_id: str
    title: str
    product: str
    release: str
    source_pdf_sha256: str
    chapters: tuple[ManualChapter, ...]


@dataclass(frozen=True)
class TextBlock:
    text: str
    kind: str
    section_path: tuple[str, ...]
    source_refs: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class IndexedChunk:
    point_id: str
    text: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class IndexResult:
    status: str
    collection_name: str
    point_count: int
    document_count: int
    chapter_count: int
    database_path: Path
    warnings: tuple[str, ...] = ()


def _read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IndexingError(f"Could not read {description}: {path}") from error
    if not isinstance(value, dict):
        raise IndexingError(f"{description} must contain a JSON object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise IndexingError(f"Could not read file for hashing: {path}") from error
    return digest.hexdigest()


def _validate_package(package_dir: Path) -> dict[str, Any]:
    report = Package(str(package_dir / "datapackage.json")).validate()
    if not report.valid:
        details = "; ".join(
            f"{code}: {message}"
            for code, message in report.flatten(["code", "message"])
        )
        raise IndexingError(f"Invalid OKF package {package_dir}: {details}")
    return _read_json(package_dir / "datapackage.json", "Data Package descriptor")


def _chapter_bodies(
    manual: str, provenance_chapters: list[dict[str, Any]]
) -> tuple[ManualChapter, ...]:
    markers = list(CHAPTER_MARKER.finditer(manual))
    if len(markers) != len(provenance_chapters):
        raise IndexingError(
            "The number of HTML chapter markers in manual.md does not match provenance.json"
        )
    by_url = {chapter["html_url"]: chapter for chapter in provenance_chapters}
    if len(by_url) != len(provenance_chapters):
        raise IndexingError("Duplicate chapter URLs in provenance.json")

    chapters: list[ManualChapter] = []
    seen_urls: set[str] = set()
    for index, marker in enumerate(markers):
        url = marker.group("url")
        chapter = by_url.get(url)
        if chapter is None or url in seen_urls:
            raise IndexingError(f"Chapter marker has no unique provenance entry: {url}")
        seen_urls.add(url)
        title = marker.group("title")
        if title != chapter["title"]:
            raise IndexingError(f"Chapter title mismatch in manual.md: {url}")
        mapping = chapter["pdf_page_mapping"]
        page_index = mapping["page_index"]
        expected_page = str(page_index) if page_index is not None else "not mapped"
        if marker.group("page") != expected_page:
            raise IndexingError(f"PDF page mapping mismatch in manual.md: {url}")
        end = markers[index + 1].start() if index + 1 < len(markers) else len(manual)
        markdown = manual[marker.end() : end].strip()
        if not markdown:
            raise IndexingError(f"Chapter has no Markdown content: {url}")
        chapters.append(
            ManualChapter(
                order=chapter["order"],
                title=title,
                html_url=url,
                html_content_sha256=chapter["html_content_sha256"],
                pdf_page_index=page_index,
                pdf_mapping_status=mapping["status"],
                markdown=markdown,
            )
        )
    return tuple(sorted(chapters, key=lambda chapter: chapter.order))


def load_validated_manuals(
    catalog_path: Path,
    okf_root: Path = Path("data/okf"),
    source_pdf_root: Path = Path("data/source_manuals"),
) -> tuple[ManualDocument, ...]:
    catalog = _read_json(catalog_path, "manual catalog")
    if catalog.get("schema_version") != 1:
        raise IndexingError("Manual catalog schema_version must be 1")
    documents = catalog.get("documents")
    if not isinstance(documents, list) or not documents:
        raise IndexingError("Manual catalog contains no documents")

    loaded: list[ManualDocument] = []
    seen_ids: set[str] = set()
    for entry in documents:
        document_id = entry.get("document_id")
        if not isinstance(document_id, str) or document_id in seen_ids:
            raise IndexingError(f"Invalid or duplicate catalog document ID: {document_id}")
        seen_ids.add(document_id)
        package_dir = okf_root / document_id
        if not package_dir.is_dir() or package_dir.is_symlink():
            raise IndexingError(f"OKF package is missing or unsafe: {package_dir}")

        descriptor = _validate_package(package_dir)
        descriptor_config = descriptor.get("configbrain", {})
        provenance = _read_json(package_dir / "provenance.json", "provenance")
        document_provenance = provenance.get("document", {})
        source_pdf = provenance.get("source_pdf", {})
        if descriptor_config.get("document_id") != document_id:
            raise IndexingError(f"Data Package document ID mismatch: {document_id}")
        if document_provenance.get("document_id") != document_id:
            raise IndexingError(f"Provenance document ID mismatch: {document_id}")
        if document_provenance.get("product") != catalog.get("product"):
            raise IndexingError(f"Product mismatch in provenance: {document_id}")
        if document_provenance.get("release") != catalog.get("release"):
            raise IndexingError(f"Release mismatch in provenance: {document_id}")
        pdf_sha256 = source_pdf.get("sha256")
        if not isinstance(pdf_sha256, str) or descriptor_config.get(
            "source_pdf_sha256"
        ) != pdf_sha256:
            raise IndexingError(f"Source PDF hash mismatch in package metadata: {document_id}")
        pdf_path = source_pdf_root / document_id / entry["filename"]
        if _sha256_file(pdf_path) != pdf_sha256:
            raise IndexingError(f"Source PDF has changed since conversion: {document_id}")

        manual_path = package_dir / "manual.md"
        try:
            manual = manual_path.read_text(encoding="utf-8")
        except OSError as error:
            raise IndexingError(f"Could not read converted manual: {manual_path}") from error
        provenance_chapters = provenance.get("chapters")
        if not isinstance(provenance_chapters, list) or not provenance_chapters:
            raise IndexingError(f"Provenance has no chapter records: {document_id}")
        chapters = _chapter_bodies(manual, provenance_chapters)
        conversion_totals = provenance.get("conversion", {}).get("totals", {})
        if conversion_totals.get("chapter_count") != len(chapters):
            raise IndexingError(f"Chapter count mismatch in provenance: {document_id}")

        for image in provenance.get("images", []):
            if image.get("status") != "available":
                continue
            relative_path = image.get("path")
            if not isinstance(relative_path, str):
                raise IndexingError(f"Invalid image path in provenance: {document_id}")
            image_path = (package_dir / relative_path).resolve()
            if not image_path.is_relative_to(package_dir.resolve()):
                raise IndexingError(f"Image path escapes its package: {document_id}")
            if not image_path.is_file():
                raise IndexingError(f"Package image is missing: {image_path}")
            if image_path.stat().st_size != image.get("size_bytes"):
                raise IndexingError(f"Package image size mismatch: {image_path}")
            if _sha256_file(image_path) != image.get("sha256"):
                raise IndexingError(f"Package image hash mismatch: {image_path}")

        loaded.append(
            ManualDocument(
                document_id=document_id,
                title=entry["title"],
                product=catalog["product"],
                release=catalog["release"],
                source_pdf_sha256=pdf_sha256,
                chapters=chapters,
            )
        )
    return tuple(sorted(loaded, key=lambda document: document.document_id))


def _visible_html_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for image in soup.find_all("img"):
        alt = image.get("alt")
        image.replace_with(f" {alt} " if isinstance(alt, str) and alt else " ")
    return " ".join(soup.get_text(" ", strip=True).split())


class _TableBoundaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.complete = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.depth:
            self.depth -= 1
            if self.depth == 0:
                self.complete = True


def _html_table_block(
    state: StateBlock, start_line: int, end_line: int, silent: bool
) -> bool:
    if state.is_code_block(start_line) or not state.md.options.get("html"):
        return False
    position = state.bMarks[start_line] + state.tShift[start_line]
    line = state.src[position : state.eMarks[start_line]]
    if not re.match(r"<table(?=\s|/?>|$)", line, re.IGNORECASE):
        return False
    if silent:
        return True

    boundary = _TableBoundaryParser()
    next_line = start_line
    while next_line < end_line:
        boundary.feed(
            state.src[state.bMarks[next_line] : state.eMarks[next_line]] + "\n"
        )
        next_line += 1
        if boundary.complete and boundary.depth == 0:
            break
    if not boundary.complete or boundary.depth:
        raise IndexingError(f"Unclosed HTML table at chapter line {start_line + 1}")
    state.line = next_line
    token = state.push("html_block", "", 0)
    token.map = [start_line, next_line]
    token.content = state.getLines(start_line, next_line, state.blkIndent, True)
    return True


def _table_blocks(
    table: Any,
    table_index: int,
    section_path: tuple[str, ...],
    source_ref: dict[str, Any],
) -> list[TextBlock]:
    rows = [
        row
        for row in table.find_all("tr")
        if row.find_parent("table") is table
    ]
    if not rows:
        return []

    header_rows = [
        row
        for row in rows
        if row.find_parent("thead") is not None
        and row.find_parent("thead").find_parent("table") is table
    ]
    if not header_rows:
        first_cells = rows[0].find_all(["th", "td"], recursive=False)
        if first_cells and all(cell.name == "th" for cell in first_cells):
            header_rows = [rows[0]]

    header_matrix = [
        [
            _visible_html_text(str(cell))
            for cell in row.find_all(["th", "td"], recursive=False)
        ]
        for row in header_rows
    ]
    header_width = max((len(row) for row in header_matrix), default=0)
    headers = [
        " / ".join(
            row[column] for row in header_matrix if column < len(row) and row[column]
        )
        for column in range(header_width)
    ]
    header_row_ids = {id(row) for row in header_rows}
    caption = table.find("caption")
    caption_text = _visible_html_text(str(caption)) if caption else ""
    table_label = f"Table {table_index}"
    if caption_text:
        table_label += f": {caption_text}"
    header_line = f"Headers: {' | '.join(headers)}" if headers else ""

    blocks: list[TextBlock] = []
    for row_index, row in enumerate(rows, start=1):
        if id(row) in header_row_ids:
            continue
        cells = row.find_all(["th", "td"], recursive=False)
        values = []
        for column, cell in enumerate(cells):
            cell_text = _visible_html_text(str(cell))
            if cell_text:
                heading = (
                    headers[column]
                    if column < len(headers) and headers[column]
                    else f"Column {column + 1}"
                )
                values.append(f"{heading}: {cell_text}")
        if not values:
            continue
        text = "\n".join(
            part
            for part in (
                table_label,
                header_line,
                f"Row {row_index}: {' | '.join(values)}",
            )
            if part
        )
        blocks.append(
            TextBlock(
                text=text,
                kind="table",
                section_path=section_path,
                source_refs=(
                    {
                        **source_ref,
                        "kind": "table",
                        "table_index": table_index,
                        "row_index": row_index,
                    },
                ),
            )
        )

    if not blocks:
        text = "\n".join(part for part in (table_label, header_line) if part)
        if text:
            blocks.append(
                TextBlock(
                    text=text,
                    kind="table",
                    section_path=section_path,
                    source_refs=(
                        {
                            **source_ref,
                            "kind": "table",
                            "table_index": table_index,
                            "row_index": None,
                        },
                    ),
                )
            )
    return blocks


def manual_markdown_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark")
    parser.block.ruler.before("html_block", "complete_html_table", _html_table_block)
    return parser


def extract_text_blocks(markdown: str) -> tuple[TextBlock, ...]:
    parser = manual_markdown_parser()
    blocks: list[TextBlock] = []
    section_path: list[str] = []
    pending_heading_level: int | None = None
    table_index = 0
    code_block_index = 0

    for token in parser.parse(markdown):
        if token.type == "heading_open":
            pending_heading_level = int(token.tag.removeprefix("h"))
            continue
        if token.type == "inline":
            text = _visible_html_text(parser.renderInline(token.content))
            if pending_heading_level is not None:
                level = pending_heading_level
                section_path = section_path[: level - 1]
                if text:
                    section_path.append(text)
                pending_heading_level = None
                continue
            if text:
                blocks.append(
                    TextBlock(
                        text=text,
                        kind="text",
                        section_path=tuple(section_path),
                        source_refs=({"kind": "text"},),
                    )
                )
            continue
        if token.type in ("fence", "code_block"):
            text = token.content.rstrip("\n")
            if text.strip():
                code_block_index += 1
                blocks.append(
                    TextBlock(
                        text=text,
                        kind="code",
                        section_path=tuple(section_path),
                        source_refs=(
                            {
                                "kind": "code",
                                "code_block_index": code_block_index,
                            },
                        ),
                    )
                )
            continue
        if token.type == "html_block":
            soup = BeautifulSoup(token.content, "html.parser")
            source_ref: dict[str, Any] = {}
            if token.map:
                source_ref = {
                    "okf_line_start": token.map[0] + 1,
                    "okf_line_end": token.map[1],
                }
            tables = list(soup.find_all("table"))
            top_level_tables = [
                table for table in tables if table.find_parent("table") is None
            ]
            for table in tables:
                table_index += 1
                blocks.extend(
                    _table_blocks(table, table_index, tuple(section_path), source_ref)
                )
            for table in top_level_tables:
                table.decompose()
            text = _visible_html_text(str(soup))
            if text:
                blocks.append(
                    TextBlock(
                        text=text,
                        kind="text",
                        section_path=tuple(section_path),
                        source_refs=(
                            {
                                "kind": "text",
                                **source_ref,
                            },
                        ),
                    )
                )
    return tuple(blocks)


def _context(document: ManualDocument, chapter: ManualChapter, section: tuple[str, ...]) -> str:
    parts = [
        f"Document: {document.title}",
        f"Product: {document.product}",
        f"Release: {document.release}",
        f"Chapter {chapter.order}: {chapter.title}",
    ]
    if chapter.pdf_page_index is not None:
        parts.append(f"PDF chapter start page: {chapter.pdf_page_index}")
    if section:
        parts.append(f"Section: {' > '.join(section)}")
    return "\n".join(parts)


def _token_count(tokenizer: Tokenizer, text: str) -> int:
    return len(tokenizer.tokenize(text)) + tokenizer.num_special_tokens_to_add(
        pair=False
    )


def _split_long_text(
    text: str,
    context: str,
    tokenizer: Tokenizer,
    max_tokens: int,
    preserve_whitespace: bool,
) -> list[str]:
    if not preserve_whitespace:
        text = text.strip()
    pieces: list[str] = []
    start = 0
    while start < len(text):
        low = start + 1
        high = len(text)
        best = start
        while low <= high:
            middle = (low + high) // 2
            candidate = text[start:middle]
            if not preserve_whitespace:
                candidate = candidate.strip()
            search_text = f"passage: {context}\n\n{candidate}"
            if _token_count(tokenizer, search_text) <= max_tokens:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best == start:
            raise IndexingError("A text block cannot fit in the embedding model context")
        while best > start:
            candidate = text[start:best]
            if not preserve_whitespace:
                candidate = candidate.strip()
            search_text = f"passage: {context}\n\n{candidate}"
            if _token_count(tokenizer, search_text) <= max_tokens:
                break
            boundary = max(
                text.rfind("\n", start, best - 1),
                text.rfind(" ", start, best - 1),
                text.rfind("\t", start, best - 1),
            )
            best = boundary if boundary > start else best - 1
        piece = text[start:best]
        if not preserve_whitespace:
            piece = piece.strip()
        if not piece:
            raise IndexingError("Text chunking produced an empty fragment")
        pieces.append(piece)
        start = best
        if not preserve_whitespace:
            while start < len(text) and text[start].isspace():
                start += 1
    return pieces


def _dataset_fingerprint(
    documents: Sequence[ManualDocument], model_name: str
) -> str:
    value = {
        "index_format_version": INDEX_FORMAT_VERSION,
        "embedding_model": model_name,
        "documents": [
            {
                "document_id": document.document_id,
                "source_pdf_sha256": document.source_pdf_sha256,
                "chapters": [
                    {
                        "order": chapter.order,
                        "html_url": chapter.html_url,
                        "html_content_sha256": chapter.html_content_sha256,
                    }
                    for chapter in document.chapters
                ],
            }
            for document in documents
        ],
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_chunks(
    documents: Sequence[ManualDocument],
    tokenizer: Tokenizer,
    max_tokens: int,
    generation: str,
) -> tuple[IndexedChunk, ...]:
    chunks: list[IndexedChunk] = []
    for document in documents:
        document_chunk_index = 0
        for chapter in document.chapters:
            current_context: str | None = None
            current_kind: list[str] = []
            current_texts: list[str] = []
            current_refs: list[dict[str, Any]] = []

            def flush() -> None:
                nonlocal document_chunk_index, current_context
                if not current_texts or current_context is None:
                    return
                body = "\n\n".join(current_texts)
                text = f"passage: {current_context}\n\n{body}"
                token_count = _token_count(tokenizer, text)
                if token_count > max_tokens:
                    raise IndexingError(
                        f"Generated chunk exceeds the embedding model context "
                        f"for {document.document_id}/{chapter.order}: "
                        f"{token_count} > {max_tokens}; "
                        f"source refs: {current_refs[:2]}"
                    )
                document_chunk_index += 1
                text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
                point_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{generation}:{document.document_id}:{document_chunk_index}:{text_sha256}",
                    )
                )
                chunks.append(
                    IndexedChunk(
                        point_id=point_id,
                        text=text,
                        payload={
                            "dataset_fingerprint": generation,
                            "document_id": document.document_id,
                            "document_title": document.title,
                            "product": document.product,
                            "release": document.release,
                            "source_pdf_sha256": document.source_pdf_sha256,
                            "chapter_order": chapter.order,
                            "chapter_title": chapter.title,
                            "chapter_url": chapter.html_url,
                            "html_content_sha256": chapter.html_content_sha256,
                            "pdf_page_index": chapter.pdf_page_index,
                            "pdf_mapping_status": chapter.pdf_mapping_status,
                            "pdf_page_scope": (
                                "chapter_start"
                                if chapter.pdf_page_index is not None
                                else "unmapped"
                            ),
                            "section_path": list(current_section),
                            "source_kind": (
                                current_kind[0]
                                if len(set(current_kind)) == 1
                                else "mixed"
                            ),
                            "source_refs": list(current_refs),
                            "chunk_index": document_chunk_index,
                            "text_sha256": text_sha256,
                            "embedding_model": EMBEDDING_MODEL,
                            "text": text,
                        },
                    )
                )
                current_kind.clear()
                current_texts.clear()
                current_refs.clear()
                current_context = None

            current_section: tuple[str, ...] = ()
            for block in extract_text_blocks(chapter.markdown):
                context = _context(document, chapter, block.section_path)
                if current_context is not None and context != current_context:
                    flush()
                if current_context is None:
                    current_context = context
                    current_section = block.section_path

                candidate_body = "\n\n".join((*current_texts, block.text))
                candidate_text = f"passage: {context}\n\n{candidate_body}"
                if _token_count(tokenizer, candidate_text) <= max_tokens:
                    current_texts.append(block.text)
                    current_kind.append(block.kind)
                    current_refs.extend(block.source_refs)
                    continue

                flush()
                current_context = context
                current_section = block.section_path
                single_text = f"passage: {context}\n\n{block.text}"
                if _token_count(tokenizer, single_text) <= max_tokens:
                    current_texts.append(block.text)
                    current_kind.append(block.kind)
                    current_refs.extend(block.source_refs)
                    continue

                parts = _split_long_text(
                    block.text,
                    context,
                    tokenizer,
                    max_tokens,
                    preserve_whitespace=block.kind == "code",
                )
                for part_index, part in enumerate(parts, start=1):
                    current_context = context
                    current_section = block.section_path
                    current_texts = [part]
                    current_kind = [block.kind]
                    current_refs = [
                        {**source_ref, "part_index": part_index, "part_count": len(parts)}
                        for source_ref in block.source_refs
                    ]
                    flush()
            flush()
    if not chunks:
        raise IndexingError("No searchable text chunks were produced from the OKF packages")
    return tuple(chunks)


def _load_embedding_model(model_name: str) -> EmbeddingModel:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, device="cpu", local_files_only=True)


def _active_collection(client: QdrantClient) -> str | None:
    matches = [
        alias.collection_name
        for alias in client.get_aliases().aliases
        if alias.alias_name == COLLECTION_ALIAS
    ]
    if len(matches) > 1:
        raise IndexingError(f"Qdrant alias is ambiguous: {COLLECTION_ALIAS}")
    return matches[0] if matches else None


def _collection_point_ids(client: QdrantClient, collection_name: str) -> set[str]:
    point_ids: set[str] = set()
    offset: int | str | uuid.UUID | None = None
    while True:
        records, offset = client.scroll(
            collection_name,
            limit=512,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        point_ids.update(str(record.id) for record in records)
        if offset is None:
            return point_ids


def _existing_index_matches(
    client: QdrantClient,
    collection_name: str | None,
    chunks: Sequence[IndexedChunk],
    generation: str,
) -> bool:
    if collection_name is None:
        return False
    if not collection_name.startswith(COLLECTION_PREFIX):
        raise IndexingError(
            f"Refusing to replace unmanaged collection behind alias {COLLECTION_ALIAS}: "
            f"{collection_name}"
        )
    if not client.collection_exists(collection_name):
        return False
    expected_name = f"{COLLECTION_PREFIX}{generation[:24]}"
    expected_ids = {chunk.point_id for chunk in chunks}
    return (
        client.count(collection_name, exact=True).count == len(expected_ids)
        and _collection_point_ids(client, collection_name) == expected_ids
        and (
            collection_name == expected_name
            or collection_name.startswith(f"{expected_name}_r")
        )
    )


def _index_documents(
    documents: Sequence[ManualDocument],
    database_path: Path,
    model: EmbeddingModel | None = None,
) -> IndexResult:
    if not documents:
        raise IndexingError("No validated OKF documents were supplied")
    embedding_model = model or _load_embedding_model(EMBEDDING_MODEL)
    dimension = embedding_model.get_embedding_dimension()
    max_tokens = embedding_model.max_seq_length - MAX_SEQUENCE_MARGIN
    if dimension < 1 or max_tokens < 16:
        raise IndexingError("The embedding model reports invalid dimensions or context size")

    generation = _dataset_fingerprint(documents, EMBEDDING_MODEL)
    chunks = build_chunks(
        documents,
        embedding_model.tokenizer,
        max_tokens,
        generation,
    )
    database_path.mkdir(parents=True, exist_ok=True)
    client = QdrantClient(path=str(database_path))
    try:
        old_collection = _active_collection(client)
        if _existing_index_matches(client, old_collection, chunks, generation):
            return IndexResult(
                status="unchanged",
                collection_name=old_collection or "",
                point_count=len(chunks),
                document_count=len(documents),
                chapter_count=sum(len(document.chapters) for document in documents),
                database_path=database_path,
            )

        collection_name = f"{COLLECTION_PREFIX}{generation[:24]}"
        if collection_name == old_collection:
            collection_name = f"{collection_name}_r{uuid.uuid4().hex[:8]}"
        elif client.collection_exists(collection_name):
            client.delete_collection(collection_name)

        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=dimension,
                distance=models.Distance.COSINE,
            ),
        )

        texts = [chunk.text for chunk in chunks]
        vectors = embedding_model.encode(
            texts,
            batch_size=EMBEDDING_BATCH_SIZE,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=True,
        )
        if len(vectors) != len(chunks):
            raise IndexingError(
                f"Embedding count mismatch: expected {len(chunks)}, got {len(vectors)}"
            )

        for start in range(0, len(chunks), UPSERT_BATCH_SIZE):
            batch_chunks = chunks[start : start + UPSERT_BATCH_SIZE]
            batch_vectors = vectors[start : start + UPSERT_BATCH_SIZE]
            points: list[models.PointStruct] = []
            for chunk, vector in zip(batch_chunks, batch_vectors, strict=True):
                values = [float(value) for value in vector]
                if len(values) != dimension or not all(
                    math.isfinite(value) for value in values
                ):
                    raise IndexingError(
                        f"Invalid embedding vector for point {chunk.point_id}"
                    )
                points.append(
                    models.PointStruct(
                        id=chunk.point_id,
                        vector=values,
                        payload=chunk.payload,
                    )
                )
            client.upsert(collection_name, points=points, wait=True)
            print(
                f"Staged {min(start + len(points), len(chunks))}/{len(chunks)} points",
                flush=True,
            )

        actual_count = client.count(collection_name, exact=True).count
        if actual_count != len(chunks):
            raise IndexingError(
                f"Qdrant point count mismatch: expected {len(chunks)}, got {actual_count}"
            )
        actual_ids = _collection_point_ids(client, collection_name)
        expected_ids = {chunk.point_id for chunk in chunks}
        if actual_ids != expected_ids:
            raise IndexingError("Qdrant point IDs do not match the staged corpus")

        operations: list[Any] = []
        if old_collection is not None:
            operations.append(
                models.DeleteAliasOperation(
                    delete_alias=models.DeleteAlias(alias_name=COLLECTION_ALIAS)
                )
            )
        operations.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(
                    collection_name=collection_name,
                    alias_name=COLLECTION_ALIAS,
                )
            )
        )
        if not client.update_collection_aliases(operations):
            raise IndexingError("Qdrant did not activate the new collection alias")
        if _active_collection(client) != collection_name:
            raise IndexingError("Qdrant alias does not point to the verified collection")

        warnings: list[str] = []
        if (
            old_collection is not None
            and old_collection != collection_name
            and old_collection.startswith(COLLECTION_PREFIX)
            and not client.delete_collection(old_collection)
        ):
            warnings.append(
                f"Old collection remains on disk but is no longer active: {old_collection}"
            )
        return IndexResult(
            status="created" if old_collection is None else "updated",
            collection_name=collection_name,
            point_count=actual_count,
            document_count=len(documents),
            chapter_count=sum(len(document.chapters) for document in documents),
            database_path=database_path,
            warnings=tuple(warnings),
        )
    finally:
        client.close()


def index_manuals(
    catalog_path: Path,
    okf_root: Path = Path("data/okf"),
    source_pdf_root: Path = Path("data/source_manuals"),
    database_path: Path = Path("data/qdrant/db"),
) -> IndexResult:
    documents = load_validated_manuals(catalog_path, okf_root, source_pdf_root)
    model = _load_embedding_model(EMBEDDING_MODEL)
    return _index_documents(documents, database_path, model)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Index validated Cisco OKF packages in local persistent Qdrant."
    )
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog/c9300-iosxe26.json"))
    parser.add_argument("--okf-root", type=Path, default=Path("data/okf"))
    parser.add_argument("--source-pdf-root", type=Path, default=Path("data/source_manuals"))
    parser.add_argument("--database", type=Path, default=Path("data/qdrant/db"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = index_manuals(
            args.catalog,
            args.okf_root,
            args.source_pdf_root,
            args.database,
        )
    except (IndexingError, OSError) as error:
        print(f"Qdrant indexing failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Qdrant {result.status}: alias={COLLECTION_ALIAS}, "
        f"collection={result.collection_name}, {result.point_count} points, "
        f"{result.document_count} documents, {result.chapter_count} chapters, "
        f"database={result.database_path}"
    )
    for warning in result.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
