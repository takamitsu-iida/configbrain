from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

from scripts.discover_manuals import DiscoveryError, validate_catalog_metadata


class DownloadError(RuntimeError):
    pass


def validate_pdf_url(url: str) -> None:
    parsed = urlsplit(url)
    hostname = parsed.hostname

    if parsed.scheme != "https" or hostname != "www.cisco.com":
        raise ValueError(f"PDF URL must use https://www.cisco.com: {url}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("PDF URL must not contain credentials.")
    if parsed.port is not None:
        raise ValueError("PDF URL must not specify a port.")
    if not parsed.path.casefold().endswith(".pdf"):
        raise ValueError(f"Catalog URL is not a PDF: {url}")


def load_catalog_documents(catalog_path: Path) -> list[dict[str, str]]:
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DownloadError(f"Could not read catalog: {catalog_path}") from error

    if not isinstance(catalog, dict):
        raise DownloadError("Unsupported or invalid catalog.")
    try:
        validate_catalog_metadata(catalog)
    except DiscoveryError as error:
        raise DownloadError(str(error)) from error
    documents = catalog.get("documents")
    if not isinstance(documents, list) or not documents:
        raise DownloadError("Catalog must contain at least one document.")

    validated: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            raise DownloadError("Catalog entries must be objects.")
        document_id = document.get("document_id")
        url = document.get("url")
        if not isinstance(document_id, str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", document_id
        ):
            raise DownloadError("Catalog contains an invalid document_id.")
        if document_id in seen_ids:
            raise DownloadError(f"Catalog contains duplicate document_id: {document_id}")
        if not isinstance(url, str):
            raise DownloadError(f"Catalog entry {document_id} has no URL.")
        try:
            validate_pdf_url(url)
        except ValueError as error:
            raise DownloadError(f"Catalog entry {document_id} has an invalid URL.") from error
        filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        if not filename or Path(filename).name != filename:
            raise DownloadError(f"Catalog entry {document_id} has an invalid filename.")
        seen_ids.add(document_id)
        validated.append(
            {"document_id": document_id, "filename": filename, "url": url}
        )
    return validated


def fetch_remote_metadata(url: str) -> dict[str, str | int | None]:
    request = Request(url, method="HEAD", headers={"User-Agent": "Wget/1.21.4"})
    try:
        with urlopen(request, timeout=60) as response:
            final_url = response.geturl()
            validate_pdf_url(final_url)
            if final_url != url:
                raise DownloadError(f"Unexpected redirect while checking {url}: {final_url}")
            headers = response.headers
            raw_length = headers.get("Content-Length")
            try:
                content_length = int(raw_length) if raw_length is not None else None
            except ValueError as error:
                raise DownloadError(f"Invalid Content-Length for {url}.") from error
            return {
                "etag": headers.get("ETag"),
                "last_modified": headers.get("Last-Modified"),
                "content_length": content_length,
            }
    except HTTPError as error:
        raise DownloadError(f"HTTP {error.code} while checking {url}.") from error
    except URLError as error:
        raise DownloadError(f"Network error while checking {url}: {error.reason}") from error
    except OSError as error:
        raise DownloadError(f"Could not check {url}: {error}") from error


def build_wget_command(url: str, output_path: Path) -> list[str]:
    return [
        "wget",
        "--no-verbose",
        "--tries=3",
        "--timeout=30",
        "--https-only",
        "--max-redirect=0",
        "--output-document",
        str(output_path),
        url,
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_download_state(state_path: Path) -> dict[str, object] | None:
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DownloadError(f"Could not read download state: {state_path}") from error
    if not isinstance(state, dict):
        raise DownloadError(f"Invalid download state: {state_path}")
    return state


def is_current(
    pdf_path: Path,
    state_path: Path,
    url: str,
    remote: dict[str, str | int | None],
) -> bool:
    state = load_download_state(state_path)
    if state is None or not pdf_path.is_file() or state.get("url") != url:
        return False
    if remote["etag"] is not None:
        if state.get("etag") != remote["etag"]:
            return False
    elif remote["last_modified"] is not None:
        if state.get("last_modified") != remote["last_modified"]:
            return False
    else:
        return False
    if (
        remote["content_length"] is not None
        and state.get("size") != remote["content_length"]
    ):
        return False
    if pdf_path.stat().st_size != state.get("size"):
        return False
    with pdf_path.open("rb") as current_pdf:
        if current_pdf.read(5) != b"%PDF-":
            return False
    return state.get("sha256") == sha256_file(pdf_path)


def save_download_state(state: dict[str, object], state_path: Path) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=state_path.parent,
            prefix=f".{state_path.name}.",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(state, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
        os.replace(temporary_path, state_path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise DownloadError(f"Could not save download state: {state_path}") from error


def download_document(
    document: dict[str, str],
    destination: Path,
) -> str:
    document_destination = destination / document["document_id"]
    document_destination.mkdir(parents=True, exist_ok=True)
    pdf_path = document_destination / document["filename"]
    state_path = document_destination / ".download-state.json"
    url = document["url"]
    remote = fetch_remote_metadata(url)
    if is_current(pdf_path, state_path, url, remote):
        return "unchanged"

    had_previous_state = state_path.is_file() and pdf_path.is_file()
    temporary_path: Path | None = None
    temporary_state_path: Path | None = None
    backup_path: Path | None = None
    replaced_pdf = False
    try:
        with tempfile.NamedTemporaryFile(
            dir=document_destination,
            prefix=f".{pdf_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        command = build_wget_command(url, temporary_path)
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as error:
            raise DownloadError("wget was not found; install wget and retry.") from error
        except OSError as error:
            raise DownloadError(f"Could not run wget: {error}") from error
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            suffix = f" {detail}" if detail else ""
            raise DownloadError(
                f"wget failed with exit code {result.returncode} for "
                f"{document['document_id']}.{suffix}"
            )

        size = temporary_path.stat().st_size
        with temporary_path.open("rb") as downloaded_pdf:
            if downloaded_pdf.read(5) != b"%PDF-":
                raise DownloadError(
                    f"Downloaded file is not a PDF: {document['document_id']}"
                )
        if remote["content_length"] is not None and size != remote["content_length"]:
            raise DownloadError(
                f"Downloaded size does not match Content-Length for "
                f"{document['document_id']}."
            )

        state = {
            "url": url,
            **remote,
            "size": size,
            "sha256": sha256_file(temporary_path),
        }
        with tempfile.NamedTemporaryFile(
            dir=document_destination, prefix=".download-state.", suffix=".tmp", delete=False,
        ) as temporary_state:
            temporary_state_path = Path(temporary_state.name)
        save_download_state(state, temporary_state_path)
        if pdf_path.is_file():
            with tempfile.NamedTemporaryFile(
                dir=document_destination, prefix=f".{pdf_path.name}.", suffix=".backup",
                delete=False,
            ) as backup:
                backup_path = Path(backup.name)
            shutil.copyfile(pdf_path, backup_path)
        os.replace(temporary_path, pdf_path)
        temporary_path = None
        replaced_pdf = True
        os.replace(temporary_state_path, state_path)
        temporary_state_path = None
        return "updated" if had_previous_state else "downloaded"
    except OSError as error:
        if replaced_pdf:
            try:
                if backup_path is not None:
                    os.replace(backup_path, pdf_path)
                    backup_path = None
                else:
                    pdf_path.unlink()
            except OSError as restore_error:
                recovery = (
                    f"Previous PDF backup remains at {backup_path}."
                    if backup_path is not None
                    else f"No previous PDF existed; the new PDF remains at {pdf_path}."
                )
                backup_path = None
                raise DownloadError(
                    f"Could not save download state ({error}) or restore {pdf_path}; "
                    f"{recovery} Recovery error: {restore_error}"
                ) from restore_error
        raise DownloadError(
            f"Could not store PDF and download state for {document['document_id']}: {error}"
        ) from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if temporary_state_path is not None:
            temporary_state_path.unlink(missing_ok=True)
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)


def download_manuals(
    catalog_path: Path, destination: Path, *, document_ids: list[str] | None = None,
) -> dict[str, int]:
    documents = load_catalog_documents(catalog_path)
    if document_ids is not None:
        if not document_ids:
            raise DownloadError("At least one document ID must be specified.")
        selected_ids = set(document_ids)
        unknown = selected_ids - {document["document_id"] for document in documents}
        if unknown:
            raise DownloadError(f"Unknown catalog document ID(s): {', '.join(sorted(unknown))}")
        documents = [document for document in documents if document["document_id"] in selected_ids]
    summary = {"downloaded": 0, "updated": 0, "unchanged": 0}
    for document in documents:
        result = download_document(document, destination)
        summary[result] += 1
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download Cisco manuals listed in a generated catalog."
    )
    parser.add_argument(
        "--document-id", dest="document_ids", action="append",
        help="Download only this catalog document (repeatable; default: all documents).",
    )
    parser.add_argument(
        "--catalog",
        required=True,
        type=Path,
        help="Catalog JSON created by scripts/discover_manuals.py.",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("data/source_manuals"),
        help="Directory for downloaded PDFs (default: data/source_manuals).",
    )
    args = parser.parse_args(argv)

    try:
        summary = download_manuals(
            args.catalog, args.destination, document_ids=args.document_ids,
        )
    except (DownloadError, ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(
        f"Manual downloads checked: {sum(summary.values())}; "
        f"{summary['downloaded']} downloaded, {summary['updated']} updated, "
        f"{summary['unchanged']} unchanged."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
