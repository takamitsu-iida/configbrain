"""Run resumable Cloud Vision OCR for image candidates in a manifest."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__:
    from .cloud_vision import (
        CloudVisionClient,
        CloudVisionError,
        load_config,
        save_result,
        sha256_file,
    )
else:
    from cloud_vision import (
        CloudVisionClient,
        CloudVisionError,
        load_config,
        save_result,
        sha256_file,
    )


class OCRBatchError(RuntimeError):
    """Raised when an OCR candidate batch cannot be processed safely."""


MARKER_IMAGE_PATTERN = re.compile(
    r"^_page_(?P<page_index>\d+)_(?P<kind>[A-Za-z]+)_(?P<item_index>\d+)"
    r"\.(?:jpe?g|png)$",
    re.IGNORECASE,
)


def load_image_candidates(manifest_path: Path) -> list[Path]:
    if not manifest_path.is_file():
        raise OCRBatchError(f"Candidate manifest does not exist: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OCRBatchError(f"Could not read candidate manifest: {manifest_path}") from error

    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise OCRBatchError("Candidate manifest has an unsupported schema version")
    images = manifest.get("images")
    if not isinstance(images, list):
        raise OCRBatchError("Candidate manifest images must be an array")

    candidates: list[Path] = []
    seen_paths: set[Path] = set()
    for index, image in enumerate(images):
        if not isinstance(image, dict) or not isinstance(image.get("path"), str):
            raise OCRBatchError(
                f"Candidate manifest image {index} must contain a string path"
            )
        path = Path(image["path"])
        if path in seen_paths:
            raise OCRBatchError(f"Candidate manifest contains a duplicate image: {path}")
        seen_paths.add(path)
        candidates.append(path)
    return candidates


def output_path_for_image(image_path: Path, output_dir: Path) -> Path:
    match = MARKER_IMAGE_PATTERN.match(image_path.name)
    if match is None:
        raise OCRBatchError(f"Candidate image has an unsupported name: {image_path}")
    return output_dir / (
        f"page-{match.group('page_index')}-"
        f"{match.group('kind').casefold()}-{match.group('item_index')}.json"
    )


def has_matching_result(image_path: Path, output_path: Path) -> bool:
    if not output_path.is_file():
        return False
    try:
        result = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OCRBatchError(f"Could not read existing OCR result: {output_path}") from error

    if not isinstance(result, dict):
        raise OCRBatchError(f"Existing OCR result must be an object: {output_path}")
    source = result.get("source")
    request = result.get("request")
    if not isinstance(source, dict) or not isinstance(request, dict):
        raise OCRBatchError(f"Existing OCR result has an invalid structure: {output_path}")
    if request.get("type") != "image" or request.get("pages") is not None:
        raise OCRBatchError(f"Existing OCR result is not an image result: {output_path}")

    try:
        matching_source = (
            source.get("path") == str(image_path)
            and source.get("size_bytes") == image_path.stat().st_size
            and source.get("sha256") == sha256_file(image_path)
        )
    except OSError as error:
        raise OCRBatchError(f"Could not inspect candidate image: {image_path}") from error
    if not matching_source:
        raise OCRBatchError(
            f"Existing OCR result does not match candidate image: {output_path}"
        )
    return True


def run_batch(
    manifest_path: Path,
    output_dir: Path,
    client: CloudVisionClient,
    force: bool = False,
) -> tuple[int, int]:
    completed = 0
    skipped = 0
    candidates = load_image_candidates(manifest_path)
    output_paths: set[Path] = set()
    for image_path in candidates:
        output_path = output_path_for_image(image_path, output_dir)
        if output_path in output_paths:
            raise OCRBatchError(
                f"Candidate manifest produces duplicate OCR output: {output_path}"
            )
        output_paths.add(output_path)
        if not force and has_matching_result(image_path, output_path):
            skipped += 1
            print(f"Skipped completed OCR result: {output_path}")
            continue
        try:
            result = client.annotate_image(image_path)
            save_result(result, output_path, force=force)
        except CloudVisionError as error:
            raise OCRBatchError(f"OCR failed for {image_path}: {error}") from error
        completed += 1
        print(f"Saved Cloud Vision OCR result to {output_path}")
    return completed, skipped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run resumable image OCR for a candidate manifest."
    )
    parser.add_argument("manifest", type=Path, help="Path to candidates.json.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Path to the environment file (default: .env).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Request timeout in seconds (default: 180).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run OCR and overwrite completed results.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        client = CloudVisionClient(load_config(args.env_file), timeout=args.timeout)
        completed, skipped = run_batch(
            args.manifest,
            args.output_dir,
            client,
            force=args.force,
        )
    except (CloudVisionError, OCRBatchError) as error:
        print(f"Cloud Vision OCR batch failed: {error}", file=sys.stderr)
        return 1

    print(f"Cloud Vision OCR batch completed: {completed} saved, {skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
