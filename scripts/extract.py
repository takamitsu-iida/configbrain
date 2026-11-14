"""Extract raw Markdown from a PDF by invoking marker-pdf."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pypdfium2


class ExtractionError(RuntimeError):
    """Raised when raw Markdown extraction cannot be completed."""


PROXY_ENVIRONMENT_VARIABLES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
DEFAULT_PAGE_BATCH_SIZE = 50


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract raw Markdown from a PDF with marker-pdf."
    )
    parser.add_argument("input_pdf", type=Path, help="Path to the source PDF.")
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory where marker-pdf writes raw Markdown.",
    )
    parser.add_argument(
        "--command",
        default="marker_single",
        help="Extraction command available on PATH (default: marker_single).",
    )
    ocr_group = parser.add_mutually_exclusive_group()
    ocr_group.add_argument(
        "--enable-ocr",
        dest="disable_ocr",
        action="store_false",
        help="Enable Marker OCR (disabled by default).",
    )
    ocr_group.add_argument(
        "--disable-ocr",
        dest="disable_ocr",
        action="store_true",
        help="Disable Marker OCR and use only the PDF text layer (default).",
    )
    parser.set_defaults(disable_ocr=True)
    parser.add_argument(
        "--page-batch-size",
        type=int,
        default=DEFAULT_PAGE_BATCH_SIZE,
        help=(
            "Maximum pages per marker-pdf invocation "
            f"(default: {DEFAULT_PAGE_BATCH_SIZE})."
        ),
    )
    return parser


def validate_input(input_pdf: Path) -> None:
    if not input_pdf.is_file():
        raise ExtractionError(f"Input PDF does not exist: {input_pdf}")
    if input_pdf.suffix.lower() != ".pdf":
        raise ExtractionError(f"Input file is not a PDF: {input_pdf}")


def pdf_page_count(input_pdf: Path) -> int:
    try:
        document = pypdfium2.PdfDocument(input_pdf)
        try:
            return len(document)
        finally:
            document.close()
    except Exception as error:
        raise ExtractionError(f"Could not read PDF page count: {input_pdf}") from error


def marker_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for variable in PROXY_ENVIRONMENT_VARIABLES:
        environment.pop(variable, None)
    environment.setdefault("HF_HUB_DISABLE_XET", "1")
    environment["NO_PROXY"] = "127.0.0.1,localhost"
    environment["no_proxy"] = "127.0.0.1,localhost"
    return environment


def run_marker(
    marker_command: list[str],
    environment: dict[str, str],
    stream_output: bool,
) -> None:
    if stream_output:
        process = subprocess.Popen(
            marker_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=environment,
        )
        if process.stdout is None:
            raise ExtractionError("Could not capture marker-pdf output")
        output_parts: list[str] = []
        for line in process.stdout:
            output_parts.append(line)
            print(line, end="", flush=True)
        returncode = process.wait()
        details = "".join(output_parts).strip()
    else:
        completed = subprocess.run(
            marker_command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        returncode = completed.returncode
        details = completed.stderr.strip() or completed.stdout.strip()
    if returncode != 0:
        message = f"marker-pdf failed with exit code {returncode}"
        if details:
            message = f"{message}: {details}"
        raise ExtractionError(message)


def merge_metadata(metadata_files: list[Path]) -> dict[str, Any]:
    merged: dict[str, Any] = {"table_of_contents": [], "page_stats": []}
    for metadata_file in metadata_files:
        try:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ExtractionError(
                f"Could not read marker-pdf metadata: {metadata_file}"
            ) from error
        if not isinstance(metadata, dict):
            raise ExtractionError(f"Marker metadata is not an object: {metadata_file}")
        for field in ("table_of_contents", "page_stats"):
            value = metadata.get(field, [])
            if not isinstance(value, list):
                raise ExtractionError(
                    f"Marker metadata field {field} is not a list: {metadata_file}"
                )
            merged[field].extend(value)
        for key, value in metadata.items():
            if key not in {"table_of_contents", "page_stats"}:
                merged.setdefault(key, value)
    return merged


def merge_marker_batches(batch_roots: list[Path], destination: Path, stem: str) -> None:
    destination.mkdir(parents=True)
    markdown_parts: list[str] = []
    metadata_files: list[Path] = []
    for batch_root in batch_roots:
        batch_output = batch_root / stem
        markdown_file = batch_output / f"{stem}.md"
        metadata_file = batch_output / f"{stem}_meta.json"
        if not markdown_file.is_file() or markdown_file.stat().st_size == 0:
            raise ExtractionError(
                f"Marker batch did not produce non-empty Markdown: {markdown_file}"
            )
        if not metadata_file.is_file():
            raise ExtractionError(
                f"Marker batch did not produce metadata: {metadata_file}"
            )
        markdown_parts.append(markdown_file.read_text(encoding="utf-8").rstrip())
        metadata_files.append(metadata_file)
        for asset in batch_output.iterdir():
            if asset in {markdown_file, metadata_file}:
                continue
            target = destination / asset.name
            if target.exists():
                raise ExtractionError(
                    f"Marker batches produced duplicate asset: {asset.name}"
                )
            if asset.is_dir():
                shutil.copytree(asset, target)
            else:
                shutil.copy2(asset, target)

    (destination / f"{stem}.md").write_text(
        "\n\n".join(markdown_parts) + "\n", encoding="utf-8"
    )
    (destination / f"{stem}_meta.json").write_text(
        json.dumps(merge_metadata(metadata_files), indent=2) + "\n",
        encoding="utf-8",
    )


def extract_markdown(
    input_pdf: Path,
    output_dir: Path,
    command: str = "marker_single",
    disable_ocr: bool = False,
    stream_output: bool = False,
    page_batch_size: int = DEFAULT_PAGE_BATCH_SIZE,
) -> list[Path]:
    """Run the extractor and return the generated non-empty Markdown files."""
    validate_input(input_pdf)

    executable = shutil.which(command)
    if executable is None:
        raise ExtractionError(f"Extraction command was not found on PATH: {command}")

    if output_dir.exists() and any(output_dir.iterdir()):
        raise ExtractionError(f"Output directory is not empty: {output_dir}")
    if page_batch_size < 1:
        raise ExtractionError("Page batch size must be greater than zero")
    output_dir.mkdir(parents=True, exist_ok=True)

    base_command = [executable, str(input_pdf)]
    extraction_options = ["--disable_ocr"] if disable_ocr else []

    page_count = pdf_page_count(input_pdf)
    environment = marker_environment()
    if page_count <= page_batch_size:
        run_marker(
            [*base_command, "--output_dir", str(output_dir), *extraction_options],
            environment,
            stream_output,
        )
    else:
        stem = input_pdf.stem
        with tempfile.TemporaryDirectory(
            prefix=f".{stem}-marker-", dir=output_dir.parent
        ) as temporary_directory:
            temporary_root = Path(temporary_directory)
            batch_roots: list[Path] = []
            for batch_number, start in enumerate(
                range(0, page_count, page_batch_size), start=1
            ):
                end = min(start + page_batch_size, page_count) - 1
                batch_root = temporary_root / f"batch-{batch_number:04d}"
                batch_roots.append(batch_root)
                print(
                    f"Extracting pages {start + 1}-{end + 1} of {page_count}",
                    flush=True,
                )
                run_marker(
                    [
                        *base_command,
                        "--output_dir",
                        str(batch_root),
                        "--page_range",
                        f"{start}-{end}",
                        "--disable_multiprocessing",
                        *extraction_options,
                    ],
                    environment,
                    stream_output,
                )
            merged_output = temporary_root / "merged" / stem
            merge_marker_batches(batch_roots, merged_output, stem)
            shutil.move(str(merged_output), str(output_dir / stem))

    markdown_files = sorted(
        path
        for path in output_dir.rglob("*.md")
        if path.is_file() and path.stat().st_size > 0
    )
    if not markdown_files:
        raise ExtractionError(f"No non-empty Markdown files were generated: {output_dir}")
    return markdown_files


def main() -> int:
    args = build_parser().parse_args()
    try:
        markdown_files = extract_markdown(
            args.input_pdf,
            args.output_dir,
            args.command,
            args.disable_ocr,
            page_batch_size=args.page_batch_size,
        )
    except ExtractionError as error:
        print(f"Extraction failed: {error}", file=sys.stderr)
        return 1

    print(f"Extracted {len(markdown_files)} Markdown file(s) to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())