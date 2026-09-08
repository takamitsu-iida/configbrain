from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Callable, Iterator
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HtmlSection:
    document_id: str
    section_url: str
    section_title: str
    text: str
    headings: tuple[str, ...]
    code_blocks: tuple[str, ...]
    tables: tuple[tuple[tuple[str, ...], ...], ...]


FetchHtml = Callable[[str], str]


class HtmlLoader:
    """Crawl one Cisco guide entry page and its same-guide HTML sections."""

    def __init__(
        self,
        entry_url: str,
        document_id: str,
        *,
        fetch_html: FetchHtml | None = None,
    ) -> None:
        self.entry_url = _normalize_url(entry_url)
        self.document_id = document_id
        self._fetch_html = fetch_html or _fetch_html
        self._allowed_prefix = _guide_prefix(self.entry_url)

    def load(self) -> list[HtmlSection]:
        urls = self._section_urls(self._fetch_html(self.entry_url))
        sections: list[HtmlSection] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            sections.extend(
                _parse_section(self._fetch_html(url), self.document_id, url)
            )
        return sections

    def iter_sections(self) -> Iterator[HtmlSection]:
        yield from self.load()

    def _section_urls(self, html: str) -> list[str]:
        parser = _LinkParser()
        parser.feed(html)
        return [
            url
            for href in parser.hrefs
            if (url := _allowed_url(href, self.entry_url, self._allowed_prefix))
        ]


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(href)


class _SectionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_main = False
        self.main_depth = 0
        self.title = ""
        self.headings: list[str] = []
        self.text_parts: list[str] = []
        self.code_blocks: list[str] = []
        self._code_parts: list[str] | None = None
        self._table_rows: list[list[str]] = []
        self._table_row: list[str] | None = None
        self._table_cell: list[str] | None = None
        self.tables: list[tuple[tuple[str, ...], ...]] = []
        self._heading_parts: list[str] | None = None
        self._heading_id: str | None = None
        self._section_title = ""
        self._sections: list[tuple[str, str, list[str], list[str], list[tuple[tuple[str, ...], ...]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "h1" and not self.title:
            self._heading_parts = []
        if tag == "div" and attributes.get("id") == "pageContentDiv":
            self.in_main = True
            self.main_depth = 1
            return
        if not self.in_main:
            return
        if tag == "div":
            self.main_depth += 1
        is_task_label = "tasklabel" in attributes.get("class", "")
        if tag in {"h2", "h3"} and not is_task_label:
            self._finish_section()
            self._heading_id = attributes.get("id")
            self._heading_parts = []
        elif tag in {"h4", "h5", "h6"}:
            self._heading_parts = []
        elif tag == "pre":
            self._code_parts = []
        elif tag == "tr":
            self._table_row = []
        elif tag in {"th", "td"} and self._table_row is not None:
            self._table_cell = []

    def handle_endtag(self, tag: str) -> None:
        if not self.in_main:
            if tag == "h1" and self._heading_parts is not None and not self.title:
                self.title = _clean_text(" ".join(self._heading_parts))
                self._heading_parts = None
            return
        if tag == "div":
            self.main_depth -= 1
            if self.main_depth == 0:
                self.in_main = False
            return
        if tag == "pre" and self._code_parts is not None:
            code = _clean_block("".join(self._code_parts))
            if code:
                self.code_blocks.append(code)
            self._code_parts = None
        elif tag in {"h2", "h3"} and self._heading_parts is not None:
            heading = _clean_text(" ".join(self._heading_parts))
            if heading and heading != "Chapter Contents":
                self._section_title = heading
            self._heading_parts = None
        elif tag in {"h4", "h5", "h6"}:
            self._heading_parts = None
        elif tag in {"th", "td"} and self._table_cell is not None:
            self._table_row.append(_clean_text(" ".join(self._table_cell)))
            self._table_cell = None
        elif tag == "tr" and self._table_row is not None:
            if any(self._table_row):
                self._table_rows.append(self._table_row)
            self._table_row = None
        elif tag == "table" and self._table_rows:
            self.tables.append(tuple(tuple(row) for row in self._table_rows))
            self._table_rows = []

    def handle_data(self, data: str) -> None:
        if self._heading_parts is not None:
            self._heading_parts.append(data)
        if self.in_main:
            if self._code_parts is not None:
                self._code_parts.append(data)
            elif self._table_cell is not None:
                self._table_cell.append(data)
            else:
                self.text_parts.append(data)

    def finish(self) -> None:
        self._finish_section()

    def _finish_section(self) -> None:
        if not self._section_title:
            return
        if not (self.text_parts or self.code_blocks or self.tables):
            return
        self._sections.append(
            (
                self._heading_id or "",
                self._section_title,
                self.text_parts,
                self.code_blocks,
                self.tables,
            )
        )
        self.text_parts = []
        self.code_blocks = []
        self.tables = []
        self._section_title = ""
        self._heading_id = None


def _parse_section(html: str, document_id: str, url: str) -> list[HtmlSection]:
    parser = _SectionParser()
    parser.feed(html)
    parser.finish()
    if not parser._sections:
        return []
    return [
        HtmlSection(
            document_id=document_id,
            section_url=f"{url}#{anchor}" if anchor else url,
            section_title=title,
            text=_clean_block("\n".join(text_parts)),
            headings=(title,),
            code_blocks=tuple(dict.fromkeys(code_blocks)),
            tables=tuple(tables),
        )
        for anchor, title, text_parts, code_blocks, tables in parser._sections
    ]


def _fetch_html(url: str) -> str:
    request = Request(url)
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _guide_prefix(url: str) -> str:
    path = urlsplit(url).path
    directory, filename = path.rsplit("/", 1)
    stem = filename.removesuffix(".html")
    return f"{directory}/{stem}/"


def _allowed_url(href: str, base_url: str, prefix: str) -> str | None:
    candidate = _normalize_url(urljoin(base_url, href))
    parsed = urlsplit(candidate)
    base = urlsplit(base_url)
    if parsed.scheme != base.scheme or parsed.netloc != base.netloc:
        return None
    if not parsed.path.startswith(prefix) or not parsed.path.endswith(".html"):
        return None
    return candidate


def _normalize_url(url: str) -> str:
    parsed = urlsplit(url)
    return parsed._replace(fragment="", query="").geturl()


def _clean_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _clean_block(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip()).strip()