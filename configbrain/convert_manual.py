"""Convert Cisco HTML into a guide OKF package or searchable command records."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

import pypdfium2
from bs4 import BeautifulSoup
from frictionless import Package
from markdownify import MarkdownConverter

from configbrain.manual_errors import ConversionError
from scripts.discover_manuals import (
    DiscoveryError, is_for_release, normalize_cisco_url, validate_catalog_metadata,
)


class MissingImageError(ConversionError):
    """Raised when an optional source image is not published by Cisco."""


DOCUMENT_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class TablePreservingMarkdownConverter(MarkdownConverter):
    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        self.fenced_code_block_count = 0

    def convert_table(
        self, element: Any, text: str, parent_tags: set[str] | None = None
    ) -> str:
        return f"\n\n{element}\n\n"

    def convert_pre(
        self, element: Any, text: str, parent_tags: set[str] | None = None
    ) -> str:
        body = element.get_text()
        if parent_tags and "table" in parent_tags:
            return body
        self.fenced_code_block_count += 1
        longest_fence = max(
            (len(run) for run in re.findall(r"`+", body)),
            default=0,
        )
        fence = "`" * max(3, longest_fence + 1)
        return f"\n\n{fence}\n{body.rstrip()}\n{fence}\n\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise ConversionError(f"Could not read source PDF: {path}") from error
    return digest.hexdigest()


def load_document(
    catalog_path: Path, document_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConversionError(f"Could not read catalog: {catalog_path}") from error
    if not isinstance(catalog, dict):
        raise ConversionError("Catalog must be a JSON object")
    try:
        validate_catalog_metadata(catalog)
    except DiscoveryError as error:
        raise ConversionError(str(error)) from error
    if not isinstance(catalog.get("product"), str) or not isinstance(
        catalog.get("release"), str
    ):
        raise ConversionError("Catalog must contain product and release")
    if not DOCUMENT_ID_PATTERN.fullmatch(document_id):
        raise ConversionError(f"Invalid document_id: {document_id}")

    documents = catalog.get("documents")
    if not isinstance(documents, list):
        raise ConversionError("Catalog documents must be a JSON array")
    matches = [
        item
        for item in documents
        if isinstance(item, dict) and item.get("document_id") == document_id
    ]
    if len(matches) != 1:
        raise ConversionError(
            f"Expected exactly one catalog entry for document_id: {document_id}"
        )
    document = matches[0]
    for field in ("title", "filename", "url", "html_book_url"):
        if not isinstance(document.get(field), str) or not document[field]:
            raise ConversionError(f"Catalog document is missing {field}: {document_id}")
    if Path(document["filename"]).name != document["filename"]:
        raise ConversionError(f"Invalid catalog filename: {document['filename']}")
    if document.get("html_status") != "available":
        raise ConversionError(
            f"HTML chapters are not available for document_id: {document_id}"
        )
    if not document["html_book_url"].casefold().endswith(".html"):
        raise ConversionError(f"Catalog HTML book URL is not HTML: {document_id}")

    chapters = document.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ConversionError(f"Catalog has no HTML chapters: {document_id}")
    book_url = validate_cisco_url(document["html_book_url"], catalog["release"])
    book_path = urlsplit(book_url).path.removesuffix(".html") + "/"
    seen_orders: set[int] = set()
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise ConversionError("Catalog chapters must be JSON objects")
        order = chapter.get("order")
        title = chapter.get("title")
        url = chapter.get("url")
        if (
            not isinstance(order, int)
            or order < 1
            or order in seen_orders
            or not isinstance(title, str)
            or not title.strip()
            or not isinstance(url, str)
        ):
            raise ConversionError(f"Invalid catalog chapter in {document_id}")
        chapter_url = validate_cisco_url(url, catalog["release"])
        if (
            not urlsplit(chapter_url).path.casefold().endswith(".html")
            or not urlsplit(chapter_url).path.startswith(book_path)
        ):
            raise ConversionError(f"Chapter is outside its HTML book: {chapter_url}")
        seen_orders.add(order)

    pdf_url = validate_cisco_url(document["url"], catalog["release"], require_pdf=True)
    if not pdf_url.endswith(".pdf"):
        raise ConversionError(f"Catalog URL is not a PDF: {pdf_url}")
    return catalog, document


def validate_cisco_url(
    url: str, release: str, *, require_pdf: bool = False
) -> str:
    try:
        normalized = normalize_cisco_url(url, require_pdf=require_pdf)
    except DiscoveryError as error:
        raise ConversionError(f"Invalid Cisco source URL: {url}") from error
    if not is_for_release(normalized, release):
        raise ConversionError(f"Source URL is outside release {release}: {url}")
    return normalized


def fetch_chapter_page(url: str, release: str) -> bytes:
    normalized_url = validate_cisco_url(url, release)
    request = Request(
        normalized_url,
        headers={"User-Agent": "Wget/1.21.4"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            final_url = validate_cisco_url(response.geturl(), release)
            if final_url != normalized_url:
                raise ConversionError(
                    f"Cisco redirected chapter to an unexpected URL: {final_url}"
                )
            content = response.read()
    except HTTPError as error:
        raise ConversionError(
            f"HTTP {error.code} while retrieving chapter: {normalized_url}"
        ) from error
    except URLError as error:
        raise ConversionError(
            f"Network error while retrieving chapter {normalized_url}: {error.reason}"
        ) from error
    except OSError as error:
        raise ConversionError(
            f"Could not retrieve chapter {normalized_url}: {error}"
        ) from error
    if not content:
        raise ConversionError(f"Empty HTML chapter: {normalized_url}")
    return content


def fetch_source_image(url: str) -> tuple[bytes, str, str]:
    try:
        normalized_url = normalize_cisco_url(url, require_pdf=False)
    except DiscoveryError as error:
        raise ConversionError(f"Invalid Cisco image URL: {url}") from error
    request = Request(normalized_url, headers={"User-Agent": "Wget/1.21.4"})
    try:
        with urlopen(request, timeout=60) as response:
            final_url = normalize_cisco_url(response.geturl(), require_pdf=False)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
            content = response.read()
    except HTTPError as error:
        if error.code == 404:
            raise MissingImageError(
                f"HTTP 404 while retrieving image: {normalized_url}"
            ) from error
        raise ConversionError(
            f"HTTP {error.code} while retrieving image: {normalized_url}"
        ) from error
    except URLError as error:
        raise ConversionError(
            f"Network error while retrieving image {normalized_url}: {error.reason}"
        ) from error
    except OSError as error:
        raise ConversionError(
            f"Could not retrieve image {normalized_url}: {error}"
        ) from error
    if not content:
        raise ConversionError(f"Empty Cisco image: {normalized_url}")

    extension = Path(urlsplit(final_url).path).suffix.casefold()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
        extension = mimetypes.guess_extension(content_type, strict=False) or ""
    mediatype = content_type or mimetypes.guess_type(final_url)[0] or ""
    if not extension or not mediatype.startswith("image/"):
        raise ConversionError(
            f"Cisco image has an unsupported media type: {normalized_url}"
        )
    return content, mediatype, final_url


def normalize_pdf_heading(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def is_pdf_chapter_heading(page_text: str, title: str) -> bool:
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    if not lines:
        return False
    if normalize_pdf_heading(title) == "preface":
        return normalize_pdf_heading(lines[0]) == "preface"
    if not re.fullmatch(r"CHAPTER\s+\d+", lines[0], re.IGNORECASE):
        return False
    expected = normalize_pdf_heading(title)
    return any(
        normalize_pdf_heading(" ".join(lines[1 : 1 + line_count])) == expected
        for line_count in range(1, min(3, len(lines) - 1) + 1)
    )


def map_chapters_to_pdf_pages(
    pdf_path: Path, chapters: list[dict[str, Any]]
) -> tuple[int, dict[int, list[int]]]:
    try:
        pdf = pypdfium2.PdfDocument(pdf_path)
    except (OSError, pypdfium2.PdfiumError) as error:
        raise ConversionError(f"Could not read source PDF: {pdf_path}") from error

    try:
        page_count = len(pdf)
        matches: dict[int, list[int]] = {chapter["order"]: [] for chapter in chapters}
        for page_index in range(len(pdf)):
            text_page = pdf[page_index].get_textpage()
            try:
                page_text = text_page.get_text_range()
            finally:
                text_page.close()
            for chapter in chapters:
                if is_pdf_chapter_heading(page_text, chapter["title"]):
                    matches[chapter["order"]].append(page_index + 1)
    except pypdfium2.PdfiumError as error:
        raise ConversionError(f"Could not extract PDF page text: {pdf_path}") from error
    finally:
        pdf.close()
    return page_count, matches


def extract_chapter_content(
    html: bytes,
    chapter: dict[str, Any],
    chapter_url: str,
    image_assets: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("div", id="chapterContent")
    if content is None:
        raise ConversionError(f"Cisco chapter has no chapterContent: {chapter_url}")

    for element in content.select("script, style, nav"):
        element.decompose()
    html_content_sha256 = sha256_bytes(str(content).encode("utf-8"))
    for element in content.select("[href]"):
        element["href"] = urljoin(chapter_url, str(element["href"]))
    for element in content.select("img[src]"):
        image_url = urljoin(chapter_url, str(element["src"]))
        if image_assets is None:
            element["src"] = image_url
            continue
        try:
            normalized_url = normalize_cisco_url(image_url, require_pdf=False)
        except DiscoveryError as error:
            raise ConversionError(f"Invalid Cisco image URL: {image_url}") from error
        asset = image_assets.get(normalized_url)
        if asset is None:
            try:
                image_bytes, mediatype, resolved_url = fetch_source_image(
                    normalized_url
                )
            except MissingImageError as error:
                image_assets[normalized_url] = {
                    "source_url": normalized_url,
                    "status": "not_found",
                    "error": str(error),
                }
                element["src"] = normalized_url
                continue
            extension = Path(urlsplit(resolved_url).path).suffix.casefold()
            if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
                extension = mimetypes.guess_extension(mediatype, strict=False) or ""
            filename = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:16]
            relative_path = f"images/{filename}{extension}"
            asset = {
                "source_url": normalized_url,
                "resolved_url": resolved_url,
                "status": "available",
                "path": relative_path,
                "bytes": image_bytes,
                "sha256": sha256_bytes(image_bytes),
                "size_bytes": len(image_bytes),
                "mediatype": mediatype,
                "format": extension.removeprefix("."),
            }
            image_assets[normalized_url] = asset
        element["src"] = asset.get("path", normalized_url)

    converter = TablePreservingMarkdownConverter(heading_style="ATX", bullets="- ")
    markdown = converter.convert(str(content)).strip()
    table_count = len(content.find_all("table"))
    preserved_table_count = len(re.findall(r"<table\b", markdown, re.IGNORECASE))
    if table_count != preserved_table_count:
        raise ConversionError(
            f"Table preservation check failed for chapter: {chapter_url}"
        )
    preformatted_count = len(content.find_all("pre"))
    preformatted_in_tables_count = len(content.select("table pre"))
    preserved_preformatted_in_tables = len(
        re.findall(r"<pre\b", markdown, re.IGNORECASE)
    )
    if preformatted_in_tables_count != preserved_preformatted_in_tables:
        raise ConversionError(
            f"Table code preservation check failed for chapter: {chapter_url}"
        )
    standalone_preformatted_count = (
        preformatted_count - preformatted_in_tables_count
    )
    if converter.fenced_code_block_count != standalone_preformatted_count:
        raise ConversionError(
            f"Code block preservation check failed for chapter: {chapter_url}"
        )
    fence_delimiters = sum(
        re.fullmatch(r"[ \t]*`{3,}[ \t]*", line) is not None
        for line in markdown.splitlines()
    )
    if fence_delimiters != converter.fenced_code_block_count * 2:
        raise ConversionError(
            f"Rendered code block check failed for chapter: {chapter_url}"
        )
    if not markdown:
        raise ConversionError(f"Cisco chapter has no convertible content: {chapter_url}")

    section_anchors = [
        str(element["id"])
        for element in content.select("[id]")
        if isinstance(element.get("id"), str) and element["id"]
    ]
    heading = soup.find(id="chapterTitleDiv")
    html_title = (
        " ".join(heading.get_text(" ", strip=True).split()) if heading else None
    )
    statistics = {
        "table_count": table_count,
        "table_row_count": len(content.find_all("tr")),
        "preformatted_block_count": preformatted_count,
        "preformatted_in_tables_count": preformatted_in_tables_count,
        "fenced_code_block_count": converter.fenced_code_block_count,
        "html_content_sha256": html_content_sha256,
        "section_anchors": section_anchors,
        "html_title": html_title,
    }
    return markdown, statistics


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def resource_descriptor(
    name: str, path: Path, relative_path: str, mediatype: str, format_name: str
) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "name": name,
        "path": relative_path,
        "format": format_name,
        "mediatype": mediatype,
        "bytes": len(content),
        "hash": f"sha256:{sha256_bytes(content)}",
    }


def validate_package(package_dir: Path) -> None:
    report = Package(str(package_dir / "datapackage.json")).validate()
    if not report.valid:
        details = "; ".join(
            f"{error.get('code')}: {error.get('message')}"
            for error in report.flatten(["code", "message"])
        )
        raise ConversionError(f"Frictionless package validation failed: {details}")


def packages_match(first: Path, second: Path) -> bool:
    first_files = {
        path.relative_to(first)
        for path in first.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second)
        for path in second.rglob("*")
        if path.is_file()
    }
    if first_files != second_files:
        return False
    return all(
        (first / relative_path).read_bytes() == (second / relative_path).read_bytes()
        for relative_path in first_files
    )


def promote_package(staged_dir: Path, output_dir: Path) -> None:
    if output_dir.is_symlink() or (output_dir.exists() and not output_dir.is_dir()):
        raise ConversionError(f"Package output path is not a directory: {output_dir}")
    backup_dir: Path | None = None
    backup_is_copy = False
    if output_dir.exists():
        backup_dir = output_dir.with_name(f".{output_dir.name}.backup")
        if backup_dir.exists():
            raise ConversionError(f"Package backup path already exists: {backup_dir}")
        try:
            os.replace(output_dir, backup_dir)
        except OSError as rename_error:
            try:
                shutil.copytree(output_dir, backup_dir)
            except OSError as copy_error:
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                raise ConversionError(
                    f"Could not preserve existing package by rename "
                    f"({rename_error}) or copy ({copy_error}): {output_dir}"
                ) from copy_error
            backup_is_copy = True
            try:
                shutil.rmtree(output_dir)
            except OSError as remove_error:
                shutil.rmtree(backup_dir)
                raise ConversionError(
                    f"Could not replace existing package; it remains at {output_dir}"
                ) from remove_error
    try:
        os.replace(staged_dir, output_dir)
    except OSError as rename_error:
        try:
            if output_dir.exists():
                shutil.rmtree(output_dir)
            shutil.copytree(staged_dir, output_dir)
        except OSError as copy_error:
            if output_dir.exists():
                shutil.rmtree(output_dir)
            if backup_dir is not None:
                try:
                    if backup_is_copy:
                        shutil.copytree(backup_dir, output_dir)
                    else:
                        os.replace(backup_dir, output_dir)
                except OSError as restore_error:
                    raise ConversionError(
                        f"Could not promote package ({rename_error}; {copy_error}) "
                        f"or restore the previous package; it remains at {backup_dir}"
                    ) from restore_error
                if backup_is_copy:
                    try:
                        shutil.rmtree(backup_dir)
                    except OSError as cleanup_error:
                        raise ConversionError(
                            f"Previous package was restored but backup cleanup failed: "
                            f"{backup_dir}"
                        ) from cleanup_error
            raise ConversionError(
                f"Could not promote package by rename ({rename_error}) or copy "
                f"({copy_error}): {output_dir}"
            ) from copy_error
    if backup_dir is not None:
        try:
            shutil.rmtree(backup_dir)
        except OSError as error:
            raise ConversionError(
                f"Package was updated but the previous package could not be removed: "
                f"{backup_dir}"
            ) from error


def convert_manual(
    catalog_path: Path,
    document_id: str,
    source_dir: Path,
    output_root: Path,
    *,
    reference_output_root: Path = Path("data/command_references"),
) -> dict[str, Any]:
    catalog, document = load_document(catalog_path, document_id)
    if document.get("document_type") == "command_reference":
        from configbrain.command_references import convert_command_reference

        return convert_command_reference(catalog, document, reference_output_root)
    pdf_path = source_dir / document_id / document["filename"]
    if not pdf_path.is_file():
        raise ConversionError(f"Source PDF does not exist: {pdf_path}")
    try:
        with pdf_path.open("rb") as source_pdf:
            if source_pdf.read(5) != b"%PDF-":
                raise ConversionError(f"Source file is not a PDF: {pdf_path}")
    except OSError as error:
        raise ConversionError(f"Could not read source PDF: {pdf_path}") from error

    pdf_hash = sha256_file(pdf_path)
    pdf_size = pdf_path.stat().st_size
    pdf_page_count, page_matches = map_chapters_to_pdf_pages(pdf_path, document["chapters"])

    output_root.mkdir(parents=True, exist_ok=True)
    output_dir = output_root / document_id
    if output_dir.is_symlink():
        raise ConversionError(f"OKF output path must not be a symlink: {output_dir}")
    if output_dir.resolve().parent != output_root.resolve():
        raise ConversionError(f"Unsafe OKF output path: {output_dir}")
    output_existed = output_dir.exists()
    with tempfile.TemporaryDirectory(
        prefix=f".{document_id}.", dir=output_root
    ) as temporary_directory:
        staged_dir = Path(temporary_directory) / document_id
        staged_dir.mkdir()
        manual_parts = [
            f"# {document['title']}",
            "",
            f"- Product: `{catalog['product']}`",
            f"- Release: `{catalog['release']}`",
            f"- Source PDF: [{document['filename']}]({document['url']})",
            f"- Source PDF SHA-256: `{pdf_hash}`",
            f"- HTML book: [{document['title']}]({document['html_book_url']})",
            "",
            "PDF page indices below are 1-based positions in the original PDF. "
            "A page is assigned only when its explicit chapter heading has one "
            "unique exact match; other mappings remain unverified.",
            "",
        ]
        chapter_provenance: list[dict[str, Any]] = []
        total_tables = 0
        total_preformatted_blocks = 0
        total_preformatted_in_tables = 0
        total_fenced_code_blocks = 0
        image_assets: dict[str, dict[str, Any]] = {}
        for chapter in sorted(document["chapters"], key=lambda item: item["order"]):
            chapter_url = validate_cisco_url(chapter["url"], catalog["release"])
            html = fetch_chapter_page(chapter_url, catalog["release"])
            chapter_markdown, statistics = extract_chapter_content(
                html, chapter, chapter_url, image_assets
            )
            candidates = page_matches[chapter["order"]]
            mapping_status = (
                "matched"
                if len(candidates) == 1
                else "ambiguous"
                if candidates
                else "not_found"
            )
            page_index = candidates[0] if len(candidates) == 1 else None
            source_link = f"[{chapter['title']}]({chapter_url})"
            manual_parts.extend(
                [
                    f"## {chapter['title']}",
                    "",
                    f"> HTML source: {source_link}",
                    (
                        f"> PDF page index: {page_index}"
                        if page_index is not None
                        else "> PDF page index: not mapped"
                    ),
                    "",
                    chapter_markdown,
                    "",
                ]
            )
            chapter_provenance.append(
                {
                    "order": chapter["order"],
                    "title": chapter["title"],
                    "html_url": chapter_url,
                    "pdf_page_mapping": {
                        "status": mapping_status,
                        "page_index": page_index,
                        "candidate_page_indexes": candidates,
                        "method": "unique exact title after PDF CHAPTER marker",
                    },
                    **statistics,
                }
            )
            total_tables += statistics["table_count"]
            total_preformatted_blocks += statistics["preformatted_block_count"]
            total_preformatted_in_tables += statistics[
                "preformatted_in_tables_count"
            ]
            total_fenced_code_blocks += statistics["fenced_code_block_count"]

        manual_path = staged_dir / "manual.md"
        manual_path.write_text("\n".join(manual_parts).rstrip() + "\n", encoding="utf-8")
        provenance = {
            "schema_version": 1,
            "document": {
                "document_id": document_id,
                "title": document["title"],
                "category": document.get("category"),
                "product": catalog["product"],
                "release": catalog["release"],
            },
            "source_pdf": {
                "url": document["url"],
                "filename": document["filename"],
                "sha256": pdf_hash,
                "size_bytes": pdf_size,
                "page_count": pdf_page_count,
            },
            "source_html_book_url": document["html_book_url"],
            "images": [
                {
                    key: value
                    for key, value in asset.items()
                    if key != "bytes"
                }
                for asset in sorted(
                    image_assets.values(), key=lambda item: item["source_url"]
                )
            ],
            "conversion": {
                "method": "Cisco chapter HTML converted to Markdown",
                "table_policy": "Preserve each source table as raw HTML",
                "page_mapping_policy": (
                    "Assign a PDF page index only for one exact chapter-title "
                    "match following a CHAPTER marker"
                ),
                "totals": {
                    "chapter_count": len(chapter_provenance),
                    "table_count": total_tables,
                    "preformatted_block_count": total_preformatted_blocks,
                    "preformatted_in_tables_count": total_preformatted_in_tables,
                    "fenced_code_block_count": total_fenced_code_blocks,
                    "image_reference_count": len(image_assets),
                    "image_count": sum(
                        asset["status"] == "available"
                        for asset in image_assets.values()
                    ),
                    "unavailable_image_count": sum(
                        asset["status"] != "available"
                        for asset in image_assets.values()
                    ),
                },
            },
            "chapters": chapter_provenance,
        }
        provenance_path = staged_dir / "provenance.json"
        write_json(provenance_path, provenance)
        resources = [
            resource_descriptor(
                "manual",
                manual_path,
                "manual.md",
                "text/markdown",
                "md",
            ),
            resource_descriptor(
                "provenance",
                provenance_path,
                "provenance.json",
                "application/json",
                "json",
            ),
        ]
        available_images = [
            asset
            for asset in image_assets.values()
            if asset["status"] == "available"
        ]
        if available_images:
            images_dir = staged_dir / "images"
            images_dir.mkdir()
            for index, asset in enumerate(
                sorted(available_images, key=lambda item: item["path"]),
                start=1,
            ):
                image_path = staged_dir / asset["path"]
                image_path.write_bytes(asset["bytes"])
                resources.append(
                    resource_descriptor(
                        f"image-{index:04d}",
                        image_path,
                        asset["path"],
                        asset["mediatype"],
                        asset["format"],
                    )
                )
        descriptor = {
            "profile": "data-package",
            "name": document_id,
            "title": document["title"],
            "resources": resources,
            "configbrain": {
                "schema_version": 1,
                "product": catalog["product"],
                "release": catalog["release"],
                "document_id": document_id,
                "source_pdf_sha256": pdf_hash,
                "pdf_page_mapping": "chapter title match; unknown pages stay unmapped",
            },
        }
        write_json(staged_dir / "datapackage.json", descriptor)
        validate_package(staged_dir)

        if output_dir.is_dir() and packages_match(staged_dir, output_dir):
            result_status = "unchanged"
        else:
            promote_package(staged_dir, output_dir)
            result_status = "updated" if output_existed else "created"

    matched_pages = sum(
        chapter["pdf_page_mapping"]["status"] == "matched"
        for chapter in chapter_provenance
    )
    return {
        "status": result_status,
        "output": output_dir,
        "chapter_count": len(chapter_provenance),
        "mapped_chapter_count": matched_pages,
        "unmapped_chapter_count": len(chapter_provenance) - matched_pages,
        "table_count": total_tables,
        "preformatted_block_count": total_preformatted_blocks,
        "fenced_code_block_count": total_fenced_code_blocks,
        "image_count": len(available_images),
        "unavailable_image_count": len(image_assets) - len(available_images),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a Cisco guide to OKF or a command reference to command records."
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("data/source_manuals"),
        help="Directory containing catalogued source PDFs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/okf"),
        help="Directory for generated OKF packages.",
    )
    parser.add_argument(
        "--reference-output",
        type=Path,
        default=Path("data/command_references"),
        help="Directory for command reference HTML and command records; no PDF required.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = convert_manual(
            args.catalog,
            args.document_id,
            args.source_dir,
            args.output,
            reference_output_root=args.reference_output,
        )
    except (ConversionError, DiscoveryError, OSError) as error:
        print(f"Manual conversion failed: {error}", file=sys.stderr)
        return 1

    if result.get("document_type") == "command_reference":
        print(
            f"Command reference {result['status']}: {result['output']} — "
            f"{result['chapter_count']} chapters, {result['command_count']} commands, "
            f"{result['subcommand_count']} subcommands, "
            f"{result['unparsed_count']} unparsed, {result['warning_count']} warnings."
        )
        return 0

    print(
        f"OKF {result['status']}: {result['output']} — "
        f"{result['chapter_count']} chapters, "
        f"{result['mapped_chapter_count']} PDF page mappings, "
        f"{result['unmapped_chapter_count']} unmapped, "
        f"{result['table_count']} tables, "
        f"{result['preformatted_block_count']} preformatted blocks, "
        f"{result['image_count']} local images, "
        f"{result['unavailable_image_count']} unavailable."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
