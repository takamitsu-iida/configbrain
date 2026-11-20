"""Discover Cisco configuration guides and command references."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import sys
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


DOCUMENT_TYPES = {
    "configuration_guide": "Configuration Guides",
    "command_reference": "Command References",
}


class DiscoveryError(RuntimeError):
    """Raised when a release page or its discovered documents are invalid."""


class PageNotFoundError(DiscoveryError):
    """Raised when a Cisco source page does not exist."""


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not slug:
        raise DiscoveryError(f"Value cannot be converted to an identifier: {value!r}")
    return slug


def normalize_release(value: str) -> str:
    return slugify(value)


def normalize_cisco_url(url: str, *, require_pdf: bool) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise DiscoveryError(f"Invalid Cisco URL: {url}") from error
    if parsed.scheme != "https" or parsed.hostname != "www.cisco.com":
        raise DiscoveryError(f"URL must use https://www.cisco.com: {url}")
    if parsed.username or parsed.password or port is not None:
        raise DiscoveryError(f"URL must not contain credentials or a port: {url}")

    normalized_path = posixpath.normpath(unquote(parsed.path))
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    if parsed.path.endswith("/") and normalized_path != "/":
        normalized_path += "/"
    if require_pdf and not normalized_path.casefold().endswith(".pdf"):
        raise DiscoveryError(f"URL is not a PDF: {url}")

    return urlunsplit(("https", "www.cisco.com", normalized_path, parsed.query, ""))


def document_type_for_url(url: str) -> str | None:
    segments = urlsplit(url).path.split("/")
    return next((kind for kind in DOCUMENT_TYPES if kind in segments), None)


def validate_catalog_metadata(catalog: dict[str, Any]) -> None:
    """Validate shared catalog versions and typed-document metadata."""
    version = catalog.get("schema_version")
    if type(version) is not int or version not in (1, 2):
        raise DiscoveryError("Catalog schema_version must be 1 or 2")
    documents = catalog.get("documents")
    if not isinstance(documents, list):
        raise DiscoveryError("Catalog documents must be a JSON array")
    for document in documents:
        if not isinstance(document, dict):
            raise DiscoveryError("Catalog documents must be JSON objects")
        kind = document.get("document_type")
        if kind is None and version == 1:
            continue
        if not isinstance(kind, str) or kind not in DOCUMENT_TYPES:
            raise DiscoveryError("Catalog document_type is missing or invalid")
        url = document.get("url")
        if (
            not isinstance(url, str)
            or document_type_for_url(normalize_cisco_url(url, require_pdf=True)) != kind
        ):
            raise DiscoveryError("Catalog document_type does not match its PDF URL")


def is_for_release(url: str, release: str) -> bool:
    release_slug = normalize_release(release)
    path_segments = [
        slugify(segment) for segment in urlsplit(url).path.split("/") if segment
    ]
    return release_slug in path_segments


def manual_pdf_url(url: str) -> str:
    normalized_url = normalize_cisco_url(url, require_pdf=False)
    path = urlsplit(normalized_url).path
    if path.casefold().endswith(".pdf"):
        return normalize_cisco_url(normalized_url, require_pdf=True)
    if path.casefold().endswith(".html"):
        return normalize_cisco_url(
            urlunsplit(
                (
                    "https",
                    "www.cisco.com",
                    str(Path(path).with_suffix(".pdf")),
                    "",
                    "",
                )
            ),
            require_pdf=True,
        )
    raise DiscoveryError(f"URL is neither a PDF nor a Cisco HTML manual: {url}")


def manual_html_url(url: str) -> str:
    normalized_url = normalize_cisco_url(url, require_pdf=True)
    parsed = urlsplit(normalized_url)
    return urlunsplit(
        (
            "https",
            "www.cisco.com",
            str(Path(parsed.path).with_suffix(".html")),
            "",
            "",
        )
    )


class ReleasePageParser(HTMLParser):
    """Collect links with the most recently encountered section heading."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._heading_depth = 0
        self._heading_parts: list[str] = []
        self._current_category = ""
        self._link_href: str | None = None
        self._link_parts: list[str] = []
        self.links: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_depth += 1
            self._heading_parts = []
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._link_href = href
                self._link_parts = []

    def handle_data(self, data: str) -> None:
        if self._heading_depth:
            self._heading_parts.append(data)
        if self._link_href is not None:
            self._link_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._heading_depth:
            self._heading_depth -= 1
            if not self._heading_depth:
                heading = " ".join("".join(self._heading_parts).split())
                if heading:
                    self._current_category = heading
        elif tag == "a" and self._link_href is not None:
            title = " ".join("".join(self._link_parts).split())
            self.links.append((self._link_href, title, self._current_category))
            self._link_href = None
            self._link_parts = []


class BookTocParser(HTMLParser):
    """Collect chapter links nested under Cisco's book table of contents."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._toc_depth = 0
        self._link_href: str | None = None
        self._link_parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "ul" and (self._toc_depth or attributes.get("id") == "bookToc"):
            self._toc_depth += 1
        elif tag == "a" and self._toc_depth:
            href = attributes.get("href")
            if href:
                self._link_href = href
                self._link_parts = []

    def handle_data(self, data: str) -> None:
        if self._link_href is not None:
            self._link_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._link_href is not None:
            title = " ".join("".join(self._link_parts).split())
            self.links.append((self._link_href, title))
            self._link_href = None
            self._link_parts = []
        elif tag == "ul" and self._toc_depth:
            self._toc_depth -= 1


def fetch_release_page(seed_url: str) -> str:
    normalized_seed_url = normalize_cisco_url(seed_url, require_pdf=False)
    request = Request(normalized_seed_url, headers={"User-Agent": "Wget/1.21.4"})
    try:
        with urlopen(request, timeout=60) as response:
            final_url = normalize_cisco_url(response.geturl(), require_pdf=False)
            if final_url != normalized_seed_url:
                raise DiscoveryError(
                    f"Unexpected redirect while retrieving {normalized_seed_url}: {final_url}"
                )
            payload = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as error:
        if error.code == 404:
            raise PageNotFoundError(
                f"HTTP 404 while retrieving {normalized_seed_url}"
            ) from error
        raise DiscoveryError(
            f"HTTP {error.code} while retrieving {normalized_seed_url}: {error.reason}"
        ) from error
    except URLError as error:
        raise DiscoveryError(
            f"Network error while retrieving {normalized_seed_url}: {error.reason}"
        ) from error
    except OSError as error:
        raise DiscoveryError(
            f"Could not retrieve release page {normalized_seed_url}: {error}"
        ) from error

    try:
        return payload.decode(charset)
    except UnicodeDecodeError as error:
        raise DiscoveryError(
            f"Could not decode release page {normalized_seed_url} as {charset}"
        ) from error


def document_id(product: str, release: str, url: str) -> str:
    filename = Path(urlsplit(url).path).stem
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"{slugify(product)}-{normalize_release(release)}-{slugify(filename)}-{url_hash}"


def discover_chapters(
    html_book_url: str, release: str, html: str
) -> list[dict[str, str | int]]:
    parser = BookTocParser()
    parser.feed(html)
    parser.close()

    book_path = urlsplit(html_book_url).path
    chapter_directory = f"{book_path.removesuffix('.html')}/"
    by_url: dict[str, dict[str, str | int]] = {}
    for href, title in parser.links:
        absolute_url = urljoin(html_book_url, href)
        try:
            chapter_url = normalize_cisco_url(absolute_url, require_pdf=False)
        except DiscoveryError:
            continue
        chapter_path = urlsplit(chapter_url).path
        if (
            not chapter_path.casefold().endswith(".html")
            or not chapter_path.startswith(chapter_directory)
            or not is_for_release(chapter_url, release)
            or not title
        ):
            continue
        by_url.setdefault(
            chapter_url,
            {
                "order": len(by_url) + 1,
                "title": title,
                "url": chapter_url,
            },
        )
    return list(by_url.values())


def discover_documents(
    product: str, release: str, seed_url: str, html: str
) -> list[dict[str, Any]]:
    normalized_seed_url = normalize_cisco_url(seed_url, require_pdf=False)
    parser = ReleasePageParser()
    parser.feed(html)
    parser.close()

    by_url: dict[str, dict[str, str]] = {}
    for href, title, category in parser.links:
        absolute_url = urljoin(normalized_seed_url, href)
        try:
            normalized_url = manual_pdf_url(absolute_url)
        except DiscoveryError:
            continue
        if not is_for_release(normalized_url, release):
            continue
        if not title:
            continue
        by_url.setdefault(
            normalized_url,
            {
                "document_id": document_id(product, release, normalized_url),
                "title": title,
                "category": category,
                "filename": Path(urlsplit(normalized_url).path).name,
                "url": normalized_url,
                "html_book_url": manual_html_url(normalized_url),
            },
        )

    return sorted(by_url.values(), key=lambda document: document["document_id"])


def catalog_differences(
    previous_catalog: dict[str, Any] | None,
    documents: list[dict[str, Any]],
) -> dict[str, list[str]]:
    previous_documents = (
        previous_catalog.get("documents", []) if previous_catalog is not None else []
    )
    if not isinstance(previous_documents, list):
        raise DiscoveryError("Existing catalog documents must be a JSON array")

    previous = {
        document["document_id"]: document
        for document in previous_documents
        if isinstance(document, dict) and isinstance(document.get("document_id"), str)
    }
    current = {document["document_id"]: document for document in documents}
    comparable_fields = (
        "title",
        "category",
        "filename",
        "url",
        "html_book_url",
        "html_status",
        "chapters",
        "document_type",
        "discovery_sources",
    )
    return {
        "new": sorted(current.keys() - previous.keys()),
        "updated": sorted(
            document_id
            for document_id in current.keys() & previous.keys()
            if any(
                current[document_id].get(field) != previous[document_id].get(field)
                for field in comparable_fields
            )
        ),
        "removed": sorted(previous.keys() - current.keys()),
    }


def load_catalog(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DiscoveryError(f"Could not read existing catalog: {path}") from error
    if not isinstance(catalog, dict):
        raise DiscoveryError("Existing catalog must be a JSON object")
    return catalog


def save_catalog(catalog: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(catalog, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
        os.replace(temporary_path, output_path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise DiscoveryError(f"Could not save catalog: {output_path}") from error


def discover_catalog(
    product: str,
    release: str,
    seed_url: str | None,
    output_path: Path,
    *,
    configuration_guide_urls: list[str] | None = None,
    command_reference_urls: list[str] | None = None,
) -> dict[str, Any]:
    if not product.strip() or not release.strip():
        raise DiscoveryError("Product and release must not be empty")
    previous_catalog = load_catalog(output_path)
    if previous_catalog is not None:
        validate_catalog_metadata(previous_catalog)
    if previous_catalog and any(
        previous_catalog.get(field, value) != value
        for field, value in (("product", product), ("release", release))
    ):
        raise DiscoveryError("Existing catalog product or release does not match")
    requested = [
        {"url": url, "document_type": kind}
        for kind, urls in (
            ("configuration_guide", ([seed_url] if seed_url else []) + (configuration_guide_urls or [])),
            ("command_reference", command_reference_urls or []),
        )
        for url in urls
    ]
    if not requested and previous_catalog:
        if previous_catalog.get("schema_version") == 2:
            requested = previous_catalog.get("sources", [])
        elif previous_catalog.get("schema_version") == 1:
            legacy_url = previous_catalog.get("seed_url")
            if isinstance(legacy_url, str) and legacy_url:
                requested = [{"url": legacy_url, "document_type": "configuration_guide"}]
    if not isinstance(requested, list) or not requested:
        raise DiscoveryError("Specify a configuration guide or command reference URL")

    sources: dict[str, dict[str, str]] = {}
    by_url: dict[str, dict[str, Any]] = {}
    pages: dict[str, str] = {}

    def fetch(url: str) -> str:
        if url not in pages:
            pages[url] = fetch_release_page(url)
        return pages[url]

    for source in requested:
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("url"), str)
            or not isinstance(source.get("document_type"), str)
            or source["document_type"] not in DOCUMENT_TYPES
        ):
            raise DiscoveryError("Invalid catalog discovery source")
        url = normalize_cisco_url(source["url"], require_pdf=False)
        kind = source["document_type"]
        if url in sources:
            if sources[url]["document_type"] != kind:
                raise DiscoveryError(f"Conflicting document types for source: {url}")
            continue
        html = fetch(url)
        soup = BeautifulSoup(html, "html.parser")
        is_book = soup.find("ul", id="bookToc") is not None
        if document_type_for_url(url) and not is_book:
            raise DiscoveryError(f"Source is not a book table of contents: {url}")
        sources[url] = {
            "url": url, "document_type": kind,
            "page_type": "book" if is_book else "listing",
        }
        if is_book:
            entries = [_discover_book(product, release, kind, url, html)]
        else:
            entries = []
            for document in discover_documents(product, release, url, html):
                pdf_url = document["url"]
                if not _is_for_product(pdf_url, product):
                    continue
                _validate_document_scope(pdf_url, product, release, kind)
                book_url = document["html_book_url"]
                book = _discover_book(product, release, kind, book_url, fetch(book_url))
                entries.append(book)
        if not entries:
            raise DiscoveryError(f"No {kind} manuals found for {product} / {release}: {url}")
        for document in entries:
            pdf_url = document["url"]
            if pdf_url in by_url:
                if by_url[pdf_url]["document_type"] != kind:
                    raise DiscoveryError(f"Conflicting document types for PDF: {pdf_url}")
                by_url[pdf_url]["discovery_sources"].append(url)
            else:
                document["discovery_sources"] = [url]
                by_url[pdf_url] = document

    documents = sorted(by_url.values(), key=lambda document: document["document_id"])
    for document in documents:
        document["discovery_sources"].sort()
    catalog = {
        "schema_version": 2,
        "product": product,
        "release": release,
        "sources": sorted(
            sources.values(), key=lambda source: (source["document_type"], source["url"]),
        ),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "documents": documents,
        "differences": catalog_differences(previous_catalog, documents),
    }
    validate_catalog_metadata(catalog)
    save_catalog(catalog, output_path)
    return catalog


def _is_for_product(url: str, product: str) -> bool:
    return slugify(product) in [
        slugify(segment) for segment in urlsplit(url).path.split("/") if segment
    ]


def _validate_document_scope(url: str, product: str, release: str, kind: str) -> None:
    if not _is_for_product(url, product) or not is_for_release(url, release):
        raise DiscoveryError(f"Document is outside {product} / {release}: {url}")
    if document_type_for_url(url) != kind:
        raise DiscoveryError(f"Document type does not match {kind}: {url}")


def _discover_book(
    product: str, release: str, kind: str, book_url: str, html: str,
) -> dict[str, Any]:
    _validate_document_scope(book_url, product, release, kind)
    soup = BeautifulSoup(html, "html.parser")
    pdf_urls: set[str] = set()
    for link in soup.select("a[href]"):
        href = urljoin(book_url, str(link["href"]))
        if not urlsplit(href).path.casefold().endswith(".pdf"):
            continue
        pdf_url = normalize_cisco_url(href, require_pdf=True)
        if manual_html_url(pdf_url) == book_url:
            pdf_urls.add(pdf_url)
    if len(pdf_urls) != 1:
        raise DiscoveryError(f"Expected one explicit complete-book PDF link: {book_url}")
    pdf_url = pdf_urls.pop()
    heading = soup.find("h1")
    title = heading.get_text(" ", strip=True) if heading else ""
    if not title:
        raise DiscoveryError(f"Book title is missing: {book_url}")
    chapters = discover_chapters(book_url, release, html)
    if not chapters:
        raise DiscoveryError(f"Book table of contents is empty: {book_url}")
    return {
        "document_id": document_id(product, release, pdf_url),
        "document_type": kind, "title": title, "category": DOCUMENT_TYPES[kind],
        "filename": Path(urlsplit(pdf_url).path).name, "url": pdf_url,
        "html_book_url": book_url, "html_status": "available", "chapters": chapters,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
    )
    parser.add_argument("--product", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument(
        "--configuration-guide-url", "--seed-url",
        dest="configuration_guide_urls", action="append",
        help="Configuration guide source URL (repeatable); --seed-url is a compatibility alias.",
    )
    parser.add_argument(
        "--command-reference-url", dest="command_reference_urls", action="append",
        help="Command reference source URL (repeatable).",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        catalog = discover_catalog(
            args.product, args.release, None, args.output,
            configuration_guide_urls=args.configuration_guide_urls,
            command_reference_urls=args.command_reference_urls,
        )
    except DiscoveryError as error:
        print(f"Manual discovery failed: {error}", file=sys.stderr)
        return 1

    differences = catalog["differences"]
    print(
        f"Discovered {len(catalog['documents'])} manual(s): "
        f"{len(differences['new'])} new, {len(differences['updated'])} updated, "
        f"{len(differences['removed'])} removed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
