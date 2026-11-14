"""Build a validated Frictionless Data Package from transformed Markdown."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from frictionless import FrictionlessException, Package


class PackagingError(RuntimeError):
    """Raised when a transformed document cannot be packaged safely."""


FRONTMATTER_PATTERN = re.compile(
    r"\A---\n(?P<metadata>.*?)\n---\n\n(?P<body>.*)\Z",
    re.DOTALL,
)
IMAGE_PATTERN = re.compile(
    r"!\[(?P<alt>[^\]]*)]\((?P<destination><[^>]+>|[^)\s]+)(?P<suffix>\s+[^)]*)?\)"
)
INTERNAL_LINK_PATTERN = re.compile(
    r"(?<!!)\[(?P<label>[^\]]*)]\(#(?P<target>[^)\s]+)\)"
)
ANCHOR_PATTERN = re.compile(
    r"<span\s+id=[\"'](?P<anchor>[^\"']+)[\"']\s*></span>"
)
PACKAGE_NAME_PATTERN = re.compile(r"^[a-z0-9._-]+$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise PackagingError(f"Could not calculate checksum: {path}") from error
    return digest.hexdigest()


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PackagingError(f"Could not read {label}: {path}") from error
    if not isinstance(value, dict):
        raise PackagingError(f"{label} must be a JSON object: {path}")
    return value


def load_manual(
    manuals_path: Path, document_id: str
) -> tuple[dict[str, Any], str | None, str | None]:
    try:
        manifest = json.loads(manuals_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PackagingError(f"Could not read manuals manifest: {manuals_path}") from error
    manuals = manifest.get("documents") if isinstance(manifest, dict) else manifest
    if not isinstance(manuals, list):
        raise PackagingError("Manuals manifest must be a JSON array or catalog object")
    matches = [
        manual
        for manual in manuals
        if isinstance(manual, dict) and manual.get("document_id") == document_id
    ]
    if len(matches) != 1:
        raise PackagingError(
            f"Expected exactly one manuals entry for document_id: {document_id}"
        )
    product = manifest.get("product") if isinstance(manifest, dict) else None
    release = manifest.get("release") if isinstance(manifest, dict) else None
    return (
        matches[0],
        product if isinstance(product, str) and product.strip() else None,
        release if isinstance(release, str) and release.strip() else None,
    )


def parse_chunk(path: Path, expected_sha256: str) -> str:
    if not path.is_file():
        raise PackagingError(f"Transformed chunk does not exist: {path}")
    if sha256_file(path) != expected_sha256:
        raise PackagingError(f"Transformed chunk checksum mismatch: {path}")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PackagingError(f"Could not read transformed chunk: {path}") from error
    match = FRONTMATTER_PATTERN.match(content)
    if match is None:
        raise PackagingError(f"Transformed chunk has invalid frontmatter: {path}")
    try:
        metadata = json.loads(match.group("metadata"))
    except json.JSONDecodeError as error:
        raise PackagingError(f"Transformed chunk has invalid metadata: {path}") from error
    if not isinstance(metadata, dict):
        raise PackagingError(f"Chunk metadata must be an object: {path}")
    body = match.group("body").strip()
    if not body:
        raise PackagingError(f"Transformed chunk body is empty: {path}")
    return body


def render_frontmatter(metadata: dict[str, str]) -> str:
    return "---\n" + json.dumps(metadata, ensure_ascii=False, indent=2) + "\n---\n\n"


def rewrite_and_collect_images(markdown: str) -> tuple[str, list[str]]:
    images: list[str] = []

    def replace(match: re.Match[str]) -> str:
        raw_destination = match.group("destination")
        destination = raw_destination.strip("<>")
        parsed = PurePosixPath(destination)
        if (
            not destination
            or destination.startswith(("/", "\\"))
            or parsed.is_absolute()
            or ".." in parsed.parts
            or len(parsed.parts) != 1
            or "://" in destination
        ):
            raise PackagingError(f"Unsupported Markdown image destination: {destination}")
        if destination not in images:
            images.append(destination)
        rewritten = f"images/{destination}"
        if raw_destination.startswith("<"):
            rewritten = f"<{rewritten}>"
        return (
            f"![{match.group('alt')}]({rewritten}"
            f"{match.group('suffix') or ''})"
        )

    return IMAGE_PATTERN.sub(replace, markdown), images


def remove_unresolved_internal_links(markdown: str) -> tuple[str, list[str]]:
    anchors = set(ANCHOR_PATTERN.findall(markdown))
    removed: list[str] = []

    def replace(match: re.Match[str]) -> str:
        target = match.group("target")
        if target in anchors:
            return match.group()
        if target not in removed:
            removed.append(target)
        return match.group("label")

    cleaned = INTERNAL_LINK_PATTERN.sub(replace, markdown)
    remaining_targets = set(INTERNAL_LINK_PATTERN.findall(cleaned))
    if any(target not in anchors for _, target in remaining_targets):
        raise PackagingError("Package contains unresolved internal links")
    return cleaned, removed


def resource_descriptor(name: str, relative_path: str, path: Path) -> dict[str, Any]:
    mediatype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "name": name,
        "path": relative_path,
        "mediatype": mediatype,
        "bytes": path.stat().st_size,
        "hash": f"sha256:{sha256_file(path)}",
    }


def promote_package(temporary_dir: Path, output_dir: Path) -> None:
    try:
        os.replace(temporary_dir, output_dir)
        return
    except PermissionError:
        pass

    try:
        shutil.copytree(temporary_dir, output_dir)
    except OSError:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        raise


def build_package(
    transformed_dir: Path,
    assets_dir: Path,
    manuals_path: Path,
    document_id: str,
    output_dir: Path,
) -> None:
    transformed_dir = transformed_dir.resolve()
    assets_dir = assets_dir.resolve()
    manuals_path = manuals_path.resolve()
    output_dir = output_dir.resolve()
    if not PACKAGE_NAME_PATTERN.fullmatch(document_id):
        raise PackagingError(
            "document_id must contain only lowercase letters, digits, dot, underscore, or hyphen"
        )
    if output_dir.exists():
        raise PackagingError(f"Output directory already exists: {output_dir}")

    manifest_path = transformed_dir / "manifest.json"
    manifest = load_json_object(manifest_path, "transformation manifest")
    if manifest.get("complete") is not True:
        raise PackagingError("Transformation manifest is not complete")
    metadata = manifest.get("document_metadata")
    chunks = manifest.get("chunks")
    if not isinstance(metadata, dict) or not all(
        isinstance(metadata.get(key), str)
        for key in ("title", "description", "product", "version", "source")
    ):
        raise PackagingError("Transformation manifest has invalid document metadata")
    if not isinstance(chunks, list) or not chunks:
        raise PackagingError("Transformation manifest has no chunks")

    manual, catalog_product, catalog_release = load_manual(manuals_path, document_id)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    try:
        bodies: list[str] = []
        for entry in chunks:
            filename = entry.get("path") if isinstance(entry, dict) else None
            checksum = entry.get("output_sha256") if isinstance(entry, dict) else None
            if (
                not isinstance(filename, str)
                or Path(filename).name != filename
                or not isinstance(checksum, str)
            ):
                raise PackagingError("Transformation manifest has an invalid chunk entry")
            bodies.append(parse_chunk(transformed_dir / filename, checksum))

        combined = render_frontmatter(metadata) + "\n\n".join(bodies) + "\n"
        combined, image_names = rewrite_and_collect_images(combined)
        combined, removed_internal_links = remove_unresolved_internal_links(combined)
        manual_path = temporary_dir / "manual.md"
        manual_path.write_text(combined, encoding="utf-8")

        images_dir = temporary_dir / "images"
        resources = [resource_descriptor("manual", "manual.md", manual_path)]
        if image_names:
            images_dir.mkdir()
        for index, image_name in enumerate(image_names, start=1):
            source = assets_dir / image_name
            if not source.is_file():
                raise PackagingError(f"Referenced image does not exist: {source}")
            destination = images_dir / image_name
            shutil.copyfile(source, destination)
            resources.append(
                resource_descriptor(
                    f"image-{index:04d}",
                    f"images/{image_name}",
                    destination,
                )
            )

        source_url = manual.get("url")
        descriptor = {
            "profile": "data-package",
            "name": document_id,
            "title": metadata["title"],
            "description": metadata["description"],
            "version": metadata["version"],
            "homepage": source_url if isinstance(source_url, str) else "",
            "contributors": [
                {
                    "title": metadata["source"] or "Cisco",
                    "role": "author",
                }
            ],
            "sources": [
                {
                    "title": manual.get("title", metadata["title"]),
                    "path": source_url,
                }
            ],
            "configbrain": {
                "documentId": document_id,
                "product": metadata["product"],
                "catalogProduct": catalog_product or metadata["product"],
                "release": catalog_release or metadata["version"],
                "sourcePdf": {
                    "filename": manual.get("filename"),
                    "sha256": manual.get("sha256"),
                    "bytes": manual.get("size_bytes"),
                    "downloadedAt": manual.get("downloaded_at"),
                },
                "removedUnresolvedInternalLinks": len(removed_internal_links),
                "transformationManifestSha256": sha256_file(manifest_path),
            },
            "resources": resources,
        }
        package = Package(descriptor, basepath=str(temporary_dir))
        report = package.validate()
        if not report.valid:
            errors = report.flatten(["code", "message"])
            raise PackagingError(f"Frictionless validation failed: {errors}")
        package.to_json(str(temporary_dir / "datapackage.json"))
        promote_package(temporary_dir, output_dir)
    except (FrictionlessException, OSError, TypeError, ValueError) as error:
        raise PackagingError(
            f"Could not build data package {output_dir}: {error}"
        ) from error
    finally:
        if temporary_dir.exists():
            try:
                shutil.rmtree(temporary_dir)
            except PermissionError as error:
                if not output_dir.is_dir():
                    raise
                print(
                    f"Warning: could not remove temporary package directory "
                    f"{temporary_dir}: {error}",
                    file=sys.stderr,
                )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a validated Frictionless Data Package."
    )
    parser.add_argument("transformed_dir", type=Path)
    parser.add_argument("--assets-dir", required=True, type=Path)
    parser.add_argument("--manuals", type=Path, default=Path("data/manuals.json"))
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        build_package(
            args.transformed_dir,
            args.assets_dir,
            args.manuals,
            args.document_id,
            args.output_dir,
        )
    except PackagingError as error:
        print(f"Packaging failed: {error}", file=sys.stderr)
        return 1
    print(f"Saved validated data package to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
