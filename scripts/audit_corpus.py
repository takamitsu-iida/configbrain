"""Audit every local PDF, extracted Markdown and OKF package without API calls."""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re
import sys
import unicodedata
from typing import Any

from frictionless import FrictionlessException
import pypdfium2

if __package__:
    from .check_pdf_markdown_quality import (
        FENCE, HEADER, QualityError, commands_in_line, markdown_commands,
        markdown_tables, normalize_command,
    )
    from .validate_packages import (
        ANCHOR_PATTERN, PackageValidationError, sha256_file, validate_package,
    )
else:
    from check_pdf_markdown_quality import (
        FENCE, HEADER, QualityError, commands_in_line, markdown_commands,
        markdown_tables, normalize_command,
    )
    from validate_packages import (
        ANCHOR_PATTERN, PackageValidationError, sha256_file, validate_package,
    )


SCHEMA_VERSION = 1
SHINGLE = 48
STRIDE = 32
LOW_COVERAGE = 0.90
IMAGE = re.compile(r"!\[[^\]]*]\((?:<)?([^)\s>]+)[^)\n]*\)")
LINK = re.compile(r"(?<!!)\[([^\]]*)]\([^)\n]+\)")
CONSTRAINT = re.compile(
    r"\b(?:must|do not|not supported|only|requires?|warning|caution|note)\b",
    re.IGNORECASE,
)
NOTICE = (
    "Local heuristic audit, not approval of semantic correctness or completeness. "
    "Text normalization ignores spacing, case and punctuation; CLI checks also compare "
    "punctuation but PDF columns and wrapping can cause false positives. "
    "Page windows are inferred from unique text anchors and overlap at boundaries. "
    "Repeated lines and standalone page numbers in the outer 6% PDF margins are excluded "
    "from text coverage and recorded. "
    "Images, diagrams, OCR and the meaning of tables require visual review. "
    "No API calls, repairs, package writes or index updates are performed."
)


class AuditError(ValueError):
    """Raised when an audit input or checkpoint is invalid."""


def compact(value: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def visible_line(value: str) -> str:
    value = IMAGE.sub("", value)
    value = LINK.sub(r"\1", value)
    value = ANCHOR_PATTERN.sub("", value)
    return re.sub(r"</?(?:br|span|sup|sub|em|strong)\b[^>]*>", " ", value)


@dataclass
class Markdown:
    lines: list[str]
    text: str
    ends: list[int]

    @classmethod
    def read(cls, path: Path) -> Markdown:
        lines = path.read_text(encoding="utf-8").splitlines()
        parts: list[str] = []
        ends: list[int] = []
        frontmatter = bool(lines and lines[0].strip() == "---")
        length = 0
        for index, line in enumerate(lines):
            if frontmatter:
                if index > 0 and line.strip() == "---":
                    frontmatter = False
                part = ""
            elif FENCE.match(line) or re.fullmatch(r"\s*>\s*\[![A-Z]+]\s*", line):
                part = ""
            else:
                part = compact(visible_line(line))
            parts.append(part)
            length += len(part)
            ends.append(length)
        if frontmatter:
            raise AuditError(f"Unclosed frontmatter: {path}")
        return cls(lines, "".join(parts), ends)

    def line_at(self, offset: int) -> int:
        return min(bisect_right(self.ends, offset) + 1, len(self.lines))

    def line_range(self, start: int, end: int) -> list[int]:
        return [self.line_at(start), self.line_at(max(start, end - 1))]


def read_pdf(path: Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    with pypdfium2.PdfDocument(path) as document:
        for number in range(len(document)):
            try:
                page = document[number]
                try:
                    textpage = page.get_textpage()
                    try:
                        text = textpage.get_text_range()
                        margins: list[int] = []
                        positions_available = len(text) == textpage.count_chars()
                        if positions_available:
                            offset = 0
                            for line_index, line in enumerate(text.splitlines(keepends=True)):
                                visible = next(
                                    (i for i, character in enumerate(line) if not character.isspace()),
                                    None,
                                )
                                if visible is not None:
                                    _, bottom, _, top = textpage.get_charbox(offset + visible)
                                    center = (top + bottom) / 2
                                    if center < page.get_height() * 0.06 or center > page.get_height() * 0.94:
                                        margins.append(line_index)
                                offset += len(line)
                    finally:
                        textpage.close()
                    images = sum(1 for _ in page.get_objects(
                        filter=[pypdfium2.raw.FPDF_PAGEOBJ_IMAGE],
                    ))
                finally:
                    page.close()
                pages.append({
                    "page": number + 1, "raw": text, "images": images,
                    "margin_indices": margins, "positions_available": positions_available,
                })
            except (pypdfium2.PdfiumError, ValueError) as error:
                pages.append({"page": number + 1, "error": str(error)})
    if not pages:
        raise AuditError(f"PDF has no pages: {path}")
    exclude_pdf_margins(pages)
    return pages


def exclude_pdf_margins(pages: list[dict[str, Any]]) -> None:
    edge_counts: Counter[str] = Counter()
    for page in pages:
        lines = page.get("raw", "").splitlines()
        edge_counts.update({
            compact(lines[i]) for i in page.get("margin_indices", []) if compact(lines[i])
        })
    for page in pages:
        lines = page.get("raw", "").splitlines()
        kept, ignored = [], []
        for index, line in enumerate(lines):
            edge = index in page.get("margin_indices", [])
            repeated = edge_counts[compact(line)] >= 3
            page_number = re.fullmatch(r"\s*(?:\d+|[ivxlcdm]+)\s*", line, re.IGNORECASE)
            if edge and (repeated or page_number):
                ignored.append(line)
            else:
                kept.append(line)
        page.update(lines=kept, text=compact("\n".join(kept)), ignored_edge_lines=ignored)


def ordered_anchors(anchors: list[tuple[int, int]]) -> list[tuple[int, int]]:
    tails: list[int] = []
    indices: list[int] = []
    previous: list[int] = []
    for index, (_, target) in enumerate(anchors):
        slot = bisect_left(tails, target)
        previous.append(indices[slot - 1] if slot else -1)
        if slot == len(tails):
            tails.append(target)
            indices.append(index)
        else:
            tails[slot], indices[slot] = target, index
    result: list[tuple[int, int]] = []
    index = indices[-1] if indices else -1
    while index >= 0:
        result.append(anchors[index])
        index = previous[index]
    return list(reversed(result))


def align_pages(pages: list[dict[str, Any]], markdown: Markdown) -> list[dict[str, Any]]:
    # Sampling the target and scanning every source offset avoids a shared chunk phase.
    index: dict[str, int | None] = {}
    for offset in range(0, len(markdown.text) - SHINGLE + 1, STRIDE):
        key = markdown.text[offset:offset + SHINGLE]
        index[key] = None if key in index else offset
    occurrences: Counter[int] = Counter()
    candidates: list[list[tuple[int, int]]] = []
    for page in pages:
        found = []
        source = page["text"]
        for offset in range(len(source) - SHINGLE + 1):
            target = index.get(source[offset:offset + SHINGLE])
            if target is not None:
                found.append((offset, target))
                occurrences[target] += 1
        candidates.append(found)
    mappings: list[dict[str, Any]] = []
    for found in candidates:
        unique = [pair for pair in found if occurrences[pair[1]] == 1]
        ordered = ordered_anchors(unique)
        enough = (
            len(ordered) >= 3
            and ordered[-1][0] - ordered[0][0] >= SHINGLE
            and ordered[-1][1] - ordered[0][1] >= SHINGLE
            and len(ordered) >= 0.8 * len(unique)
        )
        mappings.append({
            "status": "aligned" if enough else "unverifiable",
            "unique_anchors": len(unique),
            "ordered_anchors": len(ordered),
            "reason": None if enough else "Insufficient unique, ordered text anchors",
            "anchor_offsets": [ordered[0][1], ordered[-1][1] + SHINGLE] if enough else None,
        })
    aligned = [i for i, mapping in enumerate(mappings) if mapping["status"] == "aligned"]
    conflicts: set[int] = set()
    for left, right in zip(aligned, aligned[1:]):
        if mappings[left]["anchor_offsets"][1] > mappings[right]["anchor_offsets"][0]:
            conflicts.update((left, right))
    for position in conflicts:
        mappings[position].update(
            status="unverifiable", reason="Page anchors overlap or contradict PDF page order",
        )
    aligned = [i for i in aligned if i not in conflicts]
    for position, page_index in enumerate(aligned):
        mapping = mappings[page_index]
        start = mappings[aligned[position - 1]]["anchor_offsets"][1] if position else 0
        end = (
            mappings[aligned[position + 1]]["anchor_offsets"][0]
            if position + 1 < len(aligned) else len(markdown.text)
        )
        mapping.update(
            comparison_offsets=[start, end],
            markdown_lines=markdown.line_range(start, end),
            anchor_lines=markdown.line_range(*mapping["anchor_offsets"]),
            boundaries="inferred_overlapping_context",
            adjacent_unaligned_pages=(
                (page_index > 0 and page_index - 1 not in aligned)
                or (page_index + 1 < len(pages) and page_index + 1 not in aligned)
            ),
        )
    return mappings


def coverage(source: str, target: str) -> dict[str, Any]:
    if not source:
        return {"ratio": None, "examined": 0, "missing": 0, "missing_examples": []}
    offsets = list(range(0, max(1, len(source) - SHINGLE + 1), STRIDE))
    offsets.append(max(0, len(source) - SHINGLE))
    fragments = [source[offset:offset + SHINGLE] for offset in sorted(set(offsets))]
    missing = [fragment for fragment in fragments if fragment not in target]
    return {
        "ratio": round(1 - len(missing) / len(fragments), 6),
        "examined": len(fragments),
        "missing": len(missing),
        "missing_examples": missing[:8],
    }


def markdown_findings(markdown: Markdown) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    try:
        markdown_commands(markdown.lines, 1)
    except QualityError as error:
        findings.append({"kind": "invalid_code_fence", "detail": str(error)})
    for table in markdown_tables(markdown.lines, 1):
        if table["separator_columns"] != table["columns"] or table["row_width_mismatches"]:
            findings.append({"kind": "inconsistent_table_columns", **table})
        if any(HEADER.search(cell) for cell in table["header"]):
            findings.append({"kind": "merged_procedure_header", "line": table["line"]})
        if table["columns"] > 3 and "commandoraction" in compact("".join(table["header"])):
            findings.append({
                "kind": "possible_fragmented_procedure_table",
                "line": table["line"], "columns": table["columns"],
            })
        steps = [int(row["step"]) for row in table["step_rows"]]
        if any(right != left + 1 for left, right in zip(steps, steps[1:])):
            findings.append({
                "kind": "possible_nonconsecutive_table_steps", "line": table["line"],
                "steps": steps,
            })
    return findings


def feature_changes(source: Markdown, target: Markdown) -> list[dict[str, Any]]:
    def features(markdown: Markdown) -> dict[str, Counter[str]]:
        body = "\n".join(markdown.lines)
        return {
            "heading": Counter(
                compact(visible_line(line.lstrip("# ")))
                for line in markdown.lines if re.match(r"^#{1,6}\s+", line)
            ),
            "anchor": Counter(ANCHOR_PATTERN.findall(body)),
            "image": Counter(Path(path).name for path in IMAGE.findall(body)),
        }

    before, after = features(source), features(target)
    return [
        {"kind": f"{feature}_count_changed", "value": value,
         "extracted_count": before[feature][value], "package_count": after[feature][value]}
        for feature in before
        for value in sorted(before[feature].keys() | after[feature].keys())
        if before[feature][value] != after[feature][value]
    ]


def audit_page(
    page: dict[str, Any], source: Markdown, target: Markdown,
    source_mapping: dict[str, Any], target_mapping: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "page": page["page"], "status": "checked",
        "text_characters": len(page["text"]), "pdf_images": page.get("images", 0),
        "ignored_edge_lines": page["ignored_edge_lines"],
        "mappings": {"extracted": source_mapping, "package": target_mapping},
        "coverage": {}, "findings": [],
    }
    if "error" in page:
        result.update(status="error", error=page["error"])
        return result
    if page.get("positions_available") is False:
        result["findings"].append({"kind": "pdf_margin_coordinates_unavailable"})
    if len(page["text"]) < 50:
        result.update(status="unverifiable", reason="Fewer than 50 normalized PDF text characters")
        return result
    if any(mapping["status"] != "aligned" for mapping in (source_mapping, target_mapping)):
        result.update(status="unverifiable", reason="PDF to Markdown correspondence is uncertain")
    for name, markdown, mapping in (
        ("extracted", source, source_mapping), ("package", target, target_mapping),
    ):
        if mapping["status"] != "aligned":
            continue
        start, end = mapping["comparison_offsets"]
        metric = coverage(page["text"], markdown.text[start:end])
        result["coverage"][name] = metric
        if metric["missing"]:
            result["findings"].append({
                "kind": "pdf_text_fragments_not_found", "stage": name,
                "priority": "high" if metric["ratio"] < LOW_COVERAGE else "review",
                **metric,
            })
    if target_mapping["status"] == "aligned":
        first, last = target_mapping["markdown_lines"]
        window = "\n".join(target.lines[first - 1:last])
        cli_text = re.sub(r"\s+", "", normalize_command(window))
        missing_commands = sorted({
            command for line in page["lines"] for command in commands_in_line(line)
            if re.sub(r"\s+", "", command) not in cli_text
        })
        for command in missing_commands:
            result["findings"].append({
                "kind": "possible_missing_or_changed_prompt_command",
                "command": command, "markdown_lines": [first, last],
            })
        start, end = target_mapping["comparison_offsets"]
        for line in page["lines"]:
            if CONSTRAINT.search(line) and len(compact(line)) >= 30:
                if compact(line) not in target.text[start:end]:
                    result["findings"].append({
                        "kind": "possible_missing_constraint_or_alert",
                        "pdf_text": line, "markdown_lines": [first, last],
                    })
    if source_mapping["status"] == target_mapping["status"] == "aligned":
        for name, origin, destination, origin_mapping, destination_mapping in (
            ("extracted_to_package", source, target, source_mapping, target_mapping),
            ("package_to_extracted", target, source, target_mapping, source_mapping),
        ):
            start, end = origin_mapping["anchor_offsets"]
            target_start, target_end = destination_mapping["comparison_offsets"]
            metric = coverage(origin.text[start:end], destination.text[target_start:target_end])
            result["coverage"][name] = metric
            if metric["missing"]:
                result["findings"].append({
                    "kind": "possible_transformation_text_change", "direction": name,
                    "priority": "high" if metric["ratio"] < LOW_COVERAGE else "review",
                    **metric,
                })
    if page.get("images"):
        result["findings"].append({
            "kind": "visual_review_required", "pdf_images": page["images"],
        })
    if result["findings"] and result["status"] == "checked":
        result["status"] = "review_required"
    return result


def snapshot(paths: dict[str, Path]) -> dict[str, Any]:
    return {
        name: {"path": str(path.resolve()), "sha256": sha256_file(path)}
        for name, path in paths.items()
    }


def document_inputs(root: Path, document_id: str) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for stage, pattern in (("raw", "*.pdf"), ("extracted", "*.md")):
        matches = sorted((root / stage / document_id).rglob(pattern))
        if len(matches) != 1:
            raise AuditError(f"{document_id}: expected one {stage} {pattern}, found {len(matches)}")
        paths[stage] = matches[0]
    for name, filename in (("manual", "manual.md"), ("descriptor", "datapackage.json")):
        path = root / "packages" / document_id / filename
        if not path.is_file():
            raise AuditError(f"Missing package input: {path}")
        paths[name] = path
    return paths


def audit_document(
    paths: dict[str, Path], package_validation: dict[str, Any],
) -> dict[str, Any]:
    before = snapshot(paths)
    source, target = Markdown.read(paths["extracted"]), Markdown.read(paths["manual"])
    pdf = read_pdf(paths["raw"])
    source_mappings, target_mappings = align_pages(pdf, source), align_pages(pdf, target)
    pages = [
        audit_page(page, source, target, source_mapping, target_mapping)
        for page, source_mapping, target_mapping in zip(pdf, source_mappings, target_mappings)
    ]
    findings = {
        "extracted_structure": markdown_findings(source),
        "package_structure": markdown_findings(target),
        "extracted_to_package": feature_changes(source, target),
    }
    if snapshot(paths) != before:
        raise AuditError("Input files changed during the audit; rerun this document")
    counts = dict(Counter(page["status"] for page in pages))
    status = (
        "error" if counts.get("error") or package_validation["status"] == "error"
        else "review_required" if any(findings.values()) or counts.get("review_required")
        or counts.get("unverifiable") else "checked"
    )
    descriptor = json.loads(paths["descriptor"].read_text(encoding="utf-8"))
    return {
        "inputs": before, "source_url": descriptor.get("homepage"),
        "pdf_sha256": before["raw"]["sha256"], "status": status,
        "package_validation": package_validation, "page_count": len(pages),
        "page_status_counts": counts, "findings": findings, "pages": pages,
    }


def save_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def summary_for(document: dict[str, Any], filename: str) -> dict[str, Any]:
    return {
        key: document[key] for key in (
            "document_id", "status", "page_count", "page_status_counts",
            "source_url", "pdf_sha256",
        ) if key in document
    } | {
        "report": filename,
        "finding_counts": {
            name: len(items) for name, items in document.get("findings", {}).items()
        },
        "page_finding_counts": dict(Counter(
            finding["kind"] for page in document.get("pages", []) for finding in page["findings"]
        )),
        "priority_pages": sorted(
            [
                {"page": page["page"], "status": page["status"],
                 "package_lines": page["mappings"]["package"].get("markdown_lines"),
                 "high_priority_findings": sum(
                     finding.get("priority") == "high"
                     or finding["kind"] == "possible_missing_or_changed_prompt_command"
                     for finding in page["findings"]
                 ),
                 "finding_count": len(page["findings"])}
                for page in document.get("pages", []) if page["status"] != "checked"
            ],
            key=lambda page: (
                -page["high_priority_findings"],
                page["status"] not in {"error", "unverifiable"},
                -page["finding_count"], page["page"],
            ),
        )[:5],
        "error": document.get("error"),
    }


def write_summary(output: Path, manifest: dict[str, Any]) -> None:
    documents = manifest["documents"]
    counts: Counter[str] = Counter()
    for document in documents:
        counts.update(document.get("page_status_counts", {}))
    duplicate_groups: dict[str, list[str]] = defaultdict(list)
    for document in documents:
        if document.get("source_url"):
            duplicate_groups[document["source_url"]].append(document["document_id"])
    manifest.update(
        document_count=len(documents),
        page_count=sum(document.get("page_count", 0) for document in documents),
        page_status_counts=dict(counts),
        duplicate_sources=[
            {"url": url, "document_ids": ids}
            for url, ids in sorted(duplicate_groups.items()) if len(ids) > 1
        ],
    )
    save_json(output / "manifest.json", manifest)
    lines = [
        "# Local PDF / OKF corpus audit", "", NOTICE, "",
        f"- Run status: {manifest['status']}",
        f"- Documents: {len(documents)} / {manifest['expected_documents']}",
        f"- PDF pages (including duplicates): {manifest['page_count']}",
        f"- Page statuses: {json.dumps(dict(counts), sort_keys=True)}",
        "", "| Document | Status | Pages | Unverifiable | Page findings | Report |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for document in documents:
        lines.append(
            f"| {document['document_id']} | {document['status']} "
            f"| {document.get('page_count', 0)} "
            f"| {document.get('page_status_counts', {}).get('unverifiable', 0)} "
            f"| {sum(document['page_finding_counts'].values())} "
            f"| [JSON]({document['report']}) |"
        )
    finding_counts: Counter[str] = Counter()
    for document in documents:
        finding_counts.update(document["page_finding_counts"])
        finding_counts.update(document["finding_counts"])
    lines.extend(["", "## Finding counts", ""])
    lines.extend(f"- {kind}: {count}" for kind, count in sorted(finding_counts.items()))
    lines.extend([
        "", "## Review queue (up to five priority pages per document)", "",
        "All pages and all findings remain in each document JSON; this queue is only a shortlist.",
        "",
        "| Document | PDF page | Status | OKF lines (inferred context) | High priority |",
        "| --- | ---: | --- | --- | ---: |",
    ])
    for document in documents:
        for page in document["priority_pages"]:
            lines.append(
                f"| {document['document_id']} | {page['page']} | {page['status']} "
                f"| {page['package_lines']} | {page['high_priority_findings']} |"
            )
        if document.get("error"):
            lines.extend(["", f"ERROR {document['document_id']}: {document['error']}", ""])
    lines.extend(["", "## Duplicate source URLs", ""])
    for group in manifest["duplicate_sources"]:
        lines.append(f"- {group['url']}: {', '.join(group['document_ids'])}")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(root: Path, output: Path, resume: bool = False) -> dict[str, Any]:
    root = root.resolve()
    output = output.resolve()
    if output.is_relative_to(root):
        raise AuditError("Output must be outside the product input directory")
    document_ids = sorted({
        path.name for stage in ("raw", "extracted", "packages")
        for path in (root / stage).glob("*") if path.is_dir()
    })
    if not document_ids:
        raise AuditError(f"No documents found: {root}")
    fingerprint = snapshot({
        "audit": Path(__file__), "checker": Path(__file__).with_name("check_pdf_markdown_quality.py"),
        "validator": Path(__file__).with_name("validate_packages.py"),
    })
    dependencies = {name: version(name) for name in ("pypdfium2", "frictionless")}
    if resume:
        previous = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        if previous.get("data_root") != str(root) or previous.get("schema_version") != SCHEMA_VERSION:
            raise AuditError("Resume manifest does not match data root or schema")
    else:
        output.mkdir(parents=True, exist_ok=False)
    lock = output / ".audit.lock"
    with lock.open("x", encoding="utf-8"):
        pass
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "data_root": str(root), "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "notice": NOTICE, "tool_fingerprint": fingerprint,
        "dependencies": dependencies,
        "parameters": {"shingle_characters": SHINGLE, "stride": STRIDE,
                       "low_coverage_threshold": LOW_COVERAGE},
        "expected_documents": len(document_ids), "documents": [],
    }
    input_errors = (
        AuditError, OSError, UnicodeError, json.JSONDecodeError,
        PackageValidationError, pypdfium2.PdfiumError, FrictionlessException,
    )
    try:
        (output / "documents").mkdir(exist_ok=True)
        write_summary(output, manifest)
        for document_id in document_ids:
            filename = f"documents/{document_id}.json"
            destination = output / filename
            try:
                paths = document_inputs(root, document_id)
                try:
                    validation = {"status": "passed", **validate_package(paths["manual"].parent)}
                except (PackageValidationError, FrictionlessException) as error:
                    validation = {"status": "error", "error": str(error)}
                cached = None
                if resume and destination.is_file():
                    candidate = json.loads(destination.read_text(encoding="utf-8"))
                    if (
                        candidate.get("tool_fingerprint") == fingerprint
                        and candidate.get("dependencies") == dependencies
                        and candidate.get("inputs") == snapshot(paths)
                        and candidate.get("package_validation") == validation
                        and candidate.get("status") != "error"
                    ):
                        cached = candidate
                document = cached if cached is not None else audit_document(paths, validation)
                document.update(
                    document_id=document_id, tool_fingerprint=fingerprint,
                    dependencies=dependencies,
                )
                print(f"{document_id}: {document['status']}"
                      f" ({document.get('page_count', 0)} pages)"
                      f"{' [resumed]' if cached is not None else ''}", flush=True)
            except input_errors as error:
                document = {"document_id": document_id, "status": "error", "error": str(error)}
                print(f"{document_id}: ERROR: {error}", file=sys.stderr, flush=True)
            save_json(destination, document)
            manifest["documents"].append(summary_for(document, filename))
            write_summary(output, manifest)
        manifest["status"] = (
            "completed_with_errors" if any(d["status"] == "error" for d in manifest["documents"])
            else "completed"
        )
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_summary(output, manifest)
    finally:
        lock.unlink()
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data/products/catalyst9300"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        manifest = run_audit(args.data_root, args.output_dir, args.resume)
    except (
        AuditError, OSError, UnicodeError, json.JSONDecodeError, PackageValidationError,
    ) as error:
        print(f"Corpus audit error: {error}", file=sys.stderr)
        return 1
    print(f"Audit {manifest['status']}: {manifest['document_count']} documents, "
          f"{manifest['page_count']} pages; {args.output_dir / 'summary.md'}")
    if manifest["status"] != "completed":
        return 1
    return 2 if any(d["status"] != "checked" for d in manifest["documents"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
