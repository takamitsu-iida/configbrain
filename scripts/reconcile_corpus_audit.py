"""Reconcile audited CLI and table candidates against local PDF text coordinates."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import asdict, replace
import json
from pathlib import Path
import re
import sys
from typing import Any

import pypdfium2

if __package__:
    from .audit_corpus import compact, save_json, snapshot, visible_line
    from .check_pdf_markdown_quality import (
        FENCE, PROMPT, PdfLine, QualityError, commands_in_line, pdf_commands, pdf_lines, table_cells,
    )
else:
    from audit_corpus import compact, save_json, snapshot, visible_line
    from check_pdf_markdown_quality import (
        FENCE, PROMPT, PdfLine, QualityError, commands_in_line, pdf_commands, pdf_lines, table_cells,
    )


PAGE_ANCHOR = re.compile(r'<span\s+id=["\']page-(\d+)-[^"\']+["\']\s*></span>')
NOTICE = (
    "All selected audit candidates are reconciled locally, without API calls or repairs. "
    "Verdicts concern the recorded example text or Markdown serialization, not semantic "
    "correctness. A short table row can be valid Markdown with implicit empty cells; "
    "column-count variation alone does not prove content loss. "
    "PDF command continuations are inferred from coordinates. Heading/page IDs and the "
    "audit's inferred ranges bound searches; uncertain correspondence remains unresolved. "
    "Finding matching text elsewhere in the manual does not dismiss a candidate. "
    "PDF text coordinates are not a substitute for visual review of ambiguous cells."
)


def cli_text(value: str) -> str:
    value = visible_line(value)
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = value.replace("`", "").replace("**", "").replace(r"\|", "|")
    return value


def cli_key(value: str) -> str:
    return re.sub(r"\s+", "", cli_text(value))


def contains_command(value: str, command: str) -> bool:
    key = cli_key(command)
    if not key:
        return False
    pattern = r"(?<![\w./:-])" + r"\s*".join(re.escape(character) for character in key) + r"(?![\w./:-])"
    return re.search(pattern, cli_text(value)) is not None


def headings(lines: list[str]) -> list[dict[str, Any]]:
    result = []
    for number, line in enumerate(lines, 1):
        if not re.match(r"^#{1,6}\s+", line):
            continue
        markers = PAGE_ANCHOR.findall(line)
        if markers:
            title = visible_line(line.lstrip("# ")).replace("**", "").strip()
            result.append({"line": number, "page": int(markers[0]) + 1, "title": title})
    return result


def command_scope(
    lines: list[str], page: int, source_lines: list[PdfLine], source: PdfLine,
    fallback: list[int],
) -> dict[str, Any]:
    markers = headings(lines)
    local = [heading for heading in markers if heading["page"] == page]
    preceding, following = [], []
    for heading in local:
        key = compact(heading["title"])
        matches = [
            line for line in source_lines
            if len(key) >= 12 and key in compact(line.text)
            and line.top < 740 and line.bottom > 45
        ]
        if len(matches) != 1:
            continue
        if matches[0].bottom > source.top:
            preceding.append(heading)
        elif matches[0].top < source.bottom:
            following.append(heading)
    before = max(preceding, key=lambda heading: heading["line"], default=None)
    after = min(following, key=lambda heading: heading["line"], default=None)
    if before is None and after is not None:
        before = next(
            (heading for heading in reversed(markers)
             if heading["line"] < after["line"] and heading["page"] < page), None,
        )
    if after is None and before is not None:
        after = next(
            (heading for heading in markers
             if heading["line"] > before["line"] and heading["page"] > page), None,
        )
    if before is not None and after is not None and before["line"] < after["line"]:
        return {
            "lines": [before["line"], after["line"] - 1],
            "method": "page_heading_bounded", "before": before, "after": after,
        }
    earlier = [heading for heading in markers if heading["page"] < page]
    later = [heading for heading in markers if heading["page"] > page]
    before = max(earlier, key=lambda heading: heading["page"], default=None)
    if before is not None:
        after = min(
            (heading for heading in later if heading["line"] > before["line"]),
            key=lambda heading: heading["page"], default=None,
        )
        if after is not None:
            return {
                "lines": [before["line"], after["line"] - 1],
                "method": "page_marker_bracketed", "before": before, "after": after,
            }
    return {"lines": fallback, "method": "audit_inferred_context"}


def text_matches(
    lines: list[str], selected: list[int], command: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    key = cli_key(command)
    if not key:
        return [], []
    intact, fragmented = [], []
    first, last = selected
    for number in range(first, min(last, len(lines)) + 1):
        raw = lines[number - 1]
        if FENCE.match(raw):
            continue
        cells = table_cells(raw) if raw.lstrip().startswith("|") else [raw]
        matching = [
            cell for cell in cells if contains_command(cell, command) and (
                re.search(PROMPT, cli_text(cell))
                or cli_key(re.sub(r"\bStep\s+\d+\b", "", cell)) == key
                or any(contains_command(code, command) for code in re.findall(r"`([^`]+)`", cell))
            )
        ]
        if matching:
            intact.append({
                "line": number, "text": raw,
                "prompt_present": any(re.search(PROMPT, cli_text(cell)) for cell in matching),
            })
        elif (
            len(cells) > 1 and re.search(PROMPT, cli_text(" ".join(cells)))
            and contains_command(" ".join(cells), command)
        ):
            fragmented.append({"line": number, "text": raw})
    prompted = [match for match in intact if match["prompt_present"]]
    if prompted or fragmented:
        return prompted, fragmented
    if intact:
        return intact, []
    logical: list[tuple[int, str, bool]] = []
    for number in range(first, min(last, len(lines)) + 1):
        raw = lines[number - 1]
        if FENCE.match(raw) or not raw.strip():
            continue
        is_table = raw.lstrip().startswith("|")
        cells = table_cells(raw) if is_table else [re.sub(r"^#{1,6}\s+", "", raw)]
        if is_table and any(FENCE.match(cell) for cell in cells):
            continue
        logical.append((number, " ".join(cells), is_table))
    for index, (number, raw, is_table) in enumerate(logical):
        if not re.search(PROMPT, cli_text(raw)):
            continue
        for width in range(2, min(8, len(logical) - index) + 1):
            selected_lines = logical[index:index + width]
            following = selected_lines[-1]
            if following[0] - number > 16:
                break
            joined = " ".join(value for _, value, _ in selected_lines)
            starts_here = not contains_command(
                " ".join(value for _, value, _ in selected_lines[1:]), command,
            )
            if starts_here and contains_command(joined, command):
                match = {
                    "line": number, "end_line": following[0], "text": joined,
                    "prompt_present": True, "multiline": True,
                }
                if is_table or any(table for _, _, table in selected_lines):
                    fragmented.append(match)
                else:
                    intact.append(match)
                break
    return intact, fragmented


def source_command(
    lines: list[PdfLine], candidate: str,
) -> tuple[PdfLine | None, dict[str, Any] | None]:
    matches = [line for line in lines if candidate in commands_in_line(line.text)]
    if len(matches) != 1:
        return None, None
    original = matches[0]
    clipped = [
        replace(line, text=line.command_column_text or line.text)
        for line in lines
    ]
    index = lines.index(original)
    records = pdf_commands([clipped[index]] + [
        line for i, line in enumerate(clipped) if i != index
        and line.top < original.bottom and abs(line.x - original.x) <= 4
        and 0 <= original.bottom - line.top <= 7
    ])
    first = commands_in_line(clipped[index].text)
    if len(first) != 1 or not records:
        return original, None
    return original, records[0]


def reconcile_command(
    page: dict[str, Any], finding: dict[str, Any], source_lines: list[PdfLine],
    manual: list[str], extracted: list[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "command", "pdf_page": page["page"], "candidate": finding,
        "verdict": "unresolved", "reason": "Source command occurrence is ambiguous",
    }
    original, record = source_command(source_lines, finding["command"])
    if original is None:
        result["pdf_source_candidates"] = [
            asdict(line) for line in source_lines
            if finding["command"] in commands_in_line(line.text)
        ]
        return result
    result["pdf_line"] = asdict(original)
    if record is None:
        return result
    result["pdf_command"] = record
    if re.search(r"\b(?:Port|Switch)#\s+(?:Port|Port#)", original.text):
        result.update(
            verdict="audit_false_positive", reason="output_header_misidentified_as_prompt",
        )
        return result
    scope = command_scope(manual, page["page"], source_lines, original, finding["markdown_lines"])
    source_fallback = page["mappings"]["extracted"].get("markdown_lines", [1, len(extracted)])
    extracted_scope = command_scope(
        extracted, page["page"], source_lines, original, source_fallback,
    )
    result.update(package_scope=scope, extracted_scope=extracted_scope)
    intact, fragmented = text_matches(manual, scope["lines"], record["command"])
    result["package_matches"] = intact
    result["fragmented_package_matches"] = fragmented
    source_intact, source_fragmented = text_matches(
        extracted, extracted_scope["lines"], record["command"],
    )
    result["extracted_matches"] = source_intact + source_fragmented
    alternative = (
        text_matches(manual, finding["markdown_lines"], record["command"])
        if scope["lines"] != finding["markdown_lines"] else ([], [])
    )
    result["alternative_context_matches"] = alternative[0] + alternative[1]
    clipped = record["first_line"] != finding["command"]
    if len(intact) == 1:
        if intact[0]["prompt_present"]:
            result.update(
                verdict="audit_false_positive",
                reason="purpose_column_contamination" if clipped else "formatting_or_range_comparison",
            )
        else:
            result.update(
                verdict="confirmed_formatting_difference", reason="example_prompt_or_block_structure_lost",
            )
    elif not intact and len(fragmented) == 1:
        result.update(
            verdict="confirmed_formatting_difference",             reason="command_split_across_table_cells_or_rows",
        )
    elif not intact and not fragmented:
        first_matches = text_matches(manual, scope["lines"], record["first_line"])
        meaningful = len(record["first_line"].split()) >= 2 and len(cli_key(record["first_line"])) >= 6
        if (
            meaningful and scope["method"] == "page_heading_bounded"
            and original.command_column_text
            and not any(first_matches) and not result["alternative_context_matches"]
        ):
            result.update(
                verdict="confirmed_text_difference",
                reason="command_example_absent_in_corresponding_section",
                likely_stage=(
                    "extracted_to_package" if result["extracted_matches"]
                    else "pdf_to_extracted" if extracted_scope["method"] in {
                        "page_heading_bounded", "page_marker_bracketed",
                    }
                    else "undetermined"
                ),
            )
        elif result["alternative_context_matches"]:
            result["reason"] = "Example exists outside heading scope; anchor displacement or occurrence ambiguity"
        else:
            result["reason"] = "No exact command match; boundaries or continuation remain uncertain"
    else:
        result["reason"] = "Multiple matching examples; the occurrence cannot be identified uniquely"
    return result


def table_extent(lines: list[str], first: int) -> list[int]:
    last = first + 1
    while last < len(lines) and "|" in lines[last] and lines[last].strip():
        last += 1
    return [first, last]


def table_pdf_pages(pages: list[dict[str, Any]], selected: list[int]) -> list[int]:
    first, last = selected
    strong, context = [], []
    for page in pages:
        mapping = page["mappings"]["package"]
        for key, destination in (("anchor_lines", strong), ("markdown_lines", context)):
            bounds = mapping.get(key)
            if bounds and bounds[0] <= last and bounds[1] >= first:
                destination.append(page["page"])
    return strong or context


def reconcile_table(
    finding: dict[str, Any], manual: list[str], pdf: dict[int, list[PdfLine]],
    selected_pages: list[int],
) -> dict[str, Any]:
    selected = table_extent(manual, finding["line"])
    rows = []
    matched_pages: set[int] = set()
    for item in finding["row_width_mismatches"]:
        raw = manual[item["line"] - 1]
        cells = table_cells(raw)
        category = (
            "embedded_code_fence" if "```" in raw or "~~~" in raw
            else "empty_stub" if not raw.strip("| ")
            else "extra_cells" if item["columns"] > finding["columns"]
            else "short_row"
        )
        evidence = []
        for cell in cells:
            value = compact(visible_line(cell))
            if len(value) < 20:
                continue
            for number in selected_pages:
                for line in pdf.get(number, []):
                    if value in compact(line.text):
                        evidence.append({"page": number, "line": asdict(line), "cell": cell})
                        matched_pages.add(number)
                        break
        rows.append({
            **item, "text": raw, "classification": category,
            "source_cell_evidence": evidence,
        })
    context_matches = []
    for raw in manual[selected[0] - 1:selected[1]]:
        cells = table_cells(raw)
        for cell in cells + [" ".join(cells)]:
            value = compact(visible_line(cell))
            if len(value) < 24:
                continue
            for number in selected_pages:
                source = compact(" ".join(line.text for line in pdf.get(number, [])))
                if value[:48] in source:
                    matched_pages.add(number)
                    context_matches.append({
                        "page": number, "markdown_cell": cell, "matched_fragment": value[:48],
                    })
    return {
        "kind": "table", "verdict": "confirmed_column_count_variation",
        "reason": "Markdown row cell counts differ from the header; this alone does not prove text loss",
        "markdown_lines": selected, "candidate": finding,
        "pdf_page_candidates": selected_pages, "pdf_matched_pages": sorted(matched_pages),
        "source_correspondence": "cell_text_matched" if matched_pages else "unresolved",
        "source_context_matches": context_matches,
        "row_classifications": dict(Counter(row["classification"] for row in rows)),
        "rows": rows,
        "source_page_excerpts": [
            {"page": number, "lines": [asdict(line) for line in pdf.get(number, [])]}
            for number in selected_pages
        ],
    }


def reconcile_document(report: dict[str, Any]) -> dict[str, Any]:
    paths = {name: Path(value["path"]) for name, value in report["inputs"].items()}
    if snapshot(paths) != report["inputs"]:
        raise QualityError("Inputs differ from the audit; rerun the audit before reconciliation")
    manual = paths["manual"].read_text(encoding="utf-8").splitlines()
    extracted = paths["extracted"].read_text(encoding="utf-8").splitlines()
    command_candidates = [
        (page, index, finding)
        for page in report["pages"] for index, finding in enumerate(page["findings"])
        if finding["kind"] == "possible_missing_or_changed_prompt_command"
    ]
    table_candidates = [
        (index, finding) for index, finding in enumerate(report["findings"]["package_structure"])
        if finding["kind"] == "inconsistent_table_columns"
    ]
    required = {page["page"] for page, _, _ in command_candidates}
    table_pages = {
        index: sorted({
            neighbor
            for page in table_pdf_pages(report["pages"], table_extent(manual, finding["line"]))
            for neighbor in (page - 1, page, page + 1)
            if 1 <= neighbor <= report["page_count"]
        })
        for index, finding in table_candidates
    }
    required.update(number for numbers in table_pages.values() for number in numbers)
    pdf_data = paths["raw"].read_bytes()
    evidence, errors = {}, {}
    for number in sorted(required):
        try:
            evidence[number], sparse = pdf_lines(pdf_data, (number, number))
            if sparse:
                errors[number] = "Insufficient PDF text layer"
        except QualityError as error:
            evidence[number] = []
            errors[number] = str(error)
    results = []
    for page, index, finding in command_candidates:
        item = reconcile_command(page, finding, evidence[page["page"]], manual, extracted)
        item["audit_pointer"] = f"/pages/{page['page'] - 1}/findings/{index}"
        if page["page"] in errors:
            item.update(verdict="unresolved", reason=errors[page["page"]])
        results.append(item)
    for index, finding in table_candidates:
        item = reconcile_table(finding, manual, evidence, table_pages[index])
        item["audit_pointer"] = f"/findings/package_structure/{index}"
        results.append(item)
    if snapshot(paths) != report["inputs"]:
        raise QualityError("Inputs changed during reconciliation")
    return {
        "document_id": report["document_id"], "inputs": report["inputs"],
        "command_candidates": len(command_candidates), "table_candidates": len(table_candidates),
        "verdict_counts": dict(Counter(item["verdict"] for item in results)),
        "pdf_pages_read": sorted(required), "pdf_page_errors": errors, "results": results,
    }


def render_summary(manifest: dict[str, Any], documents: list[dict[str, Any]]) -> str:
    lines = [
        "# コマンド例・表の列不整合の全件照合", "", NOTICE, "",
        f"- 対象文書: {len(documents)}",
        f"- コマンド候補: {manifest['command_candidates']}",
        f"- 表候補: {manifest['table_candidates']}",
        f"- 判定: `{json.dumps(manifest['verdict_counts'], ensure_ascii=False)}`",
        f"- 表とPDF本文の対応: `{json.dumps(manifest['table_source_correspondence'], ensure_ascii=False)}`",
        f"- 表の行分類（重複あり）: `{json.dumps(manifest['table_row_classifications'], ensure_ascii=False)}`",
        "",
        "[全候補の一覧CSV](review_queue.csv)には誤検出と保留を含む全件を保存しています。",
        "", "## 判定の意味", "",
        "- `audit_false_positive`: 対応範囲に例文を確認。説明列の混入等による検出候補。",
        "- `confirmed_formatting_difference`: コマンドが複数セル・行へ分断、または例のプロンプトを喪失。",
        "- `confirmed_text_difference`: PDFのコマンド例を対応セクション内に確認できない。",
        "- `confirmed_column_count_variation`: 表の列数の揺れを確認。内容欠落の確定ではない。",
        "- `unresolved`: 対応範囲・出現箇所・折り返し等が曖昧で判断保留。",
        "", "短い行はMarkdownの空セル補完により表示できる場合があります。",
        "コードフェンス混入・余剰セルと、空セル省略を区別して詳細JSONへ記録しています。",
        "", "## 文書別結果", "",
        "| 文書 | コマンド | 表 | 詳細 |", "| --- | ---: | ---: | --- |",
    ]
    for document in documents:
        name = document["document_id"]
        lines.append(
            f"| {name} | {document['command_candidates']} | {document['table_candidates']} "
            f"| [JSON](documents/{name}.json) |"
        )
    lines.extend(["", "## コマンド例の本文相違", "",
                  "セル分断・誤検出・判断保留の全件詳細はCSVと文書別JSONを参照してください。", ""])
    for document in documents:
        for item in document["results"]:
            if item["kind"] != "command" or item["verdict"] != "confirmed_text_difference":
                continue
            command = item.get("pdf_command", {}).get("command", item["candidate"]["command"])
            lines.extend([
                f"### {document['document_id']} / PDF {item['pdf_page']}ページ",
                "",
                f"- 判定: `{item['verdict']}` / `{item['reason']}`",
                f"- PDF: `{command.replace('`', '')}`",
                f"- OKF行: {item.get('package_scope', {}).get('lines', item['candidate']['markdown_lines'])}",
                f"- 原因段階の候補: `{item.get('likely_stage', 'undetermined')}`",
                "",
            ])
    return "\n".join(lines) + "\n"


def write_review_queue(path: Path, documents: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[
            "document_id", "audit_pointer", "kind", "verdict", "pdf_pages",
            "markdown_lines", "reason", "pdf_command", "table_source_correspondence",
        ])
        writer.writeheader()
        for document in documents:
            for item in document["results"]:
                writer.writerow({
                    "document_id": document["document_id"],
                    "audit_pointer": item["audit_pointer"], "kind": item["kind"],
                    "verdict": item["verdict"],
                    "pdf_pages": json.dumps(
                        [item["pdf_page"]] if item["kind"] == "command"
                        else item["pdf_page_candidates"],
                    ),
                    "markdown_lines": json.dumps(
                        item.get("package_scope", {}).get("lines", [])
                        if item["kind"] == "command" else item["markdown_lines"],
                    ),
                    "reason": item["reason"],
                    "pdf_command": item.get("pdf_command", {}).get(
                        "command", item["candidate"].get("command", ""),
                    ),
                    "table_source_correspondence": item.get("source_correspondence", ""),
                })


def run_reconciliation(audit_dir: Path, output: Path) -> dict[str, Any]:
    audit_dir, output = audit_dir.resolve(), output.resolve()
    manifest_path = audit_dir / "manifest.json"
    manifest_before = snapshot({"manifest": manifest_path})
    audit_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if audit_manifest.get("status") != "completed":
        raise QualityError("The audit must be completed without input errors")
    if output.is_relative_to(Path(audit_manifest["data_root"]).resolve()) or output.is_relative_to(audit_dir):
        raise QualityError("Output must be outside audit and product inputs")
    output.mkdir(parents=True, exist_ok=False)
    (output / "documents").mkdir()
    documents = []
    report_hashes = {}
    for item in audit_manifest["documents"]:
        path = audit_dir / item["report"]
        before = snapshot({"report": path})
        report = json.loads(path.read_text(encoding="utf-8"))
        document = reconcile_document(report)
        if snapshot({"report": path}) != before:
            raise QualityError(f"Audit report changed during reconciliation: {path}")
        report_hashes[item["document_id"]] = before["report"]
        save_json(output / "documents" / f"{item['document_id']}.json", document)
        documents.append(document)
        print(f"{item['document_id']}: {document['command_candidates']} commands, "
              f"{document['table_candidates']} tables, {document['verdict_counts']}", flush=True)
    if snapshot({"manifest": manifest_path}) != manifest_before:
        raise QualityError("Audit manifest changed during reconciliation")
    verdicts = Counter(item["verdict"] for document in documents for item in document["results"])
    manifest = {
        "schema_version": 1, "status": "completed", "notice": NOTICE,
        "audit_manifest": manifest_before["manifest"], "audit_reports": report_hashes,
        "tool": snapshot({"reconciliation": Path(__file__)}),
        "command_candidates": sum(d["command_candidates"] for d in documents),
        "table_candidates": sum(d["table_candidates"] for d in documents),
        "verdict_counts": dict(verdicts),
        "table_source_correspondence": dict(Counter(
            item["source_correspondence"] for document in documents
            for item in document["results"] if item["kind"] == "table"
        )),
        "table_row_classifications": dict(sum(
            (Counter(item["row_classifications"]) for document in documents
             for item in document["results"] if item["kind"] == "table"), Counter(),
        )),
        "documents": [
            {"document_id": d["document_id"], "report": f"documents/{d['document_id']}.json",
             "verdict_counts": d["verdict_counts"]} for d in documents
        ],
    }
    save_json(output / "manifest.json", manifest)
    write_review_queue(output / "review_queue.csv", documents)
    (output / "summary.md").write_text(render_summary(manifest, documents), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_reconciliation(args.audit_dir, args.output_dir)
    except (OSError, UnicodeError, json.JSONDecodeError, QualityError, pypdfium2.PdfiumError) as error:
        print(f"Reconciliation error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result["verdict_counts"], ensure_ascii=False))
    return 2 if any(key != "audit_false_positive" for key in result["verdict_counts"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
