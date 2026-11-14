"""Download Cisco manuals described by a JSON manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class DownloadError(RuntimeError):
    """Raised when a manual cannot be downloaded or validated."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download manuals listed in a JSON manifest."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manuals.json"),
        help="Path to the manual manifest.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/products/catalyst9300/raw"),
        help="Root directory for downloaded PDFs.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Download files again even when the recorded checksum matches.",
    )
    return parser


def load_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    try:
        documents = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DownloadError(f"Could not read manifest: {manifest_path}") from error

    if not isinstance(documents, list):
        raise DownloadError("Manifest must contain a JSON array")
    for document in documents:
        if not isinstance(document, dict):
            raise DownloadError("Each manifest entry must be a JSON object")
        for field in ("document_id", "filename", "url"):
            if not isinstance(document.get(field), str) or not document[field]:
                raise DownloadError(f"Manifest entry is missing a valid {field}")
        document_id = document["document_id"]
        filename = document["filename"]
        if Path(document_id).name != document_id or Path(document_id).is_absolute():
            raise DownloadError(f"Unsafe document_id: {document_id}")
        if Path(filename).name != filename or Path(filename).is_absolute():
            raise DownloadError(f"Unsafe filename: {filename}")
    return documents


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_document(document: dict[str, Any], output_root: Path, force: bool) -> Path:
    document_id = document["document_id"]
    filename = document["filename"]
    destination_dir = output_root / document_id
    destination = destination_dir / filename

    if (
        not force
        and destination.is_file()
        and document.get("sha256")
        and sha256_file(destination) == document["sha256"]
    ):
        return destination

    destination_dir.mkdir(parents=True, exist_ok=True)
    request = Request(
        document["url"],
        headers={"User-Agent": "Wget/1.21.4"},
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination_dir, prefix=f".{filename}.", delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            with urlopen(request, timeout=60) as response:
                shutil.copyfileobj(response, temporary_file)
    except HTTPError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise DownloadError(
            f"HTTP {error.code} while downloading {document['url']}: {error.reason}"
        ) from error
    except URLError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise DownloadError(f"Network error while downloading {document['url']}: {error.reason}") from error
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise DownloadError(f"Could not save downloaded file for {document_id}: {error}") from error

    checksum = sha256_file(temporary_path)
    expected_checksum = document.get("sha256")
    if expected_checksum and checksum != expected_checksum:
        temporary_path.unlink(missing_ok=True)
        raise DownloadError(f"Checksum mismatch for {document_id}")

    os.replace(temporary_path, destination)
    document["sha256"] = checksum
    document["size_bytes"] = destination.stat().st_size
    document["downloaded_at"] = datetime.now(timezone.utc).isoformat()
    return destination


def save_manifest(manifest_path: Path, documents: list[dict[str, Any]]) -> None:
    temporary_path = manifest_path.with_suffix(f"{manifest_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(documents, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, manifest_path)


def download_manuals(
    manifest_path: Path,
    output_root: Path,
    force: bool = False,
) -> list[Path]:
    documents = load_manifest(manifest_path)
    downloaded_paths: list[Path] = []
    changed = False
    for document in documents:
        previous_checksum = document.get("sha256")
        path = download_document(document, output_root, force)
        downloaded_paths.append(path)
        changed = changed or document.get("sha256") != previous_checksum
    if changed:
        save_manifest(manifest_path, documents)
    return downloaded_paths


def main() -> int:
    args = build_parser().parse_args()
    try:
        paths = download_manuals(args.manifest, args.output_root, args.force)
    except DownloadError as error:
        print(f"Download failed: {error}", file=sys.stderr)
        return 1

    print(f"Downloaded or verified {len(paths)} manual(s) in {args.output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())