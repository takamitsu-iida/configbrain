from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterator

import pymupdf


@dataclass(frozen=True)
class PdfPage:
    document_id: str
    page_number: int
    text: str
    section_title: str | None
    headings: tuple[str, ...]
    code_blocks: tuple[str, ...]
    tables: tuple[tuple[tuple[str, ...], ...], ...]


class PdfLoader:
    """Load a PDF into page-level records while preserving source locations."""

    def __init__(self, path: Path, document_id: str) -> None:
        self.path = path
        self.document_id = document_id

    def load(self) -> list[PdfPage]:
        return list(self.iter_pages())

    def iter_pages(self) -> Iterator[PdfPage]:
        with pymupdf.open(self.path) as document:
            previous_section: str | None = None
            for page_index, page in enumerate(document, start=1):
                text = page.get_text("text").strip()
                headings = tuple(_find_headings(text))
                section_title = headings[0] if headings else previous_section
                if section_title:
                    previous_section = section_title
                yield PdfPage(
                    document_id=self.document_id,
                    page_number=page_index,
                    text=text,
                    section_title=section_title,
                    headings=headings,
                    code_blocks=tuple(_find_code_blocks(text)),
                    tables=tuple(_find_tables(page)),
                )


def _find_headings(text: str) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        candidate = " ".join(line.split()).strip()
        if _looks_like_heading(candidate, is_indented=line[:1].isspace()):
            headings.append(candidate)
    return headings


def _looks_like_heading(line: str, *, is_indented: bool = False) -> bool:
    if not line or len(line) > 120 or line.endswith((".", ":", ";")):
        return False
    if is_indented:
        return False
    if re.match(
        r"^(?:conf(?:igure)?\b|description\b|interface\s+(?:GigabitEthernet|Vlan|Port-channel)|"
        r"name\s+\S+|no\s+\S+|router\s+\S+|show\s+\S+|switchport\s+\S+|vlan\s+\d+)",
        line,
        re.IGNORECASE,
    ):
        return False
    if re.match(r"^(chapter|section|appendix)\b", line, re.IGNORECASE):
        return True
    words = line.split()
    return 1 < len(words) <= 12 and not any(char in line for char in "{}<>[]=|")


def _find_code_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        is_command = bool(stripped) and (
            stripped.startswith(("switch#", "switch(", "Router#", "Router(", "interface ", "show ", "conf ", "configure ", "vlan "))
            or line.startswith((" ", "\t"))
        )
        if is_command:
            current.append(line.rstrip())
        elif current:
            blocks.append("\n".join(current).strip())
            current = []
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def _find_tables(page: pymupdf.Page) -> list[tuple[tuple[str, ...], ...]]:
    """Extract tables as immutable row and cell values when detectable."""
    find_tables = getattr(page, "find_tables", None)
    if find_tables is None:
        return []
    try:
        table_finder = find_tables()
    except (RuntimeError, ValueError):
        return []
    tables: list[tuple[tuple[str, ...], ...]] = []
    for table in table_finder.tables:
        rows = tuple(
            tuple((cell or "").strip() for cell in row)
            for row in table.extract()
        )
        if rows:
            tables.append(rows)
    return tables