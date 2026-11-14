import json
from pathlib import Path

import pytest

from scripts.chunk_markdown import (
    ChunkingError,
    create_chunks,
    estimate_tokens,
    save_chunks,
)


def test_create_chunks_keeps_sections_within_limit(tmp_path: Path) -> None:
    markdown = (
        "# Chapter\n\n"
        "First paragraph.\n\n"
        "## Section A\n\n"
        "Second paragraph.\n\n"
        "## Section B\n\n"
        "Third paragraph.\n"
    )
    path = tmp_path / "manual.md"

    chunks = create_chunks_from_text(path, markdown, max_tokens=20)

    assert [chunk.content for chunk in chunks] == [
        "# Chapter\n\nFirst paragraph.\n\n## Section A\n\nSecond paragraph.\n",
        "## Section B\n\nThird paragraph.\n",
    ]
    assert chunks[0].heading_paths == (("# Chapter",), ("# Chapter", "## Section A"))


def test_create_chunks_splits_oversized_section_at_paragraphs(tmp_path: Path) -> None:
    markdown_path = tmp_path / "manual.md"
    markdown_path.write_text(
        "# Chapter\n\nfirst paragraph\n\nsecond paragraph\n\nthird paragraph\n",
        encoding="utf-8",
    )

    chunks = create_chunks(markdown_path, max_tokens=8)

    assert [chunk.content for chunk in chunks] == [
        "# Chapter\n\nfirst paragraph\n",
        "# Chapter\n\nsecond paragraph\n",
        "# Chapter\n\nthird paragraph\n",
    ]
    assert all(estimate_tokens(chunk.content) <= 8 for chunk in chunks)


def test_create_chunks_rejects_unclosed_code_fence(tmp_path: Path) -> None:
    markdown_path = tmp_path / "manual.md"
    markdown_path.write_text("# Chapter\n\n```\nmissing close\n", encoding="utf-8")

    with pytest.raises(ChunkingError, match="unclosed"):
        create_chunks(markdown_path)


def test_save_chunks_creates_markdown_and_manifest(tmp_path: Path) -> None:
    markdown_path = tmp_path / "manual.md"
    markdown_path.write_text("# Chapter\n\nText\n", encoding="utf-8")
    chunks = create_chunks(markdown_path)
    output_dir = tmp_path / "chunks"

    save_chunks(chunks, markdown_path, output_dir, 6_000)

    assert (output_dir / "chunk-0001.md").read_text(encoding="utf-8") == "# Chapter\n\nText\n"
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"]["path"] == str(markdown_path)
    assert manifest["chunking"]["max_estimated_tokens"] == 6_000
    assert manifest["chunks"][0]["heading_paths"] == [["# Chapter"]]
    with pytest.raises(ChunkingError, match="already exists"):
        save_chunks(chunks, markdown_path, output_dir, 6_000)


def test_save_chunks_falls_back_when_directory_rename_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    markdown_path = tmp_path / "manual.md"
    markdown_path.write_text("# Chapter\n\nText\n", encoding="utf-8")
    chunks = create_chunks(markdown_path)
    output_dir = tmp_path / "chunks"

    def deny_rename(source: Path, destination: Path) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr("scripts.chunk_markdown.os.replace", deny_rename)

    save_chunks(chunks, markdown_path, output_dir, 6_000)

    assert (output_dir / "chunk-0001.md").is_file()
    assert (output_dir / "manifest.json").is_file()


def create_chunks_from_text(path: Path, markdown: str, max_tokens: int):
    path.write_text(markdown, encoding="utf-8")
    return create_chunks(path, max_tokens)
