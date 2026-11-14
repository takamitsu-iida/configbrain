"""Split Markdown into heading-aware chunks for later LLM transformation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


class ChunkingError(RuntimeError):
    """Raised when Markdown cannot be split into safe chunks."""


HEADING_PATTERN = re.compile(r"^(?P<markers>#{1,6})\s+(?P<text>.+?)\s*$")
FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})")
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class Section:
    content: str
    heading_path: tuple[str, ...]


@dataclass(frozen=True)
class Chunk:
    content: str
    heading_paths: tuple[tuple[str, ...], ...]


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def validate_max_tokens(max_tokens: int) -> None:
    if max_tokens < 1:
        raise ChunkingError("Maximum estimated tokens must be greater than zero")


def parse_sections(markdown: str) -> list[Section]:
    sections: list[Section] = []
    heading_stack: list[str] = []
    content_lines: list[str] = []
    current_path: tuple[str, ...] = ()

    def save_current_section() -> None:
        content = "\n".join(content_lines).strip()
        if content:
            sections.append(Section(content, current_path))

    for line in markdown.splitlines():
        match = HEADING_PATTERN.match(line)
        if match is None:
            content_lines.append(line)
            continue
        save_current_section()
        level = len(match.group("markers"))
        heading_stack[level - 1 :] = [line]
        current_path = tuple(heading_stack)
        content_lines = [line]
    save_current_section()
    return sections


def split_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current_lines: list[str] = []
    in_fence = False
    fence_marker = ""

    def save_block() -> None:
        block = "\n".join(current_lines).strip()
        if block:
            blocks.append(block)

    for line in text.splitlines():
        fence_match = FENCE_PATTERN.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker[0]
            elif marker[0] == fence_marker:
                in_fence = False
        if not in_fence and not line.strip():
            save_block()
            current_lines = []
            continue
        current_lines.append(line)
    if in_fence:
        raise ChunkingError("Markdown contains an unclosed fenced code block")
    save_block()
    return blocks


def validate_fenced_code_blocks(markdown: str) -> None:
    in_fence = False
    fence_marker = ""
    for line in markdown.splitlines():
        match = FENCE_PATTERN.match(line)
        if match is None:
            continue
        marker = match.group(1)
        if not in_fence:
            in_fence = True
            fence_marker = marker[0]
        elif marker[0] == fence_marker:
            in_fence = False
    if in_fence:
        raise ChunkingError("Markdown contains an unclosed fenced code block")


def split_block(block: str, max_characters: int) -> list[str]:
    if len(block) <= max_characters:
        return [block]
    if FENCE_PATTERN.match(block):
        return [block]

    parts: list[str] = []
    current_lines: list[str] = []
    current_length = 0
    for line in block.splitlines():
        line_length = len(line) + (1 if current_lines else 0)
        if current_lines and current_length + line_length > max_characters:
            parts.append("\n".join(current_lines))
            current_lines = []
            current_length = 0
        if len(line) > max_characters:
            if current_lines:
                parts.append("\n".join(current_lines))
                current_lines = []
                current_length = 0
            parts.extend(
                line[index : index + max_characters]
                for index in range(0, len(line), max_characters)
            )
            continue
        current_lines.append(line)
        current_length += line_length
    if current_lines:
        parts.append("\n".join(current_lines))
    return parts


def split_section(section: Section, max_tokens: int) -> list[Section]:
    max_characters = max_tokens * CHARS_PER_TOKEN
    if len(section.content) <= max_characters:
        return [section]

    heading_prefix = "\n".join(section.heading_path)
    body = section.content
    if section.heading_path and body.startswith(section.heading_path[-1]):
        body = body[len(section.heading_path[-1]) :].lstrip("\n")
    blocks = split_blocks(body)
    pieces: list[Section] = []
    current_blocks: list[str] = []

    def save_piece() -> None:
        if not current_blocks:
            return
        prefix = heading_prefix if not pieces else heading_prefix
        content = f"{prefix}\n\n" + "\n\n".join(current_blocks) if prefix else "\n\n".join(current_blocks)
        pieces.append(Section(content, section.heading_path))

    available_characters = max_characters - len(heading_prefix) - 2
    if available_characters < 1:
        raise ChunkingError("Heading hierarchy exceeds the maximum estimated tokens")
    for block in blocks:
        for part in split_block(block, available_characters):
            candidate = "\n\n".join([*current_blocks, part])
            if current_blocks and len(candidate) > available_characters:
                save_piece()
                current_blocks = []
            current_blocks.append(part)
    save_piece()
    return pieces


def chunk_sections(sections: list[Section], max_tokens: int) -> list[Chunk]:
    validate_max_tokens(max_tokens)
    chunks: list[Chunk] = []
    current_sections: list[Section] = []
    current_length = 0
    max_characters = max_tokens * CHARS_PER_TOKEN

    def save_chunk() -> None:
        if not current_sections:
            return
        chunks.append(
            Chunk(
                content="\n\n".join(section.content for section in current_sections) + "\n",
                heading_paths=tuple(section.heading_path for section in current_sections),
            )
        )

    for section in sections:
        for piece in split_section(section, max_tokens):
            separator_length = 2 if current_sections else 0
            if current_sections and current_length + separator_length + len(piece.content) > max_characters:
                save_chunk()
                current_sections = []
                current_length = 0
            current_sections.append(piece)
            current_length += separator_length + len(piece.content)
    save_chunk()
    return chunks


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def create_chunks(markdown_path: Path, max_tokens: int = 6_000) -> list[Chunk]:
    validate_max_tokens(max_tokens)
    if not markdown_path.is_file():
        raise ChunkingError(f"Input Markdown does not exist: {markdown_path}")
    try:
        markdown = markdown_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ChunkingError(f"Could not read input Markdown: {markdown_path}") from error
    if not markdown.strip():
        raise ChunkingError(f"Input Markdown is empty: {markdown_path}")
    validate_fenced_code_blocks(markdown)
    return chunk_sections(parse_sections(markdown), max_tokens)


def save_chunks(
    chunks: list[Chunk],
    markdown_path: Path,
    output_dir: Path,
    max_tokens: int,
) -> None:
    if output_dir.exists():
        raise ChunkingError(f"Output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        chunk_metadata: list[dict[str, object]] = []
        for index, chunk in enumerate(chunks, start=1):
            filename = f"chunk-{index:04d}.md"
            (temporary_dir / filename).write_text(chunk.content, encoding="utf-8")
            chunk_metadata.append(
                {
                    "id": f"chunk-{index:04d}",
                    "path": filename,
                    "estimated_tokens": estimate_tokens(chunk.content),
                    "sha256": sha256_text(chunk.content),
                    "heading_paths": [list(path) for path in chunk.heading_paths],
                }
            )
        manifest = {
            "schema_version": 1,
            "source": {
                "path": str(markdown_path),
                "sha256": sha256_file(markdown_path),
                "size_bytes": markdown_path.stat().st_size,
            },
            "chunking": {
                "max_estimated_tokens": max_tokens,
                "characters_per_token": CHARS_PER_TOKEN,
                "strategy": "heading-first, then paragraph-boundary",
            },
            "chunks": chunk_metadata,
        }
        (temporary_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            os.replace(temporary_dir, output_dir)
        except PermissionError:
            shutil.copytree(temporary_dir, output_dir)
    except OSError as error:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        raise ChunkingError(
            f"Could not save Markdown chunks: {output_dir}: {error}"
        ) from error
    finally:
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise ChunkingError(f"Could not calculate input checksum: {path}") from error
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Split Markdown into heading-aware chunks for LLM transformation."
    )
    parser.add_argument("input", type=Path, help="Path to input Markdown.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--max-estimated-tokens",
        type=int,
        default=6_000,
        help="Maximum estimated tokens per chunk (default: 6000).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        chunks = create_chunks(args.input, args.max_estimated_tokens)
        save_chunks(chunks, args.input, args.output_dir, args.max_estimated_tokens)
    except ChunkingError as error:
        print(f"Markdown chunking failed: {error}", file=sys.stderr)
        return 1

    print(f"Saved {len(chunks)} Markdown chunk(s) to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
