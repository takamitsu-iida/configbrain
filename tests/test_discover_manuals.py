import json
import sys
from pathlib import Path

import pytest

from scripts.discover_manuals import (
    DiscoveryError,
    catalog_differences,
    build_parser,
    discover_catalog,
    discover_chapters,
    discover_documents,
    normalize_cisco_url,
    main,
    validate_catalog_metadata,
)


SEED_URL = "https://www.cisco.com/c/en/us/support/switches/catalyst-9300-series-switches/products-release-notes-list.html"
PDF_URL = "https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/ip/b_26x_ip_9300_cg.pdf"
HTML_BOOK_URL = PDF_URL.removesuffix(".pdf") + ".html"
CHAPTER_URL = (
    "https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/"
    "release/26-x/configuration_guide/ip/b_26x_ip_9300_cg/configuring_dhcp.html"
)
REFERENCE_BOOK_URL = (
    "https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/"
    "release/26-x/command_reference/b_26x_9300_cr.html"
)
REFERENCE_PDF_URL = REFERENCE_BOOK_URL.removesuffix(".html") + ".pdf"
REFERENCE_CHAPTER_URL = REFERENCE_BOOK_URL.removesuffix(".html") + "/vlan_commands.html"


def book_html(pdf_url: str, chapter_url: str, title: str) -> str:
    return (
        f'<h1>{title}</h1><a href="{pdf_url}">PDF - Complete Book</a>'
        f'<ul id="bookToc"><li><a href="{chapter_url}">VLAN Commands</a></li></ul>'
    )


@pytest.fixture
def discovery_pages(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    pages = {
        SEED_URL: f'<h2>Configuration Guides</h2><a href="{PDF_URL}">IP Guide</a>',
        HTML_BOOK_URL: book_html(PDF_URL, CHAPTER_URL, "IP Configuration Guide"),
        REFERENCE_BOOK_URL: book_html(
            REFERENCE_PDF_URL, REFERENCE_CHAPTER_URL, "Command Reference",
        ),
    }

    def fetch(url: str) -> str:
        if url not in pages:
            raise DiscoveryError(f"Source unavailable: {url}")
        return pages[url]

    monkeypatch.setattr("scripts.discover_manuals.fetch_release_page", fetch)
    return pages


def test_discover_documents_filters_and_normalizes_release_pdf_links() -> None:
    html = f"""
    <h2>Configuration Guides</h2>
    <a href="{PDF_URL}#contents">IP Addressing Services Configuration Guide</a>
    <a href="{PDF_URL}">Duplicate</a>
    <a href="/c/en/us/td/docs/switches/lan/catalyst9300/software/release/17-12/configuration_guide/ip/other.pdf">Other release</a>
    <a href="https://example.test/manual.pdf">External manual</a>
    <a href="/c/en/us/support/switches/catalyst-9300-series-switches.html">Not a PDF</a>
    """

    documents = discover_documents("catalyst9300", "26.x", SEED_URL, html)

    assert len(documents) == 1
    assert documents[0]["title"] == "IP Addressing Services Configuration Guide"
    assert documents[0]["category"] == "Configuration Guides"
    assert documents[0]["filename"] == "b_26x_ip_9300_cg.pdf"
    assert documents[0]["url"] == PDF_URL
    assert documents[0]["html_book_url"] == HTML_BOOK_URL
    assert documents[0]["document_id"].startswith(
        "catalyst9300-26-x-b-26x-ip-9300-cg-"
    )


def test_discover_documents_derives_pdf_from_cisco_html_manual_links() -> None:
    html_url = PDF_URL.removesuffix(".pdf") + ".html"

    documents = discover_documents(
        "catalyst9300",
        "26.x",
        SEED_URL,
        f'<a href="{html_url}">IP Addressing Services Configuration Guide</a>',
    )

    assert documents[0]["url"] == PDF_URL
    assert documents[0]["html_book_url"] == HTML_BOOK_URL


def test_discover_chapters_keeps_order_and_filters_outside_book_and_release() -> None:
    html = f"""
    <ul id="bookToc">
      <li><a href="{CHAPTER_URL}">Configuring DHCP</a></li>
      <li><a href="https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/ip/b_26x_ip_9300_cg/advanced/relay.html">DHCP Relay</a></li>
      <li><a href="/c/en/us/td/docs/switches/lan/catalyst9300/software/release/17-12/configuration_guide/ip/b_26x_ip_9300_cg/old.html">Old release</a></li>
      <li><a href="https://example.test/other.html">External</a></li>
    </ul>
    <a href="{CHAPTER_URL}">Outside the book TOC</a>
    """

    chapters = discover_chapters(HTML_BOOK_URL, "26.x", html)

    assert [chapter["title"] for chapter in chapters] == [
        "Configuring DHCP",
        "DHCP Relay",
    ]
    assert [chapter["order"] for chapter in chapters] == [1, 2]
    assert chapters[0]["url"] == CHAPTER_URL


def test_normalize_cisco_url_rejects_non_official_or_non_https_urls() -> None:
    with pytest.raises(DiscoveryError, match="www.cisco.com"):
        normalize_cisco_url("https://cdn.cisco.com/manual.pdf", require_pdf=True)
    with pytest.raises(DiscoveryError, match="https"):
        normalize_cisco_url("http://www.cisco.com/manual.pdf", require_pdf=True)


def test_catalog_differences_detects_new_updated_and_removed_documents() -> None:
    previous = {
        "documents": [
            {"document_id": "unchanged", "title": "Same", "category": "Guides", "filename": "same.pdf", "url": "https://www.cisco.com/same.pdf"},
            {"document_id": "updated", "title": "Old", "category": "Guides", "filename": "updated.pdf", "url": "https://www.cisco.com/updated.pdf"},
            {"document_id": "removed", "title": "Removed", "category": "Guides", "filename": "removed.pdf", "url": "https://www.cisco.com/removed.pdf"},
        ]
    }
    current = [
        {"document_id": "unchanged", "title": "Same", "category": "Guides", "filename": "same.pdf", "url": "https://www.cisco.com/same.pdf"},
        {"document_id": "updated", "title": "New", "category": "Guides", "filename": "updated.pdf", "url": "https://www.cisco.com/updated.pdf"},
        {"document_id": "new", "title": "New", "category": "Guides", "filename": "new.pdf", "url": "https://www.cisco.com/new.pdf"},
    ]

    assert catalog_differences(previous, current) == {
        "new": ["new"],
        "updated": ["updated"],
        "removed": ["removed"],
    }


def test_catalog_differences_detects_html_chapter_changes() -> None:
    previous = {
        "documents": [
            {
                "document_id": "guide",
                "html_book_url": HTML_BOOK_URL,
                "html_status": "available",
                "chapters": [{"order": 1, "title": "Old title", "url": CHAPTER_URL}],
            }
        ]
    }
    current = [
        {
            "document_id": "guide",
            "html_book_url": HTML_BOOK_URL,
            "html_status": "available",
            "chapters": [{"order": 1, "title": "New title", "url": CHAPTER_URL}],
        }
    ]

    assert catalog_differences(previous, current)["updated"] == ["guide"]


def test_discover_catalog_saves_atomically_with_previous_catalog_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "catalog" / "c9300-iosxe26.json"
    output.parent.mkdir()
    output.write_text(
        json.dumps({"schema_version": 1, "documents": [{"document_id": "obsolete"}]}),
        encoding="utf-8",
    )
    def fake_fetch(url: str) -> str:
        if url == SEED_URL:
            return (
                f'<h3>Configuration Guides</h3><a href="{PDF_URL}">'
                "IP Addressing Services Configuration Guide</a>"
            )
        return book_html(PDF_URL, CHAPTER_URL, "IP Addressing Services Configuration Guide")

    monkeypatch.setattr("scripts.discover_manuals.fetch_release_page", fake_fetch)

    catalog = discover_catalog("catalyst9300", "26.x", SEED_URL, output)

    saved_catalog = json.loads(output.read_text(encoding="utf-8"))
    assert saved_catalog == catalog
    assert catalog["documents"][0]["html_status"] == "available"
    assert catalog["documents"][0]["chapters"][0]["url"] == CHAPTER_URL
    assert catalog["differences"]["new"] == [catalog["documents"][0]["document_id"]]
    assert catalog["differences"]["removed"] == ["obsolete"]
    assert not list(output.parent.glob(f".{output.name}.*"))


def test_typed_cli_merges_sources_and_reuses_them_on_refresh(
    tmp_path: Path, discovery_pages: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "candidate.json"
    monkeypatch.setattr(sys, "argv", [
        "discover_manuals.py", "--product", "catalyst9300", "--release", "26.x",
        "--configuration-guide-url", SEED_URL,
        "--command-reference-url", REFERENCE_BOOK_URL, "--output", str(output),
    ])
    assert main() == 0
    first = json.loads(output.read_bytes())
    assert first["schema_version"] == 2
    assert {(source["document_type"], source["page_type"]) for source in first["sources"]} == {
        ("configuration_guide", "listing"), ("command_reference", "book"),
    }
    assert {doc["url"] for doc in first["documents"]} == {PDF_URL, REFERENCE_PDF_URL}
    reference = next(doc for doc in first["documents"] if doc["document_type"] == "command_reference")
    assert reference["chapters"] == [{
        "order": 1, "title": "VLAN Commands", "url": REFERENCE_CHAPTER_URL,
    }]
    assert reference["discovery_sources"] == [REFERENCE_BOOK_URL]

    second = discover_catalog("catalyst9300", "26.x", None, output)
    assert second["documents"] == first["documents"]
    assert second["differences"] == {"new": [], "updated": [], "removed": []}

    # Explicit sources replace the source set, so removals must be reported.
    third = discover_catalog("catalyst9300", "26.x", SEED_URL, output)
    assert third["differences"]["removed"] == [reference["document_id"]]


def test_same_reference_from_listing_and_book_is_not_duplicated(
    tmp_path: Path, discovery_pages: dict[str, str],
) -> None:
    listing_url = "https://www.cisco.com/c/en/us/support/command-references.html"
    discovery_pages[listing_url] = f'<a href="{REFERENCE_BOOK_URL}">Command Reference</a>'
    catalog = discover_catalog(
        "catalyst9300", "26.x", None, tmp_path / "catalog.json",
        command_reference_urls=[listing_url, REFERENCE_BOOK_URL, listing_url],
    )
    assert len(catalog["documents"]) == 1
    assert catalog["documents"][0]["discovery_sources"] == sorted([listing_url, REFERENCE_BOOK_URL])
    assert len(catalog["sources"]) == 2


@pytest.mark.parametrize("failure", [
    "unavailable", "missing_pdf", "empty_toc", "wrong_type", "wrong_release", "wrong_product",
])
def test_invalid_or_failed_source_preserves_existing_catalog(
    tmp_path: Path, discovery_pages: dict[str, str], failure: str,
) -> None:
    output = tmp_path / "catalog.json"
    discover_catalog("catalyst9300", "26.x", SEED_URL, output)
    previous = output.read_bytes()
    url = REFERENCE_BOOK_URL
    if failure == "unavailable":
        del discovery_pages[url]
    elif failure == "missing_pdf":
        discovery_pages[url] = discovery_pages[url].replace(REFERENCE_PDF_URL, "/unrelated.pdf")
    elif failure == "empty_toc":
        discovery_pages[url] = book_html(REFERENCE_PDF_URL, CHAPTER_URL, "Reference")
    elif failure == "wrong_type":
        url = HTML_BOOK_URL
    else:
        changed_url = url.replace("26-x", "17-12") if failure == "wrong_release" else url.replace("catalyst9300", "catalyst9500")
        discovery_pages[changed_url] = discovery_pages[url]
        url = changed_url
    with pytest.raises(DiscoveryError):
        discover_catalog(
            "catalyst9300", "26.x", SEED_URL, output,
            command_reference_urls=[url],
        )
    assert output.read_bytes() == previous


def test_legacy_seed_alias_and_v1_refresh(
    tmp_path: Path, discovery_pages: dict[str, str],
) -> None:
    args = build_parser().parse_args([
        "--product", "catalyst9300", "--release", "26.x", "--seed-url", SEED_URL,
        "--configuration-guide-url", HTML_BOOK_URL, "--output", "catalog.json",
    ])
    assert args.configuration_guide_urls == [SEED_URL, HTML_BOOK_URL]
    output = tmp_path / "catalog.json"
    output.write_text(json.dumps({
        "schema_version": 1, "product": "catalyst9300", "release": "26.x",
        "seed_url": SEED_URL, "documents": [],
    }))
    catalog = discover_catalog("catalyst9300", "26.x", None, output)
    assert catalog["documents"][0]["url"] == PDF_URL


def test_missing_sources_fail_without_creating_catalog(tmp_path: Path) -> None:
    output = tmp_path / "catalog.json"
    with pytest.raises(DiscoveryError, match="Specify"):
        discover_catalog("catalyst9300", "26.x", None, output)
    assert not output.exists()


@pytest.mark.parametrize("kind", [None, "other", "configuration_guide"])
def test_v2_metadata_rejects_missing_unknown_or_mismatched_type(kind: str | None) -> None:
    with pytest.raises(DiscoveryError, match="document_type"):
        validate_catalog_metadata({
            "schema_version": 2,
            "documents": [{"document_type": kind, "url": REFERENCE_PDF_URL}],
        })


def test_fetch_rejects_redirect_to_another_document(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    from scripts.discover_manuals import fetch_release_page

    response = MagicMock()
    response.__enter__.return_value = response
    response.geturl.return_value = HTML_BOOK_URL.replace("26-x", "17-12")
    monkeypatch.setattr("scripts.discover_manuals.urlopen", lambda *args, **kwargs: response)
    with pytest.raises(DiscoveryError, match="Unexpected redirect"):
        fetch_release_page(HTML_BOOK_URL)
