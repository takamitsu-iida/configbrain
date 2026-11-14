"""Discover Cisco PDF manuals from an official product release page."""

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


class DiscoveryError(RuntimeError):
    """Raised when a release page or its discovered documents are invalid."""


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not slug:
        raise DiscoveryError(f"Value cannot be converted to an identifier: {value!r}")
    return slug


def normalize_release(value: str) -> str:
    return slugify(value)


def normalize_cisco_url(url: str, *, require_pdf: bool) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "www.cisco.com":
        raise DiscoveryError(f"URL must use https://www.cisco.com: {url}")
    if parsed.username or parsed.password or parsed.port:
        raise DiscoveryError(f"URL must not contain credentials or a port: {url}")

    normalized_path = posixpath.normpath(unquote(parsed.path))
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    if parsed.path.endswith("/") and normalized_path != "/":
        normalized_path += "/"
    if require_pdf and not normalized_path.casefold().endswith(".pdf"):
        raise DiscoveryError(f"URL is not a PDF: {url}")

    return urlunsplit(("https", "www.cisco.com", normalized_path, parsed.query, ""))


def is_for_release(url: str, release: str) -> bool:
    release_slug = normalize_release(release)
    path_segments = [slugify(segment) for segment in urlsplit(url).path.split("/") if segment]
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


def fetch_release_page(seed_url: str) -> str:
    normalized_seed_url = normalize_cisco_url(seed_url, require_pdf=False)
    request = Request(normalized_seed_url, headers={"User-Agent": "Wget/1.21.4"})
    try:
        with urlopen(request, timeout=60) as response:
            payload = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as error:
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


def discover_documents(product: str, release: str, seed_url: str, html: str) -> list[dict[str, str]]:
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
            },
        )

    return sorted(by_url.values(), key=lambda document: document["document_id"])


def catalog_differences(
    previous_catalog: dict[str, Any] | None,
    documents: list[dict[str, str]],
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
    comparable_fields = ("title", "category", "filename", "url")
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
    seed_url: str,
    output_path: Path,
) -> dict[str, Any]:
    if not product.strip() or not release.strip():
        raise DiscoveryError("Product and release must not be empty")
    html = fetch_release_page(seed_url)
    documents = discover_documents(product, release, seed_url, html)
    if not documents:
        raise DiscoveryError(f"No PDF manuals found for release {release}")
    previous_catalog = load_catalog(output_path)
    catalog = {
        "schema_version": 1,
        "product": product,
        "release": release,
        "seed_url": normalize_cisco_url(seed_url, require_pdf=False),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "documents": documents,
        "differences": catalog_differences(previous_catalog, documents),
    }
    save_catalog(catalog, output_path)
    return catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover Cisco PDF manuals from an official release page."
    )
    parser.add_argument("--product", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--seed-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        catalog = discover_catalog(args.product, args.release, args.seed_url, args.output)
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
