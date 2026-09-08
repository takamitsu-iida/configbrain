from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from datetime import datetime, timezone


@dataclass(frozen=True)
class HtmlSection:
    document_id: str
    section_url: str
    section_title: str
    text: str
    headings: tuple[str, ...]
    code_blocks: tuple[str, ...]
    tables: tuple[tuple[tuple[str, ...], ...], ...]
    structural_units: tuple["HtmlStructuralUnit", ...] = ()


@dataclass(frozen=True)
class HtmlStructuralUnit:
    unit_type: str
    text: str
    element_id: str | None = None
    class_name: str | None = None


FetchHtml = Callable[[str], str]


class HtmlLoader:
    """Crawl one Cisco guide entry page and its same-guide HTML sections."""

    def __init__(
        self,
        entry_url: str,
        document_id: str,
        *,
        fetch_html: FetchHtml | None = None,
        storage_dir: Path | None = None,
        rendered_storage_dir: Path | None = None,
        metadata_path: Path | None = None,
        max_retries: int = 2,
    ) -> None:
        self.entry_url = _normalize_url(entry_url)
        self.document_id = document_id
        self._fetch_html = fetch_html or _fetch_html
        self._storage_dir = storage_dir
        self._rendered_storage_dir = rendered_storage_dir
        self._metadata_path = metadata_path
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        self._max_retries = max_retries
        self._allowed_prefix = _guide_prefix(self.entry_url)

    def load(self) -> list[HtmlSection]:
        entry_html = self._fetch_and_store(self.entry_url)
        urls = self._section_urls(entry_html)
        sections: list[HtmlSection] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            sections.extend(
                _parse_section(
                    self._fetch_and_store(url), self.document_id, url
                )
            )
        return sections

    def iter_sections(self) -> Iterator[HtmlSection]:
        yield from self.load()

    @classmethod
    def rendered(
        cls,
        entry_url: str,
        document_id: str,
        *,
        storage_dir: Path | None = None,
        rendered_storage_dir: Path | None = None,
        metadata_path: Path | None = None,
        render_page: Callable[[str], str] | None = None,
    ) -> "HtmlLoader":
        from app.ingestion.rendered_html_loader import RenderedHtmlFetcher

        fetcher = RenderedHtmlFetcher(render_page=render_page)
        return cls(
            entry_url,
            document_id,
            fetch_html=fetcher,
            storage_dir=storage_dir,
            rendered_storage_dir=rendered_storage_dir,
            metadata_path=metadata_path,
        )

    def _section_urls(self, html: str) -> list[str]:
        parser = _LinkParser()
        parser.feed(html)
        return [
            url
            for href in parser.hrefs
            if (url := _allowed_url(href, self.entry_url, self._allowed_prefix))
        ]

    def _fetch_and_store(self, url: str) -> str:
        started_at = datetime.now(timezone.utc).isoformat()
        for attempt in range(1, self._max_retries + 2):
            try:
                rendered = getattr(self._fetch_html, "render", None)
                rendered_result = rendered(url) if rendered else None
                html = rendered_result.html if rendered_result else self._fetch_html(url)
                retrieval_method = "rendered" if rendered_result else "static_html"
                stored_path = (
                    _store_html(
                        self._rendered_storage_dir if rendered_result else self._storage_dir,
                        self.document_id,
                        html,
                    )
                    if (self._rendered_storage_dir if rendered_result else self._storage_dir) is not None
                    else None
                )
                _write_fetch_record(
                    self._metadata_path,
                    {
                        "document_id": self.document_id,
                        "url": url,
                        "status": "success",
                        "attempts": attempt,
                        "fetched_at": started_at,
                        "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                        "stored_path": str(stored_path) if stored_path else None,
                        "retrieval_method": retrieval_method,
                        "browser": rendered_result.browser if rendered_result else None,
                        "selector": rendered_result.selector if rendered_result else None,
                    },
                )
                return html
            except Exception as error:
                if attempt > self._max_retries:
                    _write_fetch_record(
                        self._metadata_path,
                        {
                            "document_id": self.document_id,
                            "url": url,
                            "status": "failure",
                            "attempts": attempt,
                            "fetched_at": started_at,
                            "error_type": type(error).__name__,
                            "error": str(error),
                            "retrieval_method": "rendered" if rendered else "static_html",
                        },
                    )
                    raise
        raise AssertionError("unreachable")


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
        self._structure_stack: list[dict[str, object]] = []
        self._structural_units: list[HtmlStructuralUnit] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "h1" and not self.title:
            self._heading_parts = []
        if tag == "div" and attributes.get("id") == "pageContentDiv":
            self.in_main = True
            self.main_depth = 1
            return
        if self._ignored_depth:
            self._ignored_depth += 1
            return
        if not self.in_main:
            return
        if _is_noise_element(tag, attributes):
            self._ignored_depth = 1
            return
        if tag == "div":
            self.main_depth += 1
        if tag in {"article", "section", "pre", "table"}:
            self._structure_stack.append(
                {
                    "unit_type": tag,
                    "element_id": attributes.get("id"),
                    "class_name": attributes.get("class"),
                    "parts": [],
                }
            )
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
        if self._ignored_depth:
            self._ignored_depth -= 1
            return
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
            self._finish_structural_unit("pre")
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
            self._finish_structural_unit("table")
        elif tag in {"article", "section"}:
            self._finish_structural_unit(tag)

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._heading_parts is not None:
            self._heading_parts.append(data)
        for unit in self._structure_stack:
            parts = unit["parts"]
            assert isinstance(parts, list)
            parts.append(data)
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

    def _finish_structural_unit(self, unit_type: str) -> None:
        if not self._structure_stack:
            return
        unit = self._structure_stack.pop()
        if unit["unit_type"] != unit_type:
            return
        parts = unit["parts"]
        assert isinstance(parts, list)
        text = _clean_block("".join(parts))
        if text:
            self._structural_units.append(
                HtmlStructuralUnit(
                    unit_type=unit_type,
                    text=text,
                    element_id=unit["element_id"],
                    class_name=unit["class_name"],
                )
            )


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
            structural_units=tuple(parser._structural_units),
        )
        for anchor, title, text_parts, code_blocks, tables in parser._sections
    ]


def _is_noise_element(tag: str, attributes: dict[str, str | None]) -> bool:
    if tag in {"header", "footer", "nav", "aside"}:
        return True
    marker = " ".join(
        value.casefold()
        for key, value in attributes.items()
        if key in {"id", "class", "role"} and value
    )
    noise_markers = (
        "booktoc",
        "chaptertoc",
        "breadcrumb",
        "cookie",
        "feedback",
        "navigation",
        "skip-to",
        "searchresult",
    )
    return any(marker_name in marker for marker_name in noise_markers)


def _fetch_html(url: str) -> str:
    request = Request(url)
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _store_html(storage_dir: Path, document_id: str, html: str) -> Path:
    content_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
    output_dir = storage_dir / document_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{content_hash}.html"
    if not output_path.exists():
        output_path.write_text(html, encoding="utf-8")
    return output_path


def _write_fetch_record(metadata_path: Path | None, record: dict[str, object]) -> None:
    if metadata_path is None:
        return
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    with metadata_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


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