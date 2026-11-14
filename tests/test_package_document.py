import hashlib
import json
from pathlib import Path

import pytest
from frictionless import Package

from scripts.package_document import (
    PackagingError,
    build_package,
    promote_package,
    remove_unresolved_internal_links,
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    transformed = tmp_path / "transformed"
    assets = tmp_path / "assets"
    transformed.mkdir()
    assets.mkdir()
    metadata = {
        "title": "Guide",
        "description": "Description",
        "product": "Catalyst",
        "version": "26",
        "source": "Cisco",
    }
    chunk = (
        "---\n"
        + json.dumps(metadata, indent=2)
        + "\n---\n\n# Guide\n\n![](_page_1_Picture_0.jpeg)\n"
    )
    (transformed / "chunk-0001.md").write_text(chunk, encoding="utf-8")
    (transformed / "manifest.json").write_text(
        json.dumps(
            {
                "complete": True,
                "document_metadata": metadata,
                "chunks": [
                    {
                        "path": "chunk-0001.md",
                        "output_sha256": sha256_text(chunk),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (assets / "_page_1_Picture_0.jpeg").write_bytes(b"jpeg")
    manuals = tmp_path / "manuals.json"
    manuals.write_text(
        json.dumps(
            [
                {
                    "document_id": "test-guide",
                    "title": "Guide",
                    "filename": "guide.pdf",
                    "url": "https://example.test/guide.pdf",
                    "sha256": "abc",
                    "size_bytes": 100,
                    "downloaded_at": "2026-01-01T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )
    return transformed, assets, manuals


def test_build_package_creates_valid_frictionless_package(tmp_path: Path) -> None:
    transformed, assets, manuals = write_fixture(tmp_path)
    output = tmp_path / "package"

    build_package(transformed, assets, manuals, "test-guide", output)

    descriptor = json.loads((output / "datapackage.json").read_text())
    assert descriptor["name"] == "test-guide"
    assert descriptor["configbrain"]["product"] == "Catalyst"
    assert descriptor["configbrain"]["catalogProduct"] == "Catalyst"
    assert descriptor["configbrain"]["release"] == "26"
    assert [resource["name"] for resource in descriptor["resources"]] == [
        "manual",
        "image-0001",
    ]
    manual = (output / "manual.md").read_text()
    assert manual.count("---\n") == 2
    assert "![](images/_page_1_Picture_0.jpeg)" in manual
    assert descriptor["configbrain"]["removedUnresolvedInternalLinks"] == 0
    assert Package(str(output / "datapackage.json")).validate().valid


def test_build_package_uses_canonical_catalog_metadata(tmp_path: Path) -> None:
    transformed, assets, manuals = write_fixture(tmp_path)
    documents = json.loads(manuals.read_text(encoding="utf-8"))
    manuals.write_text(
        json.dumps(
            {
                "product": "catalyst9500",
                "release": "26.x",
                "documents": documents,
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "package"
    build_package(transformed, assets, manuals, "test-guide", output)

    descriptor = json.loads((output / "datapackage.json").read_text())
    assert descriptor["configbrain"]["product"] == "Catalyst"
    assert descriptor["configbrain"]["catalogProduct"] == "catalyst9500"
    assert descriptor["configbrain"]["release"] == "26.x"


def test_build_package_rejects_changed_chunk(tmp_path: Path) -> None:
    transformed, assets, manuals = write_fixture(tmp_path)
    (transformed / "chunk-0001.md").write_text("changed", encoding="utf-8")

    with pytest.raises(PackagingError, match="checksum mismatch"):
        build_package(
            transformed,
            assets,
            manuals,
            "test-guide",
            tmp_path / "package",
        )


def test_build_package_rejects_missing_image(tmp_path: Path) -> None:
    transformed, assets, manuals = write_fixture(tmp_path)
    (assets / "_page_1_Picture_0.jpeg").unlink()

    with pytest.raises(PackagingError, match="Referenced image"):
        build_package(
            transformed,
            assets,
            manuals,
            "test-guide",
            tmp_path / "package",
        )


def test_remove_unresolved_internal_links_preserves_labels_and_valid_links() -> None:
    markdown = (
        '<span id="existing"></span>\n'
        "[Existing section](#existing)\n"
        "[Missing section](#missing)\n"
    )

    cleaned, removed = remove_unresolved_internal_links(markdown)

    assert "[Existing section](#existing)" in cleaned
    assert "Missing section" in cleaned
    assert "[Missing section](#missing)" not in cleaned
    assert removed == ["missing"]


def test_promote_package_falls_back_when_rename_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temporary = tmp_path / "temporary"
    output = tmp_path / "output"
    temporary.mkdir()
    (temporary / "datapackage.json").write_text("{}", encoding="utf-8")

    def deny_rename(source: Path, destination: Path) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr("scripts.package_document.os.replace", deny_rename)

    promote_package(temporary, output)

    assert (output / "datapackage.json").read_text() == "{}"


def test_build_package_does_not_fail_after_successful_copy_promotion_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transformed, assets, manuals = write_fixture(tmp_path)
    output = tmp_path / "package"
    original_rmtree = __import__("shutil").rmtree

    def deny_rename(source: Path, destination: Path) -> None:
        raise PermissionError("rename denied")

    def deny_only_temporary_cleanup(path: Path) -> None:
        if Path(path).name.startswith(".package."):
            raise PermissionError("cleanup denied")
        original_rmtree(path)

    monkeypatch.setattr("scripts.package_document.os.replace", deny_rename)
    monkeypatch.setattr(
        "scripts.package_document.shutil.rmtree", deny_only_temporary_cleanup
    )

    build_package(transformed, assets, manuals, "test-guide", output)

    assert (output / "datapackage.json").is_file()
