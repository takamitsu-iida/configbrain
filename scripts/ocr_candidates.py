"""Select PDF pages and extracted images that are candidates for Cloud Vision OCR."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from PIL import Image, UnidentifiedImageError

class CandidateSelectionError(RuntimeError):
    """Raised when OCR candidates cannot be analyzed or saved."""


MARKER_IMAGE_PATTERN = re.compile(
    r"^_page_(?P<page_index>\d+)_(?P<kind>[A-Za-z]+)_(?P<item_index>\d+)\.(?:jpe?g|png)$",
    re.IGNORECASE,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_pdf_text_characters(pdf_path: Path) -> list[int]:
    if not pdf_path.is_file():
        raise CandidateSelectionError(f"Input PDF does not exist: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise CandidateSelectionError(f"Input file is not a PDF: {pdf_path}")

    try:
        document = pdfium.PdfDocument(pdf_path)
        counts: list[int] = []
        for page_index in range(len(document)):
            page = document[page_index]
            text_page = page.get_textpage()
            text = text_page.get_text_range()
            counts.append(sum(not character.isspace() for character in text))
            text_page.close()
            page.close()
        document.close()
        return counts
    except Exception as error:
        raise CandidateSelectionError(f"Could not analyze PDF text: {pdf_path}") from error


def select_sparse_pages(character_counts: list[int], min_text_chars: int) -> list[dict[str, int]]:
    if min_text_chars < 0:
        raise CandidateSelectionError("Minimum text characters cannot be negative")
    return [
        {"page": page_index + 1, "text_chars": text_chars}
        for page_index, text_chars in enumerate(character_counts)
        if text_chars < min_text_chars
    ]


def find_image_candidates(
    images_dir: Path,
    min_area: int,
    kinds: set[str],
) -> list[dict[str, Any]]:
    if not images_dir.is_dir():
        raise CandidateSelectionError(f"Images directory does not exist: {images_dir}")
    if min_area < 1:
        raise CandidateSelectionError("Minimum image area must be greater than zero")

    normalized_kinds = {kind.casefold() for kind in kinds}
    candidates: list[dict[str, Any]] = []
    for path in sorted(images_dir.iterdir()):
        match = MARKER_IMAGE_PATTERN.match(path.name)
        if not path.is_file() or match is None:
            continue
        kind = match.group("kind")
        if normalized_kinds and kind.casefold() not in normalized_kinds:
            continue
        try:
            with Image.open(path) as image:
                width, height = image.size
        except (OSError, UnidentifiedImageError) as error:
            raise CandidateSelectionError(f"Could not read extracted image: {path}") from error
        area = width * height
        if area < min_area:
            continue
        candidates.append(
            {
                "path": str(path),
                "page": int(match.group("page_index")) + 1,
                "kind": kind,
                "width": width,
                "height": height,
                "area": area,
            }
        )
    return candidates


def build_manifest(
    pdf_path: Path,
    images_dir: Path,
    min_text_chars: int = 50,
    min_image_area: int = 100_000,
    image_kinds: set[str] | None = None,
) -> dict[str, Any]:
    character_counts = count_pdf_text_characters(pdf_path)
    kinds = image_kinds if image_kinds is not None else {"Diagram"}
    return {
        "schema_version": 1,
        "source": {
            "path": str(pdf_path),
            "sha256": sha256_file(pdf_path),
            "pages": len(character_counts),
        },
        "selection": {
            "min_text_chars": min_text_chars,
            "min_image_area": min_image_area,
            "image_kinds": sorted(kinds),
        },
        "pdf_pages": select_sparse_pages(character_counts, min_text_chars),
        "images": find_image_candidates(images_dir, min_image_area, kinds),
    }


def save_manifest(manifest: dict[str, Any], output_path: Path, force: bool = False) -> None:
    if output_path.exists() and not force:
        raise CandidateSelectionError(f"Output file already exists: {output_path}")
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
            json.dump(manifest, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
        os.replace(temporary_path, output_path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise CandidateSelectionError(f"Could not save candidate manifest: {output_path}") from error


def parse_image_kinds(value: str) -> set[str]:
    kinds = {item.strip() for item in value.split(",") if item.strip()}
    if not kinds:
        raise argparse.ArgumentTypeError("At least one image kind is required")
    return kinds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select sparse PDF pages and Marker images for Cloud Vision OCR."
    )
    parser.add_argument("input_pdf", type=Path)
    parser.add_argument("--images-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--min-text-chars", type=int, default=50)
    parser.add_argument("--min-image-area", type=int, default=100_000)
    parser.add_argument("--image-kinds", type=parse_image_kinds, default={"Diagram"})
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        manifest = build_manifest(
            args.input_pdf,
            args.images_dir,
            args.min_text_chars,
            args.min_image_area,
            args.image_kinds,
        )
        save_manifest(manifest, args.output, args.force)
    except CandidateSelectionError as error:
        print(f"OCR candidate selection failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Selected {len(manifest['pdf_pages'])} PDF page(s) and "
        f"{len(manifest['images'])} image(s) for OCR"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())