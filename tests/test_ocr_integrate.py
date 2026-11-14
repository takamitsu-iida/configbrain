import hashlib
import json
from pathlib import Path

import pytest

from scripts.ocr_integrate import OCRIntegrationError, integrate_ocr, save_markdown


def write_result(image: Path, output_path: Path, text: str) -> None:
    output_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {
                    "path": str(image),
                    "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                    "size_bytes": image.stat().st_size,
                },
                "request": {"type": "image", "pages": None},
                "response": {
                    "responses": [{"fullTextAnnotation": {"text": text}}],
                },
            }
        ),
        encoding="utf-8",
    )


def write_manifest(path: Path, image: Path) -> None:
    path.write_text(
        json.dumps({"schema_version": 1, "images": [{"path": str(image)}]}),
        encoding="utf-8",
    )


def test_integrate_ocr_inserts_details_after_matching_image(tmp_path: Path) -> None:
    image = tmp_path / "_page_23_Diagram_3.jpeg"
    image.write_bytes(b"image-data")
    markdown_path = tmp_path / "manual.md"
    markdown_path.write_text(
        "Before\n\n![](_page_23_Diagram_3.jpeg)\n\nAfter\n", encoding="utf-8"
    )
    manifest = tmp_path / "candidates.json"
    write_manifest(manifest, image)
    ocr_dir = tmp_path / "ocr"
    ocr_dir.mkdir()
    write_result(image, ocr_dir / "page-23-diagram-3.json", "Host A\nSwitch 1")

    integrated, count = integrate_ocr(markdown_path, manifest, ocr_dir)

    assert count == 1
    assert integrated == (
        "Before\n\n![](_page_23_Diagram_3.jpeg)\n\n"
        "<details>\n"
        "<summary>Cloud Vision OCR text for _page_23_Diagram_3.jpeg</summary>\n\n"
        "```text\nHost A\nSwitch 1\n```\n"
        "</details>\n\nAfter\n"
    )


def test_integrate_ocr_rejects_mismatched_result(tmp_path: Path) -> None:
    image = tmp_path / "_page_23_Diagram_3.jpeg"
    image.write_bytes(b"image-data")
    markdown_path = tmp_path / "manual.md"
    markdown_path.write_text("![](_page_23_Diagram_3.jpeg)\n", encoding="utf-8")
    manifest = tmp_path / "candidates.json"
    write_manifest(manifest, image)
    ocr_dir = tmp_path / "ocr"
    ocr_dir.mkdir()
    write_result(image, ocr_dir / "page-23-diagram-3.json", "Host A")
    image.write_bytes(b"changed-image-data")

    with pytest.raises(OCRIntegrationError, match="does not match candidate image"):
        integrate_ocr(markdown_path, manifest, ocr_dir)


def test_integrate_ocr_rejects_unreferenced_candidate_image(tmp_path: Path) -> None:
    image = tmp_path / "_page_23_Diagram_3.jpeg"
    image.write_bytes(b"image-data")
    markdown_path = tmp_path / "manual.md"
    markdown_path.write_text("# Manual\n", encoding="utf-8")
    manifest = tmp_path / "candidates.json"
    write_manifest(manifest, image)
    ocr_dir = tmp_path / "ocr"
    ocr_dir.mkdir()
    write_result(image, ocr_dir / "page-23-diagram-3.json", "Host A")

    with pytest.raises(OCRIntegrationError, match="not referenced by Markdown"):
        integrate_ocr(markdown_path, manifest, ocr_dir)


def test_save_markdown_rejects_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "manual-ocr.md"
    save_markdown("# Manual\n", output)

    with pytest.raises(OCRIntegrationError, match="already exists"):
        save_markdown("# Changed\n", output)
