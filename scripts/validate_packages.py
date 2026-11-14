"""Validate resources and Markdown references across generated data packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from frictionless import Package


class PackageValidationError(RuntimeError):
    """Raised when a generated package fails cross-package validation."""


IMAGE_PATTERN = re.compile(r"!\[[^\]]*]\((?:<)?(?P<path>images/[^)\s>]+)")
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]*]\((?:<)?(?P<target>#[^)\s>]+)")
ANCHOR_PATTERN = re.compile(r"<span\s+id=[\"'](?P<anchor>[^\"']+)[\"']\s*></span>")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise PackageValidationError(f"Could not read package resource: {path}") from error
    return digest.hexdigest()


def validate_package(package_dir: Path) -> dict[str, Any]:
    descriptor_path = package_dir / "datapackage.json"
    report = Package(str(descriptor_path)).validate()
    if not report.valid:
        raise PackageValidationError(
            f"Frictionless validation failed for {package_dir}: "
            f"{report.flatten(['code', 'message'])}"
        )
    try:
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        manual = (package_dir / "manual.md").read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as error:
        raise PackageValidationError(f"Could not read package: {package_dir}") from error
    resources = descriptor.get("resources")
    document_id = descriptor.get("name")
    if not isinstance(document_id, str) or not isinstance(resources, list):
        raise PackageValidationError(f"Package descriptor is invalid: {package_dir}")

    resource_paths: set[str] = set()
    for resource in resources:
        if not isinstance(resource, dict):
            raise PackageValidationError(f"Package resource is invalid: {package_dir}")
        path = resource.get("path")
        expected_hash = resource.get("hash")
        if not isinstance(path, str) or not isinstance(expected_hash, str):
            raise PackageValidationError(f"Package resource is missing path or hash: {package_dir}")
        if path in resource_paths:
            raise PackageValidationError(f"Package has duplicate resource path: {path}")
        resource_paths.add(path)
        if not expected_hash.startswith("sha256:"):
            raise PackageValidationError(f"Package resource hash is not SHA-256: {path}")
        if sha256_file(package_dir / path) != expected_hash.removeprefix("sha256:"):
            raise PackageValidationError(f"Package resource checksum mismatch: {path}")

    image_paths = set(IMAGE_PATTERN.findall(manual))
    if image_paths != {path for path in resource_paths if path.startswith("images/")}:
        raise PackageValidationError(f"Package image references do not match resources: {package_dir}")
    anchors = set(ANCHOR_PATTERN.findall(manual))
    links = {target.removeprefix("#") for target in LINK_PATTERN.findall(manual)}
    missing = links - anchors
    if missing:
        raise PackageValidationError(
            f"Package has internal links without anchors: {', '.join(sorted(missing))}"
        )
    return {
        "document_id": document_id,
        "resources": len(resources),
        "images": len(image_paths),
        "internal_links": len(links),
    }


def validate_packages(packages_dir: Path) -> list[dict[str, Any]]:
    package_dirs = sorted(path.parent for path in packages_dir.glob("*/datapackage.json"))
    if not package_dirs:
        raise PackageValidationError(f"No data packages found under: {packages_dir}")
    return [validate_package(package_dir) for package_dir in package_dirs]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated data packages.")
    parser.add_argument(
        "--packages-dir",
        type=Path,
        default=Path("data/products/catalyst9300/packages"),
    )
    args = parser.parse_args()
    try:
        result = validate_packages(args.packages_dir)
    except PackageValidationError as error:
        print(f"Package validation failed: {error}", file=sys.stderr)
        return 1
    print(f"Validated {len(result)} package(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
