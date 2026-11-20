from __future__ import annotations

import json
import os
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock, patch

import pytest

from configbrain.download_manuals import (
    DownloadError,
    build_wget_command,
    download_manuals,
    load_catalog_documents,
    main,
    validate_pdf_url,
)

PDF_URL = "https://www.cisco.com/manual.pdf"
PDF_BYTES = b"%PDF-1.7\nmanual content\n"
UPDATED_PDF_BYTES = b"%PDF-1.7\nupdated manual\n"
REFERENCE_URL = (
    "https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/"
    "release/26-x/command_reference/b_26x_9300_cr.pdf"
)


class HeadResponse:
    def __init__(
        self, etag: str | None, content_length: int, *,
        url: str = PDF_URL, last_modified: str | None = None,
    ) -> None:
        self.headers = {
            "ETag": etag, "Content-Length": str(content_length), "Last-Modified": last_modified,
        }
        self.url = url

    def __enter__(self) -> HeadResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def geturl(self) -> str:
        return self.url


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
    state_path = pdf_path.parent / ".download-state.json"
    old_state = state_path.read_bytes()
    changed_head = HeadResponse("etag-2", len(PDF_BYTES))
    with patch("configbrain.download_manuals.urlopen", return_value=changed_head):
        with patch(
            "configbrain.download_manuals.subprocess.run",
            return_value=Mock(returncode=8, stdout="", stderr="server unavailable"),
        ):
            with pytest.raises(DownloadError, match="server unavailable"):
                download_manuals(catalog_path, destination)

    assert pdf_path.read_bytes() == old_contents
    assert state_path.read_bytes() == old_state
    assert {path.name for path in pdf_path.parent.iterdir()} == {"manual.pdf", ".download-state.json"}


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


def test_v2_cli_downloads_only_selected_reference_and_skips_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    catalog = tmp_path / "candidate.json"
    catalog.write_text(json.dumps({
        "schema_version": 2, "documents": [
            {
                "document_id": "guide-1", "document_type": "configuration_guide",
                "url": REFERENCE_URL.replace("/command_reference/", "/configuration_guide/"),
            },
            {"document_id": "reference-1", "document_type": "command_reference", "url": REFERENCE_URL},
        ],
    }))
    destination = tmp_path / "pdfs"
    guide = destination / "guide-1" / "guide.pdf"
    guide.parent.mkdir(parents=True)
    guide.write_bytes(b"existing guide")
    head = Mock(return_value=HeadResponse("ref-etag", len(PDF_BYTES), url=REFERENCE_URL))
    wget = Mock(side_effect=mock_wget)
    monkeypatch.setattr("configbrain.download_manuals.urlopen", head)
    monkeypatch.setattr("configbrain.download_manuals.subprocess.run", wget)
    args = [
        "--catalog", str(catalog), "--destination", str(destination),
        "--document-id", "reference-1", "--document-id", "reference-1",
    ]
    assert main(args) == 0
    assert "1 downloaded" in capsys.readouterr().out
    assert head.call_count == 1
    assert head.call_args.args[0].full_url == REFERENCE_URL
    assert wget.call_args.args[0][-1] == REFERENCE_URL
    pdf = destination / "reference-1" / "b_26x_9300_cr.pdf"
    assert pdf.read_bytes() == PDF_BYTES
    state = json.loads((pdf.parent / ".download-state.json").read_bytes())
    assert state["url"] == REFERENCE_URL
    assert main(args) == 0
    assert "1 unchanged" in capsys.readouterr().out
    assert wget.call_count == 1
    assert guide.read_bytes() == b"existing guide"


def test_unknown_selected_id_fails_before_network_or_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.json"
    write_catalog(catalog)
    head = Mock()
    monkeypatch.setattr("configbrain.download_manuals.urlopen", head)
    destination = tmp_path / "pdfs"
    with pytest.raises(DownloadError, match="Unknown catalog document ID"):
        download_manuals(catalog, destination, document_ids=["guide-1", "unknown"])
    head.assert_not_called()
    assert not destination.exists()


@pytest.mark.parametrize("with_validator", [True, False])
def test_last_modified_or_missing_validators_control_refetch(
    tmp_path: Path, with_validator: bool,
) -> None:
    catalog = tmp_path / "catalog.json"
    write_catalog(catalog)
    response = HeadResponse(
        None, len(PDF_BYTES), last_modified="Wed, 18 Nov 2026 00:00:00 GMT" if with_validator else None,
    )
    with patch("configbrain.download_manuals.urlopen", return_value=response):
        with patch("configbrain.download_manuals.subprocess.run", side_effect=mock_wget) as wget:
            download_manuals(catalog, tmp_path / "pdfs")
            summary = download_manuals(catalog, tmp_path / "pdfs")
    assert summary == (
        {"downloaded": 0, "updated": 0, "unchanged": 1}
        if with_validator else {"downloaded": 0, "updated": 1, "unchanged": 0}
    )
    assert wget.call_count == (1 if with_validator else 2)


@pytest.mark.parametrize("failure", ["state_staging", "state_promotion", "pdf_promotion"])
@pytest.mark.parametrize("existing", [True, False])
def test_failed_promotion_preserves_pdf_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, existing: bool,
) -> None:
    catalog = tmp_path / "catalog.json"
    write_catalog(catalog)
    destination = tmp_path / "pdfs"
    pdf = destination / "guide-1" / "manual.pdf"
    state = pdf.parent / ".download-state.json"
    if existing:
        with patch(
            "configbrain.download_manuals.urlopen", return_value=HeadResponse("old", len(PDF_BYTES)),
        ), patch("configbrain.download_manuals.subprocess.run", side_effect=mock_wget):
            download_manuals(catalog, destination)
    old_state = state.read_bytes() if existing else None
    original_replace = os.replace

    def fail_replace(source: Path, target: Path) -> None:
        target = Path(target)
        if (
            (failure == "state_staging" and target.name.startswith(".download-state."))
            or (failure == "state_promotion" and target == state)
            or (failure == "pdf_promotion" and target == pdf)
        ):
            raise PermissionError("test storage failure")
        original_replace(source, target)

    monkeypatch.setattr("configbrain.download_manuals.os.replace", fail_replace)
    with patch(
        "configbrain.download_manuals.urlopen", return_value=HeadResponse("new", len(UPDATED_PDF_BYTES)),
    ), patch("configbrain.download_manuals.subprocess.run", side_effect=mock_updated_wget):
        with pytest.raises(DownloadError, match="(save download state|store PDF)"):
            download_manuals(catalog, destination)
    if existing:
        assert pdf.read_bytes() == PDF_BYTES
        assert state.read_bytes() == old_state
        assert {path.name for path in pdf.parent.iterdir()} == {"manual.pdf", ".download-state.json"}
    else:
        assert not list(pdf.parent.iterdir())


def test_redirected_reference_does_not_replace_existing_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.json"
    write_catalog(catalog)
    response = HeadResponse("etag", len(PDF_BYTES), url=REFERENCE_URL.replace("26-x", "17-12"))
    wget = Mock()
    monkeypatch.setattr("configbrain.download_manuals.urlopen", lambda *args, **kwargs: response)
    monkeypatch.setattr("configbrain.download_manuals.subprocess.run", wget)
    with pytest.raises(DownloadError, match="Unexpected redirect"):
        download_manuals(catalog, tmp_path / "pdfs")
    wget.assert_not_called()


def test_failed_rollback_retains_previous_pdf_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.json"
    write_catalog(catalog)
    destination = tmp_path / "pdfs"
    with patch(
        "configbrain.download_manuals.urlopen", return_value=HeadResponse("old", len(PDF_BYTES)),
    ), patch("configbrain.download_manuals.subprocess.run", side_effect=mock_wget):
        download_manuals(catalog, destination)
    pdf = destination / "guide-1" / "manual.pdf"
    state = pdf.parent / ".download-state.json"
    old_state = state.read_bytes()
    original_replace = os.replace

    def fail_state_and_restore(source: Path, target: Path) -> None:
        if Path(target) == state or Path(source).suffix == ".backup":
            raise PermissionError("test rollback failure")
        original_replace(source, target)

    monkeypatch.setattr("configbrain.download_manuals.os.replace", fail_state_and_restore)
    with patch(
        "configbrain.download_manuals.urlopen", return_value=HeadResponse("new", len(UPDATED_PDF_BYTES)),
    ), patch("configbrain.download_manuals.subprocess.run", side_effect=mock_updated_wget):
        with pytest.raises(DownloadError, match="Previous PDF backup remains at") as error:
            download_manuals(catalog, destination)
    backups = list(pdf.parent.glob("*.backup"))
    assert len(backups) == 1
    assert str(backups[0]) in str(error.value)
    assert backups[0].read_bytes() == PDF_BYTES
    assert state.read_bytes() == old_state
    assert not list(pdf.parent.glob("*.tmp"))


def test_local_hash_change_requires_refetch_even_when_size_and_etag_match(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    write_catalog(catalog)
    destination = tmp_path / "pdfs"
    with patch(
        "configbrain.download_manuals.urlopen", return_value=HeadResponse("same", len(PDF_BYTES)),
    ), patch("configbrain.download_manuals.subprocess.run", side_effect=mock_wget) as wget:
        download_manuals(catalog, destination)
        pdf = destination / "guide-1" / "manual.pdf"
        pdf.write_bytes(PDF_BYTES.replace(b"content", b"contEnt"))
        assert download_manuals(catalog, destination) == {
            "downloaded": 0, "updated": 1, "unchanged": 0,
        }
    assert wget.call_count == 2
    assert pdf.read_bytes() == PDF_BYTES
