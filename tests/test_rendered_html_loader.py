from __future__ import annotations

import json
from pathlib import Path

from app.ingestion.rendered_html_loader import RenderedHtmlFetcher, rendered_loader


ENTRY_URL = "https://docs.example.test/guides/vlan/book.html"
SECTION_URL = "https://docs.example.test/guides/vlan/book/configuring_vlans.html"


def test_rendered_html_fetcher_uses_injected_renderer() -> None:
    fetcher = RenderedHtmlFetcher(
        render_page=lambda url: f"<html><body><main>{url}</main></body></html>"
    )

    rendered = fetcher.render(SECTION_URL)

    assert rendered.url == SECTION_URL
    assert rendered.browser == "chromium"
    assert rendered.selector == "document.documentElement"
    assert SECTION_URL in rendered.html
    assert fetcher(SECTION_URL) == rendered.html


def test_rendered_loader_stores_rendered_html_and_metadata(tmp_path: Path) -> None:
    pages = {
        ENTRY_URL: '<div id="bookToc"><a href="book/configuring_vlans.html">VLANs</a></div>',
        SECTION_URL: (
            '<div id="pageContentDiv">'
            '<h2 id="vlan">VLANs</h2><p>Rendered content.</p>'
            '</div>'
        ),
    }
    metadata_path = tmp_path / "html_fetches.jsonl"

    loader = rendered_loader(
        ENTRY_URL,
        "doc-1",
        rendered_storage_dir=tmp_path / "rendered_html",
        metadata_path=metadata_path,
        render_page=pages.__getitem__,
    )

    sections = loader.load()

    assert len(sections) == 1
    assert sections[0].section_url == f"{SECTION_URL}#vlan"
    assert "Rendered content." in sections[0].text

    rendered_files = list((tmp_path / "rendered_html" / "doc-1").glob("*.html"))
    assert len(rendered_files) == 2
    assert all(path.read_text(encoding="utf-8") in pages.values() for path in rendered_files)

    records = [
        json.loads(line)
        for line in metadata_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 2
    assert all(record["retrieval_method"] == "rendered" for record in records)
    assert all(record["stored_path"].endswith(".html") for record in records)


def test_rendered_loader_deduplicates_same_rendered_html(tmp_path: Path) -> None:
    pages = {
        ENTRY_URL: (
            '<div id="bookToc">'
            '<a href="configuring_vlans.html">VLANs</a>'
            '<a href="configuring_vlans.html#vlan">VLANs again</a>'
            '</div>'
        ),
        SECTION_URL: '<div id="pageContentDiv"><h2>VLANs</h2><p>Same page.</p></div>',
    }

    rendered_loader(
        ENTRY_URL,
        "doc-1",
        rendered_storage_dir=tmp_path / "rendered_html",
        render_page=pages.__getitem__,
    ).load()

    rendered_files = list((tmp_path / "rendered_html" / "doc-1").glob("*.html"))
    assert len(rendered_files) == 1
    assert all(len(path.stem) == 64 for path in rendered_files)
