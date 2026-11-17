import json
from pathlib import Path

import pytest

from scripts.discover_manuals import (
    DiscoveryError,
    catalog_differences,
    discover_catalog,
    discover_chapters,
    discover_documents,
    normalize_cisco_url,
)


SEED_URL = "https://www.cisco.com/c/en/us/support/switches/catalyst-9300-series-switches/products-release-notes-list.html"
PDF_URL = "https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/ip/b_26x_ip_9300_cg.pdf"
HTML_BOOK_URL = PDF_URL.removesuffix(".pdf") + ".html"
CHAPTER_URL = (
    "https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/"
    "release/26-x/configuration_guide/ip/b_26x_ip_9300_cg/configuring_dhcp.html"
)


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
        json.dumps({"documents": [{"document_id": "obsolete"}]}),
        encoding="utf-8",
    )
    def fake_fetch(url: str) -> str:
        if url == SEED_URL:
            return (
                f'<h3>Configuration Guides</h3><a href="{PDF_URL}">'
                "IP Addressing Services Configuration Guide</a>"
            )
        return (
            f'<ul id="bookToc"><li><a href="{CHAPTER_URL}">'
            "Configuring DHCP</a></li></ul>"
        )

    monkeypatch.setattr("scripts.discover_manuals.fetch_release_page", fake_fetch)

    catalog = discover_catalog("catalyst9300", "26.x", SEED_URL, output)

    saved_catalog = json.loads(output.read_text(encoding="utf-8"))
    assert saved_catalog == catalog
    assert catalog["documents"][0]["html_status"] == "available"
    assert catalog["documents"][0]["chapters"][0]["url"] == CHAPTER_URL
    assert catalog["differences"]["new"] == [catalog["documents"][0]["document_id"]]
    assert catalog["differences"]["removed"] == ["obsolete"]
    assert not list(output.parent.glob(f".{output.name}.*"))
