from dataclasses import dataclass
import re
from typing import Iterable

from app.ingestion.pdf_loader import PdfPage


@dataclass(frozen=True)
class Chunk:
    document_id: str
    page_number: int
    section_title: str | None
    text: str
    content_type: str


class SemanticChunker:
    """Split extracted pages at document structure boundaries."""

    def __init__(self, max_chars: int = 1800) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        self.max_chars = max_chars

    def chunk_pages(self, pages: Iterable[PdfPage]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for page in pages:
            chunks.extend(self.chunk_page(page))
        return chunks

    def chunk_page(self, page: PdfPage) -> list[Chunk]:
        units = _extract_units(page)
        chunks: list[Chunk] = []
        pending: list[str] = []
        pending_type = "concept"
        pending_length = 0

        def flush() -> None:
            nonlocal pending, pending_type, pending_length
            if pending:
                chunks.append(
                    _make_chunk(page, pending, pending_type)
                )
                pending = []
                pending_type = "concept"
                pending_length = 0

        for unit, content_type in units:
            if content_type in {"configuration_example", "command_reference"}:
                flush()
                chunks.append(_make_chunk(page, [unit], content_type))
                continue
            if content_type == "parameter_reference":
                flush()
                chunks.extend(_split_oversized_unit(page, unit, content_type, self.max_chars))
                continue
            unit_length = len(unit)
            if pending and pending_length + unit_length + 2 > self.max_chars:
                flush()
            pending.append(unit)
            pending_length += unit_length + (2 if len(pending) > 1 else 0)
        flush()
        return chunks


def _extract_units(page: PdfPage) -> list[tuple[str, str]]:
    text = page.text
    for code_block in page.code_blocks:
        text = text.replace(code_block, f"\n\n__CONFIGBRAIN_CODE_{len(code_block)}__\n\n", 1)

    units: list[tuple[str, str]] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        code_match = re.fullmatch(r"__CONFIGBRAIN_CODE_(\d+)__", block)
        if code_match:
            original = next(
                code for code in page.code_blocks if len(code) == int(code_match.group(1))
            )
            units.append((original, _code_content_type(original)))
            continue
        content_type = "parameter_reference" if _looks_like_table_text(block) else "concept"
        units.append((block, content_type))

    for table in page.tables:
        table_text = "\n".join(" | ".join(row) for row in table)
        if table_text:
            units.append((table_text, "parameter_reference"))
    return units


def _make_chunk(page: PdfPage, units: list[str], content_type: str) -> Chunk:
    text = "\n\n".join(units).strip()
    return Chunk(
        document_id=page.document_id,
        page_number=page.page_number,
        section_title=page.section_title,
        text=text,
        content_type=content_type,
    )


def _split_oversized_unit(
    page: PdfPage, text: str, content_type: str, max_chars: int
) -> list[Chunk]:
    if len(text) <= max_chars:
        return [_make_chunk(page, [text], content_type)]
    lines = text.splitlines()
    chunks: list[Chunk] = []
    current: list[str] = []
    current_length = 0
    for line in lines:
        if current and current_length + len(line) + 1 > max_chars:
            chunks.append(_make_chunk(page, ["\n".join(current)], content_type))
            current = []
            current_length = 0
        current.append(line)
        current_length += len(line) + 1
    if current:
        chunks.append(_make_chunk(page, ["\n".join(current)], content_type))
    return chunks


def _code_content_type(text: str) -> str:
    return "command_reference" if text.lstrip().startswith(("show ", "switch#", "Router#")) else "configuration_example"


def _looks_like_table_text(text: str) -> bool:
    return " | " in text or text.count("\t") >= 2
