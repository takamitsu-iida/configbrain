from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock, patch

import pytest

from configbrain.download_manuals import (
    DownloadError,
    build_wget_command,
    download_manuals,
    load_catalog_documents,
    validate_pdf_url,
)

PDF_URL = "https://www.cisco.com/manual.pdf"
PDF_BYTES = b"%PDF-1.7\nmanual content\n"
UPDATED_PDF_BYTES = b"%PDF-1.7\nupdated manual\n"


class HeadResponse:
    def __init__(self, etag: str, content_length: int) -> None:
        self.headers = {"ETag": etag, "Content-Length": str(content_length)}

    def __enter__(self) -> HeadResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def geturl(self) -> str:
        return PDF_URL


def write_catalog(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "documents": [{"document_id": "guide-1", "url": PDF_URL}],
            }
        ),
        encoding="utf-8",
    )


def mock_wget(command: list[str], **kwargs: object) -> CompletedProcess[str]:
    output_path = Path(command[command.index("--output-document") + 1])
    output_path.write_bytes(PDF_BYTES)
    return CompletedProcess(command, 0, stdout="", stderr="")


def mock_updated_wget(command: list[str], **kwargs: object) -> CompletedProcess[str]:
    output_path = Path(command[command.index("--output-document") + 1])
    output_path.write_bytes(UPDATED_PDF_BYTES)
    return CompletedProcess(command, 0, stdout="", stderr="")


def test_validate_pdf_url_accepts_cisco_pdf() -> None:
    validate_pdf_url(PDF_URL)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.cisco.com/manual.pdf",
        "https://example.com/manual.pdf",
        "https://cdn.cisco.com/manual.pdf",
        "https://user:password@www.cisco.com/manual.pdf",
        "https://www.cisco.com/manual.html",
    ],
)
def test_validate_pdf_url_rejects_non_official_or_unsafe_urls(url: str) -> None:
    with pytest.raises(ValueError):
        validate_pdf_url(url)


def test_build_wget_command_downloads_to_a_temporary_path() -> None:
    output_path = Path("data/source_manuals/guide-1/.manual.pdf.tmp")
    command = build_wget_command(PDF_URL, output_path)

    assert command[0] == "wget"
    assert "--output-document" in command
    assert str(output_path) in command
    assert command[-1] == PDF_URL


def test_load_catalog_documents_rejects_unsafe_document_ids(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "documents": [{"document_id": "../outside", "url": PDF_URL}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DownloadError, match="invalid document_id"):
        load_catalog_documents(catalog_path)


def test_download_manuals_skips_unchanged_pdf_after_etag_check(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.json"
    write_catalog(catalog_path)
    head = HeadResponse("etag-1", len(PDF_BYTES))
    with patch("configbrain.download_manuals.urlopen", return_value=head):
        with patch(
            "configbrain.download_manuals.subprocess.run",
            side_effect=mock_wget,
        ) as wget:
            first = download_manuals(catalog_path, tmp_path / "pdfs")
    assert first == {"downloaded": 1, "updated": 0, "unchanged": 0}
    wget.assert_called_once()

    with patch("configbrain.download_manuals.urlopen", return_value=head):
        with patch("configbrain.download_manuals.subprocess.run") as wget:
            second = download_manuals(catalog_path, tmp_path / "pdfs")
    assert second == {"downloaded": 0, "updated": 0, "unchanged": 1}
    wget.assert_not_called()


def test_download_manuals_replaces_pdf_when_etag_changes(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    write_catalog(catalog_path)
    destination = tmp_path / "pdfs"
    with patch(
        "configbrain.download_manuals.urlopen",
        return_value=HeadResponse("etag-1", len(PDF_BYTES)),
    ):
        with patch(
            "configbrain.download_manuals.subprocess.run",
            side_effect=mock_wget,
        ):
            download_manuals(catalog_path, destination)

    with patch(
        "configbrain.download_manuals.urlopen",
        return_value=HeadResponse("etag-2", len(UPDATED_PDF_BYTES)),
    ):
        with patch(
            "configbrain.download_manuals.subprocess.run",
            side_effect=mock_updated_wget,
        ):
            summary = download_manuals(catalog_path, destination)

    assert summary == {"downloaded": 0, "updated": 1, "unchanged": 0}
    assert (destination / "guide-1" / "manual.pdf").read_bytes() == UPDATED_PDF_BYTES


def test_download_manuals_preserves_existing_pdf_when_wget_fails(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.json"
    write_catalog(catalog_path)
    destination = tmp_path / "pdfs"
    head = HeadResponse("etag-1", len(PDF_BYTES))
    with patch("configbrain.download_manuals.urlopen", return_value=head):
        with patch(
            "configbrain.download_manuals.subprocess.run",
            side_effect=mock_wget,
        ):
            download_manuals(catalog_path, destination)

    pdf_path = destination / "guide-1" / "manual.pdf"
    old_contents = pdf_path.read_bytes()
    changed_head = HeadResponse("etag-2", len(PDF_BYTES))
    with patch("configbrain.download_manuals.urlopen", return_value=changed_head):
        with patch(
            "configbrain.download_manuals.subprocess.run",
            return_value=Mock(returncode=8, stdout="", stderr="server unavailable"),
        ):
            with pytest.raises(DownloadError, match="server unavailable"):
                download_manuals(catalog_path, destination)

    assert pdf_path.read_bytes() == old_contents
    assert not list(pdf_path.parent.glob("*.tmp"))


def test_download_manuals_rejects_non_pdf_response(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    write_catalog(catalog_path)

    def write_html(command: list[str], **kwargs: object) -> CompletedProcess[str]:
        output_path = Path(command[command.index("--output-document") + 1])
        output_path.write_bytes(b"<html>not a PDF</html>")
        return CompletedProcess(command, 0, stdout="", stderr="")

    with patch(
        "configbrain.download_manuals.urlopen",
        return_value=HeadResponse("etag-1", 25),
    ):
        with patch(
            "configbrain.download_manuals.subprocess.run",
            side_effect=write_html,
        ):
            with pytest.raises(DownloadError, match="not a PDF"):
                download_manuals(catalog_path, tmp_path / "pdfs")
