"""Integrate Cloud Vision image OCR text into Marker Markdown output."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__:
    from .cloud_vision import sha256_file
    from .ocr_batch import OCRBatchError, load_image_candidates, output_path_for_image
else:
    from cloud_vision import sha256_file
    from ocr_batch import OCRBatchError, load_image_candidates, output_path_for_image


class OCRIntegrationError(RuntimeError):
    """Raised when OCR text cannot be safely integrated into Markdown."""


MARKDOWN_IMAGE_PATTERN = re.compile(
    r"!\[[^\]]*]\((?P<destination><[^>]+>|[^)\s]+)(?:\s+[^)]*)?\)"
)


def load_ocr_text(image_path: Path, result_path: Path) -> str:
    if not result_path.is_file():
        raise OCRIntegrationError(f"OCR result does not exist: {result_path}")
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OCRIntegrationError(f"Could not read OCR result: {result_path}") from error

    if not isinstance(result, dict):
        raise OCRIntegrationError(f"OCR result must be an object: {result_path}")
    source = result.get("source")
    request = result.get("request")
    response = result.get("response")
    if not isinstance(source, dict) or not isinstance(request, dict) or not isinstance(
        response, dict
    ):
        raise OCRIntegrationError(f"OCR result has an invalid structure: {result_path}")
    if request.get("type") != "image" or request.get("pages") is not None:
        raise OCRIntegrationError(f"OCR result is not an image result: {result_path}")

    try:
        matching_source = (
            source.get("path") == str(image_path)
            and source.get("size_bytes") == image_path.stat().st_size
            and source.get("sha256") == sha256_file(image_path)
        )
    except OSError as error:
        raise OCRIntegrationError(f"Could not inspect candidate image: {image_path}") from error
    if not matching_source:
        raise OCRIntegrationError(
            f"OCR result does not match candidate image: {result_path}"
        )

    responses = response.get("responses")
    if not isinstance(responses, list) or len(responses) != 1:
        raise OCRIntegrationError(f"OCR result has an invalid responses array: {result_path}")
    annotation = responses[0].get("fullTextAnnotation") if isinstance(responses[0], dict) else None
    text = annotation.get("text") if isinstance(annotation, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise OCRIntegrationError(f"OCR result has no text annotation: {result_path}")
    return text.rstrip()


def fenced_text(text: str) -> str:
    longest_backtick_run = max(
        (len(match.group()) for match in re.finditer(r"`+", text)),
        default=0,
    )
    fence = "`" * max(3, longest_backtick_run + 1)
    return f"{fence}text\n{text}\n{fence}"


def ocr_details(image_reference: str, text: str) -> str:
    return (
        "\n\n<details>\n"
        f"<summary>Cloud Vision OCR text for {image_reference}</summary>\n\n"
        f"{fenced_text(text)}\n"
        "</details>"
    )


def integrate_ocr(
    markdown_path: Path,
    manifest_path: Path,
    ocr_dir: Path,
) -> tuple[str, int]:
    if not markdown_path.is_file():
        raise OCRIntegrationError(f"Input Markdown does not exist: {markdown_path}")
    try:
        markdown = markdown_path.read_text(encoding="utf-8")
    except OSError as error:
        raise OCRIntegrationError(f"Could not read input Markdown: {markdown_path}") from error

    try:
        candidates = load_image_candidates(manifest_path)
    except OCRBatchError as error:
        raise OCRIntegrationError(str(error)) from error

    ocr_by_image = {
        image_path.resolve(): load_ocr_text(
            image_path, output_path_for_image(image_path, ocr_dir)
        )
        for image_path in candidates
    }
    matched_images: set[Path] = set()

    def replace_image(match: re.Match[str]) -> str:
        destination = match.group("destination").strip("<>")
        referenced_image = (markdown_path.parent / destination).resolve()
        text = ocr_by_image.get(referenced_image)
        if text is None:
            return match.group()
        matched_images.add(referenced_image)
        return match.group() + ocr_details(destination, text)

    integrated = MARKDOWN_IMAGE_PATTERN.sub(replace_image, markdown)
    missing_images = set(ocr_by_image).difference(matched_images)
    if missing_images:
        paths = ", ".join(str(path) for path in sorted(missing_images))
        raise OCRIntegrationError(f"Candidate images are not referenced by Markdown: {paths}")
    return integrated, len(matched_images)


def save_markdown(markdown: str, output_path: Path, force: bool = False) -> None:
    if output_path.exists() and not force:
        raise OCRIntegrationError(f"Output file already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(markdown)
        os.replace(temporary_path, output_path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise OCRIntegrationError(f"Could not save integrated Markdown: {output_path}") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Insert Cloud Vision OCR text after matching Markdown image references."
    )
    parser.add_argument("input", type=Path, help="Path to Marker Markdown.")
    parser.add_argument("--manifest", required=True, type=Path, help="Path to candidates.json.")
    parser.add_argument("--ocr-dir", required=True, type=Path, help="Directory containing OCR JSON.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        markdown, integrated_count = integrate_ocr(args.input, args.manifest, args.ocr_dir)
        save_markdown(markdown, args.output, args.force)
    except OCRIntegrationError as error:
        print(f"OCR integration failed: {error}", file=sys.stderr)
        return 1

    print(f"Saved Markdown with OCR text for {integrated_count} image(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
