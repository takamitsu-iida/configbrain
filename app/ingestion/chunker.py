from dataclasses import dataclass
import re
from typing import Iterable

from app.ingestion.html_loader import HtmlSection
from app.ingestion.pdf_loader import PdfPage


@dataclass(frozen=True)
class Chunk:
    document_id: str
    page_number: int | None
    section_title: str | None
    text: str
    content_type: str
    section_url: str | None = None


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
                chunks.append(_make_chunk(page, pending, pending_type))
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


class HtmlChunker:
    """Split HTML sections into searchable text, command, and table chunks."""

    def __init__(self, max_chars: int = 1800) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        self.max_chars = max_chars

    def chunk_sections(self, sections: Iterable[HtmlSection]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for section in sections:
            chunks.extend(self.chunk_section(section))
        return chunks

    def chunk_section(self, section: HtmlSection) -> list[Chunk]:
        section_title = _section_title(section)
        if section.code_blocks:
            return _make_html_chunks(
                section,
                section_title,
                _configuration_text(section),
                "configuration_example",
                self.max_chars,
            )
        chunks: list[Chunk] = []
        for block in _split_html_text(section.text):
            chunks.extend(
                _make_html_chunks(section, section_title, block, "concept", self.max_chars)
            )
        for code in section.code_blocks:
            chunks.append(_make_html_chunk(section, section_title, code, _code_content_type(code)))
        for table in section.tables:
            table_text = "\n".join(" | ".join(row) for row in table)
            if table_text.strip():
                chunks.extend(
                    _make_html_chunks(
                        section,
                        section_title,
                        table_text,
                        "parameter_reference",
                        self.max_chars,
                    )
                )
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
        if table_text and not _is_noise_table(table):
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


def _section_title(section: HtmlSection) -> str:
    title = section.section_title.strip()
    if not title:
        raise ValueError(f"HTML section has no section title: {section.section_url}")
    return title


def _configuration_text(section: HtmlSection) -> str:
    parts = [section.text.strip()]
    parts.extend(code.strip() for code in section.code_blocks if code.strip())
    parts.extend(
        "\n".join(" | ".join(row) for row in table).strip()
        for table in section.tables
        if table
    )
    return "\n\n".join(part for part in parts if part)


def _make_html_chunk(
    section: HtmlSection,
    section_title: str,
    text: str,
    content_type: str,
) -> Chunk:
    return Chunk(
        document_id=section.document_id,
        page_number=None,
        section_title=section_title,
        text=text.strip(),
        content_type=content_type,
        section_url=section.section_url,
    )


def _make_html_chunks(
    section: HtmlSection,
    section_title: str,
    text: str,
    content_type: str,
    max_chars: int,
) -> list[Chunk]:
    if len(text) <= max_chars:
        return [_make_html_chunk(section, section_title, text, content_type)]
    return [
        _make_html_chunk(section, section_title, part, content_type)
        for part in _split_text_at_lines(text, max_chars)
    ]


def _split_html_text(text: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]


def _split_text_at_lines(text: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in text.splitlines():
        if current and current_length + len(line) + 1 > max_chars:
            parts.append("\n".join(current).strip())
            current = []
            current_length = 0
        current.append(line)
        current_length += len(line) + 1
    if current:
        parts.append("\n".join(current).strip())
    return parts


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


def _is_noise_table(table: tuple[tuple[str, ...], ...]) -> bool:
    cells = [cell.strip() for row in table for cell in row if cell.strip()]
    return (
        any("Configuration Guide, Cisco IOS XE" in cell for cell in cells)
        and bool(cells)
        and all(cell.isdigit() or "Configuration Guide, Cisco IOS XE" in cell for cell in cells)
    )
