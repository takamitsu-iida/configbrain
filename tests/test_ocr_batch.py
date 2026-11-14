import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.ocr_batch import OCRBatchError, output_path_for_image, run_batch


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def annotate_image(self, image_path: Path) -> dict[str, Any]:
        self.calls.append(image_path)
        return {
            "schema_version": 1,
            "source": {
                "path": str(image_path),
                "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                "size_bytes": image_path.stat().st_size,
            },
            "request": {"type": "image", "pages": None},
            "response": {"responses": [{"fullTextAnnotation": {"text": "Diagram"}}]},
        }


def write_manifest(path: Path, images: list[Path]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "images": [{"path": str(image_path)} for image_path in images],
            }
        ),
        encoding="utf-8",
    )


def test_run_batch_saves_results_and_resumes_completed_images(tmp_path: Path) -> None:
    image = tmp_path / "_page_23_Diagram_3.jpeg"
    image.write_bytes(b"image-data")
    manifest = tmp_path / "candidates.json"
    output_dir = tmp_path / "ocr"
    write_manifest(manifest, [image])
    client = FakeClient()

    assert run_batch(manifest, output_dir, client) == (1, 0)
    output_path = output_dir / "page-23-diagram-3.json"
    assert json.loads(output_path.read_text(encoding="utf-8"))["source"]["path"] == str(image)
    assert run_batch(manifest, output_dir, client) == (0, 1)
    assert client.calls == [image]


def test_run_batch_rejects_mismatched_existing_result(tmp_path: Path) -> None:
    image = tmp_path / "_page_23_Diagram_3.jpeg"
    image.write_bytes(b"image-data")
    manifest = tmp_path / "candidates.json"
    output_dir = tmp_path / "ocr"
    write_manifest(manifest, [image])
    output_dir.mkdir()
    (output_dir / "page-23-diagram-3.json").write_text(
        json.dumps(
            {
                "source": {"path": str(image), "sha256": "incorrect", "size_bytes": 10},
                "request": {"type": "image", "pages": None},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OCRBatchError, match="does not match candidate image"):
        run_batch(manifest, output_dir, FakeClient())


def test_output_path_for_image_uses_marker_page_and_item_indexes(tmp_path: Path) -> None:
    assert output_path_for_image(
        tmp_path / "_page_71_Diagram_2.jpeg", tmp_path / "ocr"
    ) == tmp_path / "ocr" / "page-71-diagram-2.json"
