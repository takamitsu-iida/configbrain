import io
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from scripts.download_manuals import DownloadError, download_manuals


def test_download_manuals_stores_pdf_by_document_id_and_updates_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"%PDF-1.7 test manual"
    manifest_path = tmp_path / "manuals.json"
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "document_id": "c9300_iosxe26_ip_addr_svc_cg",
                    "filename": "manual.pdf",
                    "url": "https://example.test/manual.pdf",
                    "sha256": "",
                    "size_bytes": 0,
                    "downloaded_at": "",
                }
            ]
        ),
        encoding="utf-8",
    )

    class FakeResponse:
        def __init__(self) -> None:
            self.stream = io.BytesIO(content)

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def read(self, size: int = -1) -> bytes:
            return self.stream.read(size)

    captured_user_agent = ""

    def fake_urlopen(request: object, **kwargs: object) -> FakeResponse:
        nonlocal captured_user_agent
        captured_user_agent = request.get_header("User-agent")
        return FakeResponse()

    monkeypatch.setattr("scripts.download_manuals.urlopen", fake_urlopen)

    paths = download_manuals(manifest_path, tmp_path / "raw")

    destination = tmp_path / "raw" / "c9300_iosxe26_ip_addr_svc_cg" / "manual.pdf"
    assert paths == [destination]
    assert destination.read_bytes() == content
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))[0]
    assert metadata["sha256"] == hashlib.sha256(content).hexdigest()
    assert metadata["size_bytes"] == len(content)
    assert metadata["downloaded_at"]
    assert captured_user_agent == "Wget/1.21.4"


def test_download_manuals_reuses_file_when_checksum_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"existing manual"
    checksum = hashlib.sha256(content).hexdigest()
    manifest_path = tmp_path / "manuals.json"
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "document_id": "manual-1",
                    "filename": "manual.pdf",
                    "url": "https://example.test/manual.pdf",
                    "sha256": checksum,
                    "size_bytes": len(content),
                    "downloaded_at": "2026-11-02T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )
    destination = tmp_path / "raw" / "manual-1" / "manual.pdf"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(content)

    def fail_download(*args: object, **kwargs: object) -> None:
        raise AssertionError("A matching local file should not be downloaded")

    monkeypatch.setattr("scripts.download_manuals.urlopen", fail_download)

    assert download_manuals(manifest_path, tmp_path / "raw") == [destination]


def test_download_manuals_reports_http_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "manuals.json"
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "document_id": "manual-1",
                    "filename": "manual.pdf",
                    "url": "https://example.test/manual.pdf",
                    "sha256": "",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.download_manuals.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            HTTPError(
                "https://example.test/manual.pdf",
                403,
                "Forbidden",
                {},
                None,
            )
        ),
    )

    with pytest.raises(DownloadError, match="HTTP 403"):
        download_manuals(manifest_path, tmp_path / "raw")