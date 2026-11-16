"""Report possible PDF-to-Markdown table and CLI example discrepancies."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import pypdfium2


class QualityError(ValueError):
    """Raised when an input cannot be compared."""


PROMPT = r"[A-Za-z][A-Za-z0-9_.-]*(?:\([^()\n]+\))?[>#]"
PROMPT_COMMAND = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?P<prompt>{PROMPT})\s*(?P<command>.*?)"
    rf"(?=\s+(?:Step\s+\d+\b|Example:|{PROMPT})|[|`\n]|$)"
)
STEP = re.compile(r"\bStep\s+(\d+)\b", re.IGNORECASE)
SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
HEADER = re.compile(r"\bCommand\s+or\s+Action\s+Purpose\b", re.IGNORECASE)


@dataclass(frozen=True)
class PdfLine:
    page: int
    text: str
    x: float
    top: float
    bottom: float
    command_column_text: str = ""


def parse_range(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+):(\d+)", value)
    if not match or int(match[1]) < 1 or int(match[1]) > int(match[2]):
        raise argparse.ArgumentTypeError("範囲は1始まりの開始:終了で指定してください")
    return int(match[1]), int(match[2])


def read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise QualityError(f"ファイルを読み取れません: {path}: {error}") from error


def pdf_lines(data: bytes, pages: tuple[int, int]) -> tuple[list[PdfLine], list[int]]:
    try:
        document = pypdfium2.PdfDocument(data)
    except (pypdfium2.PdfiumError, ValueError) as error:
        raise QualityError(f"PDFを開けません: {error}") from error
    try:
        if pages[1] > len(document):
            raise QualityError(f"PDFのページ範囲が文書のページ数({len(document)})を超えています")
        lines: list[PdfLine] = []
        sparse_pages: list[int] = []
        for page_number in range(pages[0], pages[1] + 1):
            page = document[page_number - 1]
            try:
                text_page = page.get_textpage()
                try:
                    text = text_page.get_text_range()
                    if len(text) != text_page.count_chars():
                        raise QualityError(
                            f"PDFページ{page_number}の文字位置を対応付けできません"
                        )
                    if sum(not character.isspace() for character in text) < 50:
                        sparse_pages.append(page_number)
                    page_lines: list[tuple[str, list[tuple[float, float, float, float]]]] = []
                    offset = 0
                    for part in text.splitlines(keepends=True):
                        content = part.rstrip("\r\n")
                        boxes = [text_page.get_charbox(offset + index) for index in range(len(content))]
                        offset += len(part)
                        if not content.strip():
                            continue
                        page_lines.append((content, boxes))
                    column_bounds = None
                    for content, boxes in page_lines:
                        header = HEADER.search(content)
                        if header:
                            purpose = re.search(r"\bPurpose\b", header.group(), re.IGNORECASE)
                            if purpose:
                                column_bounds = (
                                    boxes[header.start()][0],
                                    boxes[header.start() + purpose.start()][0],
                                )
                                break
                    for content, boxes in page_lines:
                        visible = [box for character, box in zip(content, boxes) if not character.isspace()]
                        column_text = ""
                        if column_bounds:
                            column_text = "".join(
                                character for character, box in zip(content, boxes)
                                if column_bounds[0] - 1 <= box[0] < column_bounds[1] - 1
                            ).strip()
                        lines.append(PdfLine(
                            page_number, content.strip(),
                            min(box[0] for box in visible),
                            max(box[3] for box in visible),
                            min(box[1] for box in visible),
                            column_text,
                        ))
                finally:
                    text_page.close()
            finally:
                page.close()
        return lines, sparse_pages
    except pypdfium2.PdfiumError as error:
        raise QualityError(f"PDFのテキストを読み取れません: {error}") from error
    finally:
        document.close()


def normalize_command(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = value.replace("**", "").replace("`", "")
    value = re.sub(r"(?<!\w)\*([^*]+)\*(?!\w)", r"\1", value)
    return " ".join(value.split())


def commands_in_line(text: str) -> list[str]:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    return [
        command for match in PROMPT_COMMAND.finditer(text)
        if (command := normalize_command(match.group("command")))
    ]


def pdf_commands(lines: list[PdfLine]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for line in lines:
        source_text = line.command_column_text if commands_in_line(line.command_column_text) else line.text
        for command in commands_in_line(source_text):
            continuations = [
                candidate for candidate in lines
                if candidate.page == line.page
                and abs(candidate.x - line.x) <= 4
                and 0 <= line.bottom - candidate.top <= 7
                and not commands_in_line(candidate.text)
            ]
            continuation = min(
                continuations, key=lambda candidate: line.bottom - candidate.top,
                default=None,
            )
            full = normalize_command(f"{command} {continuation.text}") if continuation else command
            commands.append({
                "command": full,
                "first_line": command,
                "page": line.page,
                "continuation_uncertain": continuation is not None,
            })
    return commands


def markdown_commands(lines: list[str], first_line: int) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    fence_marker: str | None = None
    fence_lines: list[tuple[int, str]] = []
    fence_language = ""

    def save_fence() -> None:
        cli_fence = fence_language.casefold() in {"console", "cli", "ios", "cisco"}
        cli_fence = cli_fence or any(commands_in_line(text) for _, text in fence_lines)
        for number, text in fence_lines:
            found = commands_in_line(text)
            if found:
                commands.extend({"command": value, "line": number, "kind": "prompt"} for value in found)
            elif cli_fence and text.strip() and not text.lstrip().startswith(("!", "#")):
                commands.append({
                    "command": normalize_command(text),
                    "line": number,
                    "kind": "unprompted_fence_line",
                })

    for number, line in enumerate(lines, start=first_line):
        fence = FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if fence_marker is None:
                fence_marker = marker
                fence_language = fence.group(2).strip()
                fence_lines = []
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                save_fence()
                fence_marker = None
                fence_lines = []
            else:
                fence_lines.append((number, line))
            continue
        if fence_marker is not None:
            fence_lines.append((number, line))
        else:
            commands.extend(
                {"command": value, "line": number, "kind": "prompt"}
                for value in commands_in_line(line)
            )
    if fence_marker is not None:
        raise QualityError("選択したMarkdown範囲に閉じていないコードフェンスがあります")
    return commands


def table_cells(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith(r"\|"):
        text = text[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", text)]


def markdown_tables(lines: list[str], first_line: int) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    in_fence: str | None = None
    index = 0
    while index < len(lines):
        fence = FENCE.match(lines[index])
        if fence:
            marker = fence.group(1)
            if in_fence is None:
                in_fence = marker
            elif marker[0] == in_fence[0] and len(marker) >= len(in_fence):
                in_fence = None
            index += 1
            continue
        if in_fence or index + 1 >= len(lines) or "|" not in lines[index]:
            index += 1
            continue
        separator = table_cells(lines[index + 1])
        if "|" not in lines[index + 1] or not separator or not all(
            SEPARATOR_CELL.fullmatch(cell) for cell in separator
        ):
            index += 1
            continue
        header = table_cells(lines[index])
        rows: list[tuple[int, list[str]]] = []
        next_index = index + 2
        while next_index < len(lines) and "|" in lines[next_index] and lines[next_index].strip():
            rows.append((first_line + next_index, table_cells(lines[next_index])))
            next_index += 1
        command_columns = [
            column for column, cell in enumerate(header)
            if re.fullmatch(r"Command\s+or\s+Action", normalize_command(cell), re.IGNORECASE)
        ]
        step_rows: list[dict[str, Any]] = []
        if len(command_columns) == 1:
            command_column = command_columns[0]
            for number, cells in rows:
                if command_column >= len(cells):
                    continue
                steps = [
                    match.group(1)
                    for cell in cells
                    for match in STEP.finditer(cell)
                ]
                if len(steps) == 1:
                    step_rows.append({
                        "step": steps[0], "line": number, "command_cell": cells[command_column],
                    })
        tables.append({
            "line": first_line + index,
            "header": header,
            "columns": len(header),
            "separator_columns": len(separator),
            "row_width_mismatches": [
                {"line": number, "columns": len(cells)}
                for number, cells in rows if len(cells) != len(header)
            ],
            "step_rows": step_rows,
        })
        index = next_index
    return tables


def build_report(
    pdf: list[PdfLine], sparse_pages: list[int], markdown: list[str], first_line: int,
) -> dict[str, Any]:
    source_commands = pdf_commands(pdf)
    output_commands = markdown_commands(markdown, first_line)
    source_counts = Counter(item["command"] for item in source_commands)
    output_counts = Counter(item["command"] for item in output_commands)
    command_findings: list[dict[str, Any]] = []
    for command in sorted(source_counts.keys() | output_counts.keys()):
        source_count, output_count = source_counts[command], output_counts[command]
        if source_count == output_count:
            continue
        partial_source = [
            item for item in source_commands
            if item["continuation_uncertain"] and item["first_line"] == command
        ]
        command_findings.append({
            "kind": (
                "possible_truncated_continuation" if partial_source
                else "missing_or_truncated" if source_count > output_count
                else "added_or_duplicated"
            ),
            "command": command,
            "pdf_count": source_count,
            "markdown_count": output_count,
            "pdf_pages": sorted({
                item["page"] for item in source_commands
                if item["command"] == command or item in partial_source
            }),
            "markdown_lines": [item["line"] for item in output_commands if item["command"] == command],
            "continuation_uncertain": bool(partial_source) or any(
                item["continuation_uncertain"] for item in source_commands if item["command"] == command
            ),
        })
    tables = markdown_tables(markdown, first_line)
    table_findings: list[dict[str, Any]] = []
    header_pages = sorted({line.page for line in pdf if HEADER.search(line.text)})
    syntax_candidates = []
    normalized_markdown = normalize_command(" ".join(markdown).replace("|", " "))
    for step in pdf:
        if not re.fullmatch(r"Step\s+\d+", step.text, re.IGNORECASE):
            continue
        matching = [
            line for line in pdf
            if line.page == step.page and abs(line.top - step.top) <= 4
            and line.command_column_text and not HEADER.search(line.command_column_text)
        ]
        if not matching:
            continue
        line = min(matching, key=lambda value: abs(value.top - step.top))
        syntax = normalize_command(line.command_column_text)
        if not syntax:
            continue
        pattern = rf"(?<![A-Za-z0-9_.:/-]){re.escape(syntax)}(?![A-Za-z0-9_.:/-])"
        found = re.search(pattern, normalized_markdown) is not None
        syntax_candidates.append({
            "step": step.text, "syntax": syntax, "page": step.page,
            "text_found_in_markdown": found,
        })
        if not found:
            table_findings.append({
                "kind": "possible_missing_table_command", "step": step.text,
                "syntax": syntax, "pdf_page": step.page,
            })
    if header_pages and not tables:
        table_findings.append({"kind": "pdf_table_without_markdown_table", "pdf_pages": header_pages})
    for table in tables:
        if table["separator_columns"] != table["columns"] or table["row_width_mismatches"]:
            table_findings.append({"kind": "inconsistent_column_count", **table})
        if any(HEADER.search(cell) for cell in table["header"]):
            table_findings.append({"kind": "merged_command_purpose_header", "line": table["line"]})
        if header_pages and table["columns"] > 3:
            table_findings.append({
                "kind": "possible_fragmented_procedure_table", "line": table["line"],
                "columns": table["columns"], "pdf_pages": header_pages,
            })
        for row in table["step_rows"]:
            for source in syntax_candidates:
                match = STEP.search(source["step"])
                if match is None or match.group(1) != row["step"]:
                    continue
                syntax = source["syntax"]
                pattern = rf"(?<![A-Za-z0-9_.:/-]){re.escape(syntax)}(?![A-Za-z0-9_.:/-])"
                if not re.search(pattern, normalize_command(row["command_cell"])):
                    table_findings.append({
                        "kind": "possible_step_command_mismatch", "line": row["line"],
                        "step": row["step"], "pdf_page": source["page"],
                        "pdf_syntax": syntax, "markdown_command_cell": row["command_cell"],
                    })
    pdf_step_order = STEP.findall(" ".join(line.text for line in pdf))
    md_step_order = STEP.findall(" ".join(line.replace("|", " ") for line in markdown))
    pdf_steps = Counter(pdf_step_order)
    md_steps = Counter(md_step_order)
    step_findings = [
        {"step": number, "pdf_count": pdf_steps[number], "markdown_count": md_steps[number]}
        for number in sorted(pdf_steps.keys() | md_steps.keys(), key=int)
        if pdf_steps[number] != md_steps[number]
    ]
    if pdf_steps == md_steps and len(pdf_step_order) > 1 and pdf_step_order != md_step_order:
        step_findings.append({
            "kind": "possible_reordered_steps",
            "pdf_order": pdf_step_order,
            "markdown_order": md_step_order,
        })
    limitations = []
    if sparse_pages:
        limitations.append(f"テキストレイヤーが50文字未満のPDFページ: {sparse_pages}")
    if any(item["continuation_uncertain"] for item in source_commands):
        limitations.append("PDFのコマンド例の折り返しを座標から推定しています")
    if not source_commands:
        limitations.append("PDF側にプロンプト付きコマンド例を検出できませんでした")
    if not header_pages:
        limitations.append("PDF側で比較対象の手順表の見出しを検出できませんでした")
    elif not syntax_candidates:
        limitations.append("PDF側の手順表からコマンド構文を抽出できませんでした")
    return {
        "status": "review_required" if command_findings or table_findings or step_findings or limitations
        else "no_anomalies_detected",
        "pdf_commands": source_commands,
        "markdown_commands": output_commands,
        "command_findings": command_findings,
        "pdf_table_syntax_candidates": syntax_candidates,
        "markdown_tables": tables,
        "table_findings": table_findings,
        "step_findings": step_findings,
        "limitations": limitations,
        "notice": (
            "検出結果は要確認の候補です。PDFのテキストレイヤー、プロンプト付き例、"
            "Markdownの表・CLIコードフェンスを用いた限定的な検査であり、"
            "表の意味、プロンプトのないPDFコマンド、画像や完全性は保証しません。"
            "対象範囲の対応、折り返し、誤検出と未検出は元PDFで確認してください。"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--pdf-pages", required=True, type=parse_range, metavar="START:END")
    parser.add_argument("--markdown-lines", required=True, type=parse_range, metavar="START:END")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        print(f"出力先は既に存在します: {args.output}", file=sys.stderr)
        return 1
    try:
        pdf_data = read_bytes(args.pdf)
        md_data = read_bytes(args.markdown)
        try:
            md_lines = md_data.decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise QualityError("MarkdownはUTF-8で指定してください") from error
        if args.markdown_lines[1] > len(md_lines):
            raise QualityError(f"Markdown行範囲が文書の行数({len(md_lines)})を超えています")
        selected = md_lines[args.markdown_lines[0] - 1:args.markdown_lines[1]]
        if not any(line.strip() for line in selected):
            raise QualityError("選択したMarkdown範囲が空です")
        lines, sparse_pages = pdf_lines(pdf_data, args.pdf_pages)
        report = build_report(lines, sparse_pages, selected, args.markdown_lines[0])
        report["source"] = {
            "pdf": str(args.pdf.resolve()),
            "pdf_sha256": hashlib.sha256(pdf_data).hexdigest(),
            "pdf_pages": list(args.pdf_pages),
            "markdown": str(args.markdown.resolve()),
            "markdown_sha256": hashlib.sha256(md_data).hexdigest(),
            "markdown_lines": list(args.markdown_lines),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (QualityError, OSError) as error:
        print(f"品質検査エラー: {error}", file=sys.stderr)
        return 1
    if report["status"] == "review_required":
        print(f"要確認の項目があります: {args.output}", file=sys.stderr)
        return 2
    print(f"検査結果を保存しました（完全性の保証ではありません）: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
