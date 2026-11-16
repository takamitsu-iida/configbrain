"""Validate and apply explicitly reviewed, PDF-grounded Markdown corrections."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit

if __package__:
    from .check_pdf_markdown_quality import (
        FENCE, QualityError, markdown_tables, pdf_lines, table_cells,
    )
    from .package_document import ANCHOR_PATTERN, IMAGE_PATTERN, INTERNAL_LINK_PATTERN
else:
    from check_pdf_markdown_quality import (
        FENCE, QualityError, markdown_tables, pdf_lines, table_cells,
    )
    from package_document import ANCHOR_PATTERN, IMAGE_PATTERN, INTERNAL_LINK_PATTERN


NOTICE = (
    "Mechanical validation and application only; PDF hashes and coordinates do not prove "
    "that a replacement is faithful or complete. Approval is an explicit local review "
    "assertion, not an authenticated signature. This output is not document-level quality "
    "approval. No API calls, repairs by inference, source overwrites, packaging or indexing "
    "are performed. Only a completed manifest identifies a usable output bundle."
)
RESERVED = {
    "manual.md", "manifest.json", "validation.json", "correction-set.json",
    "diff.patch", "fragments", "evidence",
}
ID = re.compile(r"[a-z0-9][a-z0-9_-]*")
LINK = re.compile(r"(?<!!)\[[^\]]*]\(([^)\n]+)\)")


class CorrectionError(ValueError):
    """Raised when a correction cannot be verified or safely applied."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def object_fields(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise CorrectionError(f"{label} must contain exactly: {', '.join(sorted(keys))}")
    return value


def nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CorrectionError(f"{label} must be a non-empty string")
    return value


def sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise CorrectionError(f"{label} must be a lowercase SHA-256")
    return value


def interval(value: Any, label: str) -> tuple[int, int]:
    if (
        not isinstance(value, list) or len(value) != 2
        or any(type(number) is not int for number in value)
        or value[0] < 1 or value[1] < value[0]
    ):
        raise CorrectionError(f"{label} must be an inclusive 1-based [start, end]")
    return value[0], value[1]


def resolve_path(base: Path, value: Any, label: str) -> Path:
    return (base / nonempty(value, label)).resolve()


@dataclass
class Inputs:
    files: dict[Path, bytes] = field(default_factory=dict)

    def read(self, path: Path, expected: str | None = None) -> bytes:
        if path not in self.files:
            self.files[path] = path.read_bytes()
        data = self.files[path]
        if expected is not None and digest(data) != expected:
            raise CorrectionError(f"SHA-256 mismatch: {path}")
        return data

    def verify_unchanged(self) -> None:
        for path, data in self.files.items():
            if path.read_bytes() != data:
                raise CorrectionError(f"Input changed during validation/application: {path}")

    def records(self) -> list[dict[str, str]]:
        return [
            {"path": str(path), "sha256": digest(data)}
            for path, data in sorted(self.files.items())
        ]


@dataclass
class Change:
    id: str
    first: int
    last: int
    start: int
    end: int
    before: bytes
    after: bytes
    replacement_path: Path
    review: dict[str, Any]
    reason: str
    pages: tuple[int, int]
    evidence: list[dict[str, Any]]


def utf8(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CorrectionError(f"{label} must be UTF-8") from error


def check_fences(lines: list[str]) -> None:
    opening: str | None = None
    for line in lines:
        match = FENCE.match(line)
        if not match:
            continue
        marker, suffix = match.groups()
        if opening is None:
            opening = marker
        elif marker[0] == opening[0] and len(marker) >= len(opening) and not suffix.strip():
            opening = None
    if opening is not None:
        raise CorrectionError("Markdown contains an unclosed code fence")


def check_fragment(text: str, label: str) -> None:
    if not text.strip() or text.startswith("\ufeff"):
        raise CorrectionError(f"{label} is empty or contains an embedded BOM")
    if re.match(r"\A\s*---\s*(?:\r?\n|$)", text):
        raise CorrectionError(f"{label} must not introduce frontmatter")
    lines = text.splitlines()
    check_fences(lines)
    for line in lines:
        if line.lstrip().startswith("|"):
            if any(FENCE.match(cell) for cell in table_cells(line)):
                raise CorrectionError(f"{label} contains a code fence inside a table cell")
    for table in markdown_tables(lines, 1):
        if table["separator_columns"] != table["columns"] or table["row_width_mismatches"]:
            raise CorrectionError(f"{label} contains inconsistent table columns")


def replacement_bytes(data: bytes, before: bytes, has_after: bool, source: bytes) -> bytes:
    text = utf8(data, "Replacement")
    check_fragment(text, "Replacement")
    normalized = data.replace(b"\r\n", b"\n")
    if b"\r" in normalized:
        raise CorrectionError("Replacement contains unsupported standalone CR line endings")
    first_newline = re.search(rb"\r?\n", before) or re.search(rb"\r?\n", source)
    newline = first_newline.group() if first_newline else b"\n"
    result = normalized.replace(b"\n", newline)
    if has_after and not result.endswith(b"\n"):
        raise CorrectionError("Replacement must end with a newline before the following source line")
    if result == before:
        raise CorrectionError("Correction does not change the selected text")
    return result


def references(text: str) -> tuple[Counter[str], Counter[str], Counter[str]]:
    return (
        Counter(ANCHOR_PATTERN.findall(text)),
        Counter(match.group("destination").strip("<>") for match in IMAGE_PATTERN.finditer(text)),
        Counter(LINK.findall(text)),
    )


def check_result(before: str, after: str) -> dict[str, Any]:
    old, new = references(before), references(after)
    for link in new[2]:
        match = re.match(r"\s*(<[^>]+>|\S+)", link)
        if match is None:
            raise CorrectionError("Markdown contains an empty link destination")
        destination = match[1].strip("<>")
        if not destination.startswith(("#", "//")) and not urlsplit(destination).scheme:
            raise CorrectionError(f"Local non-image links are not supported in standalone output: {destination}")
    for label, original, corrected in zip(("anchors", "images", "links"), old, new):
        if original - corrected:
            raise CorrectionError(f"Correction removes existing {label}: {list((original - corrected).keys())}")
    if any(count > max(1, old[0][anchor]) for anchor, count in new[0].items()):
        raise CorrectionError("Correction introduces duplicate anchors")

    def missing(text: str, anchors: Counter[str]) -> Counter[str]:
        return Counter(
            match.group("target") for match in INTERNAL_LINK_PATTERN.finditer(text)
            if match.group("target") not in anchors
        )

    old_missing, new_missing = missing(before, old[0]), missing(after, new[0])
    if new_missing - old_missing:
        raise CorrectionError(f"Correction introduces broken internal links: {list((new_missing - old_missing).keys())}")
    old_tables = [
        table for table in markdown_tables(before.splitlines(), 1)
        if table["separator_columns"] != table["columns"] or table["row_width_mismatches"]
    ]
    new_tables = [
        table for table in markdown_tables(after.splitlines(), 1)
        if table["separator_columns"] != table["columns"] or table["row_width_mismatches"]
    ]

    def table_signatures(tables: list[dict[str, Any]]) -> Counter[tuple[Any, ...]]:
        return Counter(
            (table["columns"], table["separator_columns"],
             tuple(row["columns"] for row in table["row_width_mismatches"]))
            for table in tables
        )

    if table_signatures(new_tables) - table_signatures(old_tables):
        raise CorrectionError("Correction introduces inconsistent table columns at a range boundary")
    try:
        check_fences(before.splitlines())
    except CorrectionError as error:
        raise CorrectionError(f"Source has invalid code fences; correct the source scope first: {error}") from error
    check_fences(after.splitlines())
    return {
        "new_broken_internal_links": [],
        "preexisting_broken_internal_links": dict(new_missing),
        "preexisting_duplicate_anchors": {key: count for key, count in old[0].items() if count > 1},
        "preexisting_table_issues": old_tables,
        "remaining_table_issues": new_tables,
        "preserved_existing_references": True,
        "code_fences_closed": True,
        "replacement_tables_checked": True,
        "external_links": "not_fetched_or_validated",
        "pdf_content_faithfulness": "not_automatically_verified",
    }


def collect_assets(text: str, assets_dir: Path, inputs: Inputs) -> tuple[dict[str, bytes], list[str]]:
    assets: dict[str, bytes] = {}
    remote: list[str] = []
    for match in IMAGE_PATTERN.finditer(text):
        destination = match.group("destination").strip("<>")
        parsed_url = urlsplit(destination)
        if parsed_url.scheme in {"http", "https"} or destination.startswith("//"):
            remote.append(destination)
            continue
        relative = PurePosixPath(destination)
        if (
            parsed_url.scheme or parsed_url.query or parsed_url.fragment
            or relative.is_absolute() or ".." in relative.parts or "\\" in destination
            or not relative.parts or relative.parts[0] in RESERVED
        ):
            raise CorrectionError(f"Unsupported local image destination: {destination}")
        path = (assets_dir / destination).resolve()
        if not path.is_relative_to(assets_dir):
            raise CorrectionError(f"Image escapes assets directory: {destination}")
        assets[destination] = inputs.read(path)
    return assets, sorted(set(remote))


def load_changes(
    specification: dict[str, Any], base: Path, pdf_data: bytes, source: bytes, inputs: Inputs,
) -> list[Change]:
    values = specification["corrections"]
    if not isinstance(values, list) or not values:
        raise CorrectionError("corrections must be a non-empty array")
    lines = source.splitlines(keepends=True)
    changes: list[Change] = []
    seen: set[str] = set()
    for value in values:
        patch = object_fields(
            value, {"id", "reason", "pdf_pages", "target", "replacement", "review"}, "Correction",
        )
        identifier = nonempty(patch["id"], "Correction id")
        if not ID.fullmatch(identifier) or identifier in seen:
            raise CorrectionError(f"Invalid or duplicate correction id: {identifier}")
        seen.add(identifier)
        target = object_fields(patch["target"], {"lines", "sha256"}, "target")
        first, last = interval(target["lines"], "target.lines")
        if last > len(lines):
            raise CorrectionError(f"{identifier}: target exceeds source line count ({len(lines)})")
        original = b"".join(lines[first - 1:last])
        if digest(original) != sha256(target["sha256"], "target.sha256"):
            raise CorrectionError(f"{identifier}: selected text SHA-256 mismatch")
        if not original.strip():
            raise CorrectionError(f"{identifier}: select a non-empty context, not a bare insertion point")
        replacement = object_fields(patch["replacement"], {"path", "sha256"}, "replacement")
        path = resolve_path(base, replacement["path"], "replacement.path")
        if not path.is_relative_to(base):
            raise CorrectionError("Replacement files must be inside the correction-set directory")
        data = inputs.read(path, sha256(replacement["sha256"], "replacement.sha256"))
        rendered = replacement_bytes(data, original, last < len(lines), source)
        review = object_fields(
            patch["review"], {"status", "pdf_compared", "complete_procedure_checked", "notes"}, "review",
        )
        if not isinstance(review["status"], str) or review["status"] not in {"pending_review", "approved", "rejected"}:
            raise CorrectionError("review.status must be pending_review, approved or rejected")
        for name in ("pdf_compared", "complete_procedure_checked"):
            if type(review[name]) is not bool:
                raise CorrectionError(f"review.{name} must be boolean")
        nonempty(review["notes"], "review.notes")
        if review["status"] == "approved" and not (
            review["pdf_compared"] and review["complete_procedure_checked"]
        ):
            raise CorrectionError(f"{identifier}: approval requires both PDF and complete-procedure review")
        pages = interval(patch["pdf_pages"], "pdf_pages")
        evidence, sparse = pdf_lines(pdf_data, pages)
        if sparse:
            raise CorrectionError(f"{identifier}: PDF text evidence is insufficient on pages {sparse}")
        changes.append(Change(
            identifier, first, last, sum(map(len, lines[:first - 1])), sum(map(len, lines[:last])),
            original, rendered, path, review, nonempty(patch["reason"], "reason"), pages,
            [asdict(line) for line in evidence],
        ))
    changes.sort(key=lambda change: change.first)
    for previous, following in zip(changes, changes[1:]):
        if following.first <= previous.last:
            raise CorrectionError(f"Overlapping corrections: {previous.id}, {following.id}")
    return changes


def assemble(source: bytes, changes: list[Change]) -> tuple[bytes, list[dict[str, Any]]]:
    parts: list[bytes] = []
    records: list[dict[str, Any]] = []
    cursor = 0
    offset = 0
    for change in changes:
        unchanged = source[cursor:change.start]
        parts.extend((unchanged, change.after))
        offset += len(unchanged)
        records.append({
            "id": change.id, "reason": change.reason, "source_lines": [change.first, change.last],
            "source_byte_range": [change.start, change.end],
            "output_byte_range": [offset, offset + len(change.after)],
            "original_sha256": digest(change.before),
            "replacement_path": str(change.replacement_path),
            "applied_fragment_sha256": digest(change.after),
            "review": change.review, "pdf_pages": list(change.pages),
            "evidence": f"evidence/{change.id}.json",
        })
        offset += len(change.after)
        cursor = change.end
    parts.append(source[cursor:])
    result = b"".join(parts)
    old_cursor = new_cursor = 0
    for record in records:
        old_start, old_end = record["source_byte_range"]
        new_start, new_end = record["output_byte_range"]
        if source[old_cursor:old_start] != result[new_cursor:new_start]:
            raise CorrectionError("Text outside correction ranges changed")
        old_cursor, new_cursor = old_end, new_end
    if source[old_cursor:] != result[new_cursor:]:
        raise CorrectionError("Text following corrections changed")
    return result, records


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def correction_diff(before: str, after: str, original_path: Path) -> str:
    parts = []
    for line in difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=str(original_path), tofile="manual.md (candidate)",
    ):
        parts.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(parts)


def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CorrectionError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def run_corrections(specification_path: Path, output: Path, *, apply: bool = False) -> dict[str, Any]:
    if output.is_symlink():
        raise CorrectionError(f"Output directory is a symlink: {output}")
    specification_path, output = specification_path.resolve(), output.resolve()
    if output.exists():
        raise CorrectionError(f"Output directory already exists: {output}")
    inputs = Inputs()
    specification_data = inputs.read(specification_path)
    specification = object_fields(
        json.loads(utf8(specification_data, "Correction set"), object_pairs_hook=unique_fields),
        {"schema_version", "document_id", "source", "open_issues", "corrections"}, "Correction set",
    )
    if type(specification["schema_version"]) is not int or specification["schema_version"] != 1:
        raise CorrectionError("Unsupported correction schema_version")
    document_id = nonempty(specification["document_id"], "document_id")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", document_id):
        raise CorrectionError("Invalid document_id")
    issues = specification["open_issues"]
    if not isinstance(issues, list) or any(not isinstance(item, str) or not item.strip() for item in issues):
        raise CorrectionError("open_issues must be an array of non-empty strings")
    source = object_fields(specification["source"], {"pdf", "markdown", "assets_dir"}, "source")
    paths: dict[str, Path] = {}
    data: dict[str, bytes] = {}
    for name in ("pdf", "markdown"):
        entry = object_fields(source[name], {"path", "sha256"}, f"source.{name}")
        paths[name] = resolve_path(specification_path.parent, entry["path"], f"source.{name}.path")
        data[name] = inputs.read(paths[name], sha256(entry["sha256"], f"source.{name}.sha256"))
    assets_dir = resolve_path(specification_path.parent, source["assets_dir"], "source.assets_dir")
    if not assets_dir.is_dir():
        raise CorrectionError(f"Assets directory does not exist: {assets_dir}")
    before = utf8(data["markdown"], "Source Markdown")
    if not before.strip():
        raise CorrectionError("Source Markdown is empty")
    changes = load_changes(specification, specification_path.parent, data["pdf"], data["markdown"], inputs)
    corrected, records = assemble(data["markdown"], changes)
    validation = check_result(before, utf8(corrected, "Corrected Markdown"))
    validation["outside_ranges_unchanged"] = True
    assets, remote = collect_assets(utf8(corrected, "Corrected Markdown"), assets_dir, inputs)
    validation["external_images_not_checked"] = remote
    for directory in {path.parent for path in inputs.files} | {assets_dir}:
        if output.is_relative_to(directory):
            raise CorrectionError(f"Output must be outside input directories: {directory}")
    blocked = [change.id for change in changes if change.review["status"] != "approved"]
    should_apply = apply and not blocked
    manifest: dict[str, Any] = {
        "schema_version": 1, "document_id": document_id, "notice": NOTICE,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "blocked_unapproved" if apply and blocked
            else "applied_review_required" if should_apply
            else "validated_review_required" if blocked else "validated"
        ),
        "mode": "apply" if apply else "check",
        "source_correction_set": {"path": str(specification_path), "sha256": digest(specification_data)},
        "source_markdown": {"path": str(paths["markdown"]), "sha256": digest(data["markdown"])},
        "source_pdf": {"path": str(paths["pdf"]), "sha256": digest(data["pdf"])},
        "tool": {"path": str(Path(__file__).resolve()), "sha256": digest(inputs.read(Path(__file__).resolve()))},
        "corrections": records, "blocked_corrections": blocked, "open_issues": issues,
        "document_quality_approved": False,
        "output": {"path": "manual.md", "sha256": digest(corrected)} if should_apply else None,
        "assets": [
            {"path": name, "sha256": digest(content)} for name, content in sorted(assets.items())
        ] if should_apply else [],
        "verified_inputs": inputs.records(),
    }
    inputs.verify_unchanged()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        (staging / "fragments").mkdir()
        (staging / "evidence").mkdir()
        for change in changes:
            (staging / "fragments" / f"{change.id}.before.md").write_bytes(change.before)
            (staging / "fragments" / f"{change.id}.after.md").write_bytes(change.after)
            write_json(staging / "evidence" / f"{change.id}.json", {
                "pdf_sha256": digest(data["pdf"]), "pdf_pages": list(change.pages),
                "lines": change.evidence, "notice": "Coordinates are evidence, not content approval.",
            })
        (staging / "correction-set.json").write_bytes(specification_data)
        (staging / "diff.patch").write_text(
            correction_diff(before, utf8(corrected, "Corrected Markdown"), paths["markdown"]),
            encoding="utf-8", newline="",
        )
        write_json(staging / "validation.json", validation)
        if should_apply:
            (staging / "manual.md").write_bytes(corrected)
            for name, content in assets.items():
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
        write_json(staging / "manifest.json", manifest)
        inputs.verify_unchanged()
        output.mkdir()
        for child in staging.iterdir():
            if child.name != "manifest.json":
                child.rename(output / child.name)
        (staging / "manifest.json").rename(output / "manifest.json")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "apply"):
        command = commands.add_parser(name)
        command.add_argument("correction_set", type=Path)
        command.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = run_corrections(args.correction_set, args.output_dir, apply=args.command == "apply")
    except (CorrectionError, QualityError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"Correction error: {error}", file=sys.stderr)
        return 1
    print(f"{manifest['status']}: {args.output_dir / 'manifest.json'}")
    print("Document quality is not approved; packaging and indexing were not performed.")
    return 2 if manifest["blocked_corrections"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
