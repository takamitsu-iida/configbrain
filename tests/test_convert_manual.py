import json
import os
from pathlib import Path

import pytest

from configbrain.convert_manual import (
    ConversionError,
    MissingImageError,
    convert_manual,
    extract_chapter_content,
    is_pdf_chapter_heading,
    promote_package,
)


DOCUMENT_ID = "catalyst9300-26-x-test-guide"
PDF_URL = (
    "https://www.cisco.com/c/en/us/td/docs/switches/catalyst9300/"
    "release/26-x/test/guide.pdf"
)
BOOK_URL = PDF_URL.removesuffix(".pdf") + ".html"
CHAPTER_URL = BOOK_URL.removesuffix(".html") + "/configuring_dhcp.html"
CHAPTER_HTML = b"""
<html><body>
  <div id="chapterTitleDiv"><h1>Configuring DHCP</h1></div>
  <div id="chapterContent">
    <article id="configuring-dhcp">
      <h2>Procedure</h2>
      <table><thead><tr><th>Step</th><th>Command</th></tr></thead>
        <tbody><tr><td rowspan="2">1</td><td>ip dhcp snooping
          <pre><code>show ip dhcp snooping</code></pre></td></tr>
        <tr><td>ip dhcp snooping vlan 10</td></tr></tbody>
      </table>
      <pre><code>interface GigabitEthernet1/0/1
 description edge</code></pre>
      <img src="/content/dam/en/us/td/i/icons/note.svg" alt="Note">
    </article>
  </div>
</body></html>
"""


def write_catalog_and_pdf(tmp_path: Path) -> tuple[Path, Path, Path]:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "product": "catalyst9300",
                "release": "26.x",
                "documents": [
                    {
                        "document_id": DOCUMENT_ID,
                        "title": "Test Configuration Guide",
                        "category": "Configuration Guides",
                        "filename": "guide.pdf",
                        "url": PDF_URL,
                        "html_book_url": BOOK_URL,
                        "html_status": "available",
                        "chapters": [
                            {
                                "order": 1,
                                "title": "Configuring DHCP",
                                "url": CHAPTER_URL,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    source_dir = tmp_path / "source_manuals"
    pdf_path = source_dir / DOCUMENT_ID / "guide.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.7\nsynthetic test document\n")
    return catalog_path, source_dir, pdf_path


def test_extract_chapter_preserves_table_structure_and_cli() -> None:
    markdown, statistics = extract_chapter_content(
        CHAPTER_HTML,
        {"order": 1, "title": "Configuring DHCP"},
        CHAPTER_URL,
    )

    assert "<table>" in markdown
    assert 'rowspan="2"' in markdown
    assert "ip dhcp snooping vlan 10" in markdown
    assert "```" in markdown
    assert statistics["table_count"] == 1
    assert statistics["table_row_count"] == 3
    assert statistics["preformatted_block_count"] == 2
    assert statistics["preformatted_in_tables_count"] == 1
    assert statistics["fenced_code_block_count"] == 1
    assert statistics["section_anchors"] == ["configuring-dhcp"]


def test_pdf_page_mapping_requires_an_explicit_chapter_heading() -> None:
    assert is_pdf_chapter_heading(
        "CHAPTER 3\nConfiguring DHCP\nThis section provides information.",
        "Configuring DHCP",
    )
    assert not is_pdf_chapter_heading(
        "CONTENTS\nCHAPTER 3 Configuring DHCP\nHow to Configure DHCP, on page 35",
        "Configuring DHCP",
    )
    assert not is_pdf_chapter_heading(
        "CHAPTER 3\nConfiguring IPv6\nConfiguring DHCP appears later.",
        "Configuring DHCP",
    )


def test_missing_cisco_image_is_recorded_and_keeps_source_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_url = "https://www.cisco.com/content/dam/en/us/td/i/icons/note.svg"

    def missing_image(url: str) -> tuple[bytes, str, str]:
        raise MissingImageError("HTTP 404")

    monkeypatch.setattr(
        "configbrain.convert_manual.fetch_source_image",
        missing_image,
    )
    assets: dict[str, dict[str, object]] = {}

    markdown, _ = extract_chapter_content(
        CHAPTER_HTML,
        {"order": 1, "title": "Configuring DHCP"},
        CHAPTER_URL,
        assets,
    )

    assert image_url in markdown
    assert assets[image_url]["status"] == "not_found"
    assert assets[image_url]["error"] == "HTTP 404"


def test_convert_manual_builds_valid_package_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path, source_dir, pdf_path = write_catalog_and_pdf(tmp_path)
    monkeypatch.setattr(
        "configbrain.convert_manual.fetch_chapter_page",
        lambda url, release: CHAPTER_HTML,
    )
    monkeypatch.setattr(
        "configbrain.convert_manual.map_chapters_to_pdf_pages",
        lambda pdf_path, chapters: (3, {1: [2]}),
    )
    monkeypatch.setattr(
        "configbrain.convert_manual.fetch_source_image",
        lambda url: (b"<svg></svg>", "image/svg+xml", url),
    )

    output_root = tmp_path / "okf"
    result = convert_manual(catalog_path, DOCUMENT_ID, source_dir, output_root)

    assert result["status"] == "created"
    assert result["mapped_chapter_count"] == 1
    assert result["image_count"] == 1
    package_dir = output_root / DOCUMENT_ID
    manual = (package_dir / "manual.md").read_text(encoding="utf-8")
    assert "PDF page index: 2" in manual
    assert "rowspan=\"2\"" in manual
    assert "images/" in manual
    assert len(list((package_dir / "images").iterdir())) == 1
    provenance = json.loads(
        (package_dir / "provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["source_pdf"]["sha256"]
    assert provenance["chapters"][0]["pdf_page_mapping"]["page_index"] == 2
    assert provenance["images"][0]["source_url"].startswith("https://www.cisco.com/")

    second = convert_manual(catalog_path, DOCUMENT_ID, source_dir, output_root)

    assert second["status"] == "unchanged"
    assert pdf_path.is_file()


def test_failed_conversion_keeps_previous_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path, source_dir, _ = write_catalog_and_pdf(tmp_path)
    monkeypatch.setattr(
        "configbrain.convert_manual.fetch_chapter_page",
        lambda url, release: b"<html><body>No chapter content</body></html>",
    )
    monkeypatch.setattr(
        "configbrain.convert_manual.map_chapters_to_pdf_pages",
        lambda pdf_path, chapters: (1, {1: []}),
    )
    output_dir = tmp_path / "okf" / DOCUMENT_ID
    output_dir.mkdir(parents=True)
    previous_marker = output_dir / "previous-package.txt"
    previous_marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ConversionError, match="chapterContent"):
        convert_manual(catalog_path, DOCUMENT_ID, source_dir, output_dir.parent)

    assert previous_marker.read_text(encoding="utf-8") == "keep"


def test_package_promotion_falls_back_to_copy_when_rename_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged_dir = tmp_path / "staged"
    output_dir = tmp_path / "package"
    staged_dir.mkdir()
    output_dir.mkdir()
    (staged_dir / "manual.md").write_text("new", encoding="utf-8")
    (output_dir / "manual.md").write_text("old", encoding="utf-8")
    original_replace = os.replace

    def deny_staged_rename(source: Path, destination: Path) -> None:
        if Path(source) == staged_dir:
            raise PermissionError("rename denied")
        original_replace(source, destination)

    monkeypatch.setattr("configbrain.convert_manual.os.replace", deny_staged_rename)

    promote_package(staged_dir, output_dir)

    assert (output_dir / "manual.md").read_text(encoding="utf-8") == "new"
    assert not output_dir.with_name(".package.backup").exists()


def test_package_promotion_copies_previous_output_when_backup_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged_dir = tmp_path / "staged"
    output_dir = tmp_path / "package"
    staged_dir.mkdir()
    output_dir.mkdir()
    (staged_dir / "manual.md").write_text("new", encoding="utf-8")
    (output_dir / "manual.md").write_text("old", encoding="utf-8")
    original_replace = os.replace

    def deny_output_rename(source: Path, destination: Path) -> None:
        if Path(source) == output_dir:
            raise PermissionError("backup rename denied")
        original_replace(source, destination)

    monkeypatch.setattr("configbrain.convert_manual.os.replace", deny_output_rename)

    promote_package(staged_dir, output_dir)

    assert (output_dir / "manual.md").read_text(encoding="utf-8") == "new"
    assert not output_dir.with_name(".package.backup").exists()


def test_failed_package_promotion_restores_previous_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged_dir = tmp_path / "staged"
    output_dir = tmp_path / "package"
    staged_dir.mkdir()
    output_dir.mkdir()
    (staged_dir / "manual.md").write_text("new", encoding="utf-8")
    previous_marker = output_dir / "manual.md"
    previous_marker.write_text("old", encoding="utf-8")
    original_replace = os.replace

    def deny_staged_rename(source: Path, destination: Path) -> None:
        if Path(source) == staged_dir:
            raise PermissionError("rename denied")
        original_replace(source, destination)

    def deny_copy(source: Path, destination: Path) -> None:
        raise PermissionError("copy denied")

    monkeypatch.setattr("configbrain.convert_manual.os.replace", deny_staged_rename)
    monkeypatch.setattr("configbrain.convert_manual.shutil.copytree", deny_copy)

    with pytest.raises(ConversionError, match="rename.*copy"):
        promote_package(staged_dir, output_dir)

    assert previous_marker.read_text(encoding="utf-8") == "old"
    assert not output_dir.with_name(".package.backup").exists()
