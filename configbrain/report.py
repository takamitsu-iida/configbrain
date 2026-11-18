"""Render an evaluation-only configuration draft as a readable Markdown report."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from configbrain.generate_config import GenerationError, _array, _single_line, _text
from configbrain.select_manuals import SelectionError, _write_output


class ReportError(RuntimeError):
    """Raised when a draft cannot be rendered safely."""


STATUS_LABELS = {
    "supported": "根拠あり",
    "partial": "部分対応",
    "not_found": "根拠未発見",
    "conflict": "矛盾あり",
    "needs_input": "追加情報が必要",
}


def _md(value: Any) -> str:
    text = " ".join(_text(value, "Report text").split())
    return re.sub(r"([\\`*_\[\]#|])", r"\\\1", html.escape(text, quote=False))


def _objects(value: Any, label: str) -> list[dict[str, Any]]:
    entries = _array(value, label)
    if any(not isinstance(entry, dict) for entry in entries):
        raise ReportError(f"{label} must contain objects.")
    return entries


def _code(text: str) -> str:
    fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", text)), default=0))
    return f"{fence}text\n{text}\n{fence}\n"


def _render_report(draft: dict[str, Any]) -> str:
    if (
        draft.get("schema_version") != 1 or draft.get("purpose") != "evaluation_only"
        or any(draft.get(key) is not False for key in (
            "ready_to_apply", "configuration_complete", "device_changes_performed",
        ))
        or draft.get("evidence_status") != "candidates_only"
        or draft.get("pdf_verification_status") != "not_verified"
    ):
        raise ReportError("Expected an evaluation-only generate_config draft with safety flags.")
    sources = draft.get("sources")
    if not isinstance(sources, dict):
        raise ReportError("Draft sources must be an object.")
    cited: dict[str, dict[str, Any]] = {}

    def reference(citation: dict[str, Any]) -> str:
        sid = _text(citation.get("source_id"), "Source ID")
        if not re.fullmatch(r"source-[1-9][0-9]*", sid) or sid not in sources:
            raise ReportError(f"Unknown source reference: {sid}")
        source = sources[sid]
        if not isinstance(source, dict):
            raise ReportError(f"Invalid source: {sid}")
        cited[sid] = source
        location = f"[{sid}](#{sid})"
        if "step_label" in citation:
            location += f" / {_md(citation['step_label'])}"
            location += (
                f" / 範囲内の表 {_md(str(citation['table_index_in_context']))}"
                f"・行 {_md(str(citation['row_index']))}"
            )
        return location

    def bullets(title: str, values: Any) -> None:
        lines.extend([f"### {title}", ""])
        entries = _array(values, title)
        lines.extend([f"- {_md(entry)}" for entry in entries] or ["なし。"])
        lines.append("")

    lines = [
        "# コンフィグ案レポート", "",
        "> **評価用・実機投入不可。** 原本PDFの内容は未照合です。",
        "> 出典・構文の検証は、設定の意味、順序、モード、許容値や手順の網羅性を保証しません。",
        "> 状態はLLMの判定です。この表示処理自体は元資料の再検証を行いません。", "",
        f"対象: {_md(draft['product'])} / {_md(draft['release'])}", "",
        "## 入力要件", "", _md(draft["requirement"]), "",
        "## 要件ごとの結果", "",
        "| 項目 | 要件 | 状態 |", "| --- | --- | --- |",
    ]
    items = _objects(draft.get("items"), "Draft items")
    if not items:
        raise ReportError("Draft items must not be empty.")
    seen = set()
    for item in items:
        item_id = _text(item.get("item_id"), "Item ID")
        if not re.fullmatch(r"item-[1-9][0-9]*", item_id) or item_id in seen:
            raise ReportError("Item IDs must be unique item-N identifiers.")
        seen.add(item_id)
        status = item.get("status")
        if status not in STATUS_LABELS:
            raise ReportError(f"Unknown item status: {status!r}")
        lines.append(
            f"| [{item_id}](#{item_id}) | {_md(item['requirement'])} "
            f"| {STATUS_LABELS[status]} ({status}) |"
        )
    lines.append("")
    for item in items:
        lines.extend([
            f"## {item['item_id']}", "", _md(item["requirement"]), "",
            f"**状態: {STATUS_LABELS[item['status']]} ({item['status']})**", "",
            _md(item["assessment"]), "",
        ])
        for key, title in (
            ("configuration", "設定案"),
            ("templates", "設定テンプレート（未指定値の解決が必要）"),
            ("verification", "確認コマンド"),
            ("verification_templates", "確認テンプレート（未指定値の解決が必要）"),
        ):
            lines.extend([f"### {title}", ""])
            commands = _objects(item.get(key), key)
            if not commands:
                lines.extend(["なし。", ""])
                continue
            lines.append(_code("\n".join(_single_line(c.get("cli"), "CLI") for c in commands)))
            lines.extend(["| CLI行 | 構文ID | 出典 |", "| --- | --- | --- |"])
            for index, command in enumerate(commands, 1):
                citations = _objects(command.get("citations"), "Command citations")
                if not citations:
                    raise ReportError("Each command must cite source evidence.")
                refs = "; ".join(reference(citation) for citation in citations)
                lines.append(f"| {index} | {_md(command['command_id'])} | {refs} |")
            lines.append("")
            for index, command in enumerate(commands, 1):
                for normalization in _objects(command.get("normalizations", []), "Normalizations"):
                    if normalization["kind"] != "interface_case":
                        raise ReportError("Unknown command normalization.")
                    lines.append(
                        f"- CLI行 {index}: インターフェース名の大文字小文字を要件の表記に統一"
                        f"（元のCLI: {_md(normalization['original_cli'])}）"
                    )
                bindings = command.get("bindings", {})
                if not isinstance(bindings, dict) or any(
                    not isinstance(binding, dict) for binding in bindings.values()
                ):
                    raise ReportError("Command bindings must contain objects.")
                for parameter, binding in bindings.items():
                    if "derivation" in binding:
                        derivation = binding["derivation"]
                        if derivation["kind"] != "ipv4_cidr_netmask":
                            raise ReportError("Unknown requirement-value derivation.")
                        lines.append(
                            f"- CLI行 {index} の {_md(parameter)}: "
                            f"{_md(derivation['source'])} から計算したマスク {_md(binding['value'])}"
                        )
            lines.append("")
        bullets("不足情報", item.get("missing_information"))
        bullets("前提・仮定", item.get("assumptions"))
        for key, title in (("constraints", "注意事項・制約"), ("evidence", "根拠の説明")):
            lines.extend([f"### {title}", ""])
            findings = _objects(item.get(key), key)
            if not findings:
                lines.extend(["なし。", ""])
            for finding in findings:
                lines.extend([_md(finding["explanation"]), ""])
                citations = _objects(finding.get("citations"), "Finding citations")
                if not citations:
                    raise ReportError("Each finding must cite source evidence.")
                for citation in citations:
                    lines.extend([
                        f"- {reference(citation)}",
                        f"  > {_md(citation['quote'])}", "",
                    ])
        lines.extend(["### 採用を保留した手順", ""])
        withheld = _objects(item.get("withheld_steps"), "Withheld steps")
        if not withheld:
            lines.extend(["なし。", ""])
        for step in withheld:
            lines.extend([_code(_single_line(step.get("source_syntax"), "Source syntax")),
                          _md(step["reason"]), ""])
            lines.extend(f"- {reference(c)}" for c in _objects(step.get("citations"), "Citations"))
            lines.append("")
        lines.extend(["### 推論候補（根拠付き設定とは別・未検証）", ""])
        inferences = _objects(item.get("inference_candidates"), "Inference candidates")
        if not inferences:
            lines.extend(["なし。", ""])
        for inference in inferences:
            lines.extend([_md(inference["description"]), ""])
            lines.extend(f"- 必要な前提: {_md(a)}" for a in _array(inference.get("assumptions"), "Assumptions"))
            lines.append("")
    lines.extend(["## 次に確認すること", ""])
    lines.extend(f"- {_md(check)}" for check in _array(draft.get("next_checks"), "Next checks"))
    lines.extend(["", "## 制限事項", ""])
    lines.extend(f"- {_md(limit)}" for limit in _array(draft.get("limitations"), "Limitations"))
    lines.extend(["", "## 応答修正履歴", ""])
    repairs = _objects(draft.get("validation_repairs", []), "Validation repairs")
    lines.extend([
        f"- {_md(repair['item_id'])}: {_md(repair['validation_error'])}"
        for repair in repairs
    ] or ["修正依頼なし。"])
    lines.extend(["", "## 参照資料", ""])
    for sid, source in cited.items():
        url = _text(source.get("chapter_url"), "Chapter URL")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "www.cisco.com" or parsed.username:
            raise ReportError(f"Invalid Cisco chapter URL for {sid}.")
        lines.extend([
            f"### {sid}", "",
            f"- 文書: {_md(source['document_title'])}",
            f"- 製品・リリース: {_md(source['product'])} / {_md(source['release'])}",
            f"- 章: {_md(str(source['chapter_order']))} / {_md(source['chapter_title'])}",
            f"- 節: {' / '.join(_md(s) for s in _array(source['section_path'], 'Section path'))}",
            f"- HTML: [章本文]({quote(url, safe=':/?=&%')})",
            f"- 章内行: {_md(str(source['chapter_line_start']))}–{_md(str(source['chapter_line_end']))}",
            f"- OKF行: {_md(str(source['manual_line_start']))}–{_md(str(source['manual_line_end']))}",
            f"- OKF: {_md(source['manual_path'])}",
            f"- OKF SHA-256: {_md(source['manual_sha256'])}",
            f"- PDF: {_md(source['source_pdf_path'])}",
            f"- PDF SHA-256: {_md(source['source_pdf_sha256'])}",
        ])
        page = source["pdf_page_index"]
        if source.get("pdf_page_scope") != ("chapter_start" if page is not None else "unmapped"):
            raise ReportError(f"Invalid PDF page scope for {sid}.")
        lines.extend([
            f"- PDF位置: 章先頭の物理ページ {_md(str(page))}（手順のページではありません）"
            if page is not None else "- PDF位置: 未対応",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def render_report(draft: dict[str, Any]) -> str:
    try:
        return _render_report(draft)
    except (GenerationError, KeyError, TypeError, ValueError) as error:
        raise ReportError(f"Could not render draft: {error}") from error


def _load_draft(path: Path) -> dict[str, Any]:
    try:
        draft = json.loads(path.read_bytes())
    except (UnicodeDecodeError, ValueError) as error:
        raise ReportError(f"Could not read draft JSON: {path}") from error
    if not isinstance(draft, dict):
        raise ReportError("Draft must be a JSON object.")
    return draft


def load_report(path: Path) -> str:
    return render_report(_load_draft(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        draft = _load_draft(args.draft)
        output = args.output.resolve()
        protected = {args.draft.resolve(), Path(".env").resolve()}
        sources = draft.get("sources")
        if isinstance(sources, dict):
            for source in sources.values():
                if isinstance(source, dict):
                    for key in ("manual_path", "source_pdf_path"):
                        if isinstance(source.get(key), str):
                            protected.add(Path(source[key]).resolve())
        if output in protected or any(
            output.is_relative_to(Path(root).resolve())
            for root in ("data/okf", "data/source_manuals", "data/qdrant")
        ):
            raise ReportError("Output must not overwrite the draft, sources or configuration.")
        _write_output(args.output, render_report(draft))
    except (ReportError, SelectionError, OSError) as error:
        print(f"Report generation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
