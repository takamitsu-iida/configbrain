from pathlib import Path

import pytest
from PIL import Image

from scripts.ocr_candidates import (
    CandidateSelectionError,
    find_image_candidates,
    select_sparse_pages,
)


def test_select_sparse_pages_uses_one_based_page_numbers() -> None:
    assert select_sparse_pages([120, 0, 49, 50], min_text_chars=50) == [
        {"page": 2, "text_chars": 0},
        {"page": 3, "text_chars": 49},
    ]


def test_find_image_candidates_selects_large_marker_diagrams(tmp_path: Path) -> None:
    Image.new("RGB", (400, 300)).save(tmp_path / "_page_23_Diagram_3.jpeg")
    Image.new("RGB", (100, 100)).save(tmp_path / "_page_24_Diagram_1.jpeg")
    Image.new("RGB", (400, 300)).save(tmp_path / "_page_25_Picture_0.jpeg")
    Image.new("RGB", (400, 300)).save(tmp_path / "unrelated.jpeg")

    candidates = find_image_candidates(tmp_path, 100_000, {"Diagram"})

    assert candidates == [
        {
            "path": str(tmp_path / "_page_23_Diagram_3.jpeg"),
            "page": 24,
            "kind": "Diagram",
            "width": 400,
            "height": 300,
            "area": 120_000,
        }
    ]


def test_select_sparse_pages_rejects_negative_threshold() -> None:
    with pytest.raises(CandidateSelectionError, match="cannot be negative"):
        select_sparse_pages([100], min_text_chars=-1)