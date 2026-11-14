import hashlib
import json
from pathlib import Path

import pytest

from scripts.validate_packages import PackageValidationError, validate_package


def write_package(tmp_path: Path, manual: str) -> Path:
    package_dir = tmp_path / "guide"
    images_dir = package_dir / "images"
    images_dir.mkdir(parents=True)
    (images_dir / "diagram.jpeg").write_bytes(b"jpeg")
    (package_dir / "manual.md").write_text(manual, encoding="utf-8")
    resources = []
    for path in (package_dir / "manual.md", images_dir / "diagram.jpeg"):
        relative = path.relative_to(package_dir).as_posix()
        resources.append(
            {
                "name": path.stem,
                "path": relative,
                "mediatype": "text/markdown" if path.suffix == ".md" else "image/jpeg",
                "bytes": path.stat().st_size,
                "hash": f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
            }
        )
    (package_dir / "datapackage.json").write_text(
        json.dumps(
            {
                "profile": "data-package",
                "name": "guide",
                "title": "Guide",
                "version": "26",
                "resources": resources,
            }
        ),
        encoding="utf-8",
    )
    return package_dir


def test_validate_package_checks_resources_images_and_anchors(tmp_path: Path) -> None:
    package_dir = write_package(
        tmp_path,
        '<span id="dhcp"></span>\n\n[DHCP](#dhcp)\n\n![](images/diagram.jpeg)\n',
    )

    assert validate_package(package_dir) == {
        "document_id": "guide",
        "resources": 2,
        "images": 1,
        "internal_links": 1,
    }


def test_validate_package_rejects_missing_internal_anchor(tmp_path: Path) -> None:
    package_dir = write_package(tmp_path, "[Missing](#missing)\n\n![](images/diagram.jpeg)\n")

    with pytest.raises(PackageValidationError, match="without anchors"):
        validate_package(package_dir)
