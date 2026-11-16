"""Compare rule candidates from a selected local HTML fragment with a PDF baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import Comment, Doctype
from soupsieve import SelectorSyntaxError

if __package__:
    from .extract_rule_candidates import RuleExtractionError, build_candidates, extract_candidates
    from .rag import RAGError, Section, clean_heading
    from .transform_markdown import TransformationError, load_config
else:
    from extract_rule_candidates import RuleExtractionError, build_candidates, extract_candidates
    from rag import RAGError, Section, clean_heading
    from transform_markdown import TransformationError, load_config


class ComparisonError(ValueError):
    """Raised when a comparison input cannot be used."""


def read_file(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise ComparisonError(f"ファイルを読み取れません: {path}: {error}") from error


def parse_html(data: bytes) -> BeautifulSoup:
    # Beautiful Soup detects the encoding from the HTML document.
    soup = BeautifulSoup(data, "html.parser")
    if soup.contains_replacement_characters:
        raise ComparisonError("HTMLの文字コードを正しく読み取れません")
    return soup


def list_headings(soup: BeautifulSoup) -> list[dict[str, str | None]]:
    return [
        {
            "tag": heading.name,
            "id": heading.get("id"),
            "text": " ".join(heading.get_text(" ", strip=True).split()),
        }
        for heading in soup.find_all(re.compile(r"^h[1-6]$"))
    ]


def select_fragment(soup: BeautifulSoup, selector: str) -> Tag:
    try:
        matches = soup.select(selector)
    except SelectorSyntaxError as error:
        raise ComparisonError(f"CSSセレクターが不正です: {error}") from error
    if len(matches) != 1:
        raise ComparisonError(f"セレクターは要素1件を選択してください: {len(matches)}件")
    return matches[0]


def render_fragment(fragment: Tag) -> tuple[str, list[str]]:
    """Render a fragment as text with explicit table rows and cells."""
    blocks = {
        "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt",
        "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "ol", "p", "section", "ul",
    }
    ignored = {"script", "style", "noscript"}
    lines: list[str] = []
    pending: list[str] = []
    warnings: list[str] = []
    if fragment.name == "img" or fragment.find("img") is not None:
        warnings.append("画像の内容は抽出していません。原文を確認してください")

    def flush() -> None:
        text = " ".join("".join(pending).split())
        pending.clear()
        if text:
            lines.append(text)

    def walk(node: Tag | NavigableString) -> None:
        if isinstance(node, (Comment, Doctype)):
            return
        if isinstance(node, NavigableString):
            pending.append(str(node))
            return
        if node.name in ignored:
            return
        if node.name == "img":
            return
        if node.name == "br":
            flush()
            return
        if node.name == "pre":
            flush()
            text = node.get_text().strip("\n")
            if text.strip():
                lines.extend(["[pre]", text, "[/pre]"])
            return
        if node.name == "table":
            flush()
            if node.find("table") is not None:
                raise ComparisonError("入れ子の表は未対応です。原文を確認してください")
            lines.append("[table]")
            caption = node.find("caption", recursive=False)
            if caption is not None:
                lines.append("[caption]")
                for child in caption.children:
                    walk(child)
                flush()
                lines.append("[/caption]")
            for row in node.find_all("tr"):
                lines.append("[row]")
                for cell in row.find_all(["th", "td"], recursive=False):
                    spans = " ".join(
                        f"{name}={cell[name]}" for name in ("rowspan", "colspan")
                        if cell.get(name) is not None
                    )
                    label = cell.name + (" " + spans if spans else "")
                    # Preserve cell boundaries and block breaks, including CLI examples.
                    lines.append(f"[{label}]")
                    for child in cell.children:
                        walk(child)
                    flush()
                    lines.append(f"[/{cell.name}]")
                lines.append("[/row]")
            lines.append("[/table]")
            return
        if node.name in blocks:
            flush()
        for child in node.children:
            if isinstance(child, (Tag, NavigableString)):
                walk(child)
        if node.name in blocks:
            flush()

    walk(fragment)
    flush()
    text = "\n".join(lines).strip()
    if not text:
        raise ComparisonError("選択したHTML要素に本文がありません")
    return text, list(dict.fromkeys(warnings))


def load_baseline(data: bytes) -> dict[str, Any]:
    try:
        result = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ComparisonError("PDF側候補JSONを読み取れません") from error
    if not isinstance(result, dict) or result.get("status") != "candidate":
        raise ComparisonError("PDF側は未検証のルール候補JSONを指定してください")
    source = result.get("source")
    if not isinstance(source, dict) or not all(
        isinstance(source.get(key), str) and source[key]
        for key in ("document_id", "citation_id", "title", "product", "release", "source_url")
    ):
        raise ComparisonError("PDF側候補JSONの出典情報が不正です")
    if not isinstance(result.get("rules"), list) or not isinstance(result.get("issues"), list):
        raise ComparisonError("PDF側候補JSONのルールまたはissuesが不正です")
    return result


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    for rule in result["rules"]:
        if not isinstance(rule, dict) or not isinstance(rule.get("commands"), list):
            raise ComparisonError("候補JSONのcommandsが不正です")
        for command in rule["commands"]:
            if not isinstance(command, dict) or not isinstance(command.get("syntax"), str):
                raise ComparisonError("候補JSONのコマンド構文が不正です")
            check = command.get("syntax_check")
            if (
                not isinstance(check, dict)
                or set(check) != {"in_section", "in_manual", "syntax_in_quote"}
                or not all(isinstance(value, bool) for value in check.values())
                or "mode" not in command
            ):
                raise ComparisonError("候補JSONのコマンド検査結果が不正です")
            commands.append(command)
    return {
        "commands": len(commands),
        "syntax_and_quote_matched": sum(
            all(command["syntax_check"].values()) for command in commands
        ),
        "mode_unknown": sum(command["mode"] is None for command in commands),
        "issues": len(result["issues"]),
        "syntaxes": [command["syntax"] for command in commands],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path, help="保存したHTMLファイル")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list-headings", action="store_true")
    action.add_argument("--selector", help="比較対象を一意に選ぶCSSセレクター")
    parser.add_argument("--pdf-result", type=Path, help="対応するPDF側の候補JSON")
    parser.add_argument("--source-url", help="HTML原文のURL")
    parser.add_argument("--preview", action="store_true", help="LLMを呼ばずに選択内容を確認")
    parser.add_argument("--output", type=Path, help="比較JSONの新規保存先")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.list_headings:
        if args.pdf_result or args.source_url or args.preview or args.output:
            parser.error("--list-headingsは他のオプションと併用できません")
    else:
        if not args.pdf_result or not args.source_url:
            parser.error("--selectorには--pdf-resultと--source-urlが必要です")
        if args.preview == bool(args.output):
            parser.error("--previewまたは--outputのいずれかを指定してください")
        parsed_url = urlsplit(args.source_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.username or parsed_url.password:
            parser.error("--source-urlには認証情報のないHTTPS URLを指定してください")
    if args.output and args.output.exists():
        print(f"出力先は既に存在します: {args.output}", file=sys.stderr)
        return 1
    try:
        html_bytes = read_file(args.html)
        soup = parse_html(html_bytes)
        if args.list_headings:
            print(json.dumps(list_headings(soup), ensure_ascii=False, indent=2))
            return 0
        baseline_bytes = read_file(args.pdf_result)
        baseline = load_baseline(baseline_bytes)
        pdf_summary = summarize(baseline)
        fragment = select_fragment(soup, args.selector)
        body, warnings = render_fragment(fragment)
        headings = list_headings(BeautifulSoup(str(fragment), "html.parser"))
        path = tuple(item["text"] for item in headings if item["text"])[:1]
        if not path:
            path = ("Selected HTML fragment",)
        source = baseline["source"]
        prefix_path = path[1:] if path[0].lower() == clean_heading(source["title"]).lower() else path
        prefix = " > ".join(prefix_path)
        section = Section(
            citation_id=f"{source['document_id']}:html-{hashlib.sha256(args.selector.encode()).hexdigest()[:12]}",
            document_id=source["document_id"],
            title=source["title"],
            version=source["release"],
            product=source["product"],
            release=source["release"],
            heading_path=path,
            text=f"{prefix}\n\n{body}" if prefix else body,
            source_url=args.source_url,
        )
        input_info = {
            "html_file": str(args.html.resolve()),
            "html_sha256": hashlib.sha256(html_bytes).hexdigest(),
            "source_url": args.source_url,
            "selector": args.selector,
            "heading_path": path,
            "headings": headings,
            "normalized_text": body,
            "warnings": warnings,
            "pdf_result_file": str(args.pdf_result.resolve()),
            "pdf_result_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
            "pdf_source": source,
        }
        if args.preview:
            print(json.dumps(input_info, ensure_ascii=False, indent=2))
            return 0
        config = load_config()
        proposal = extract_candidates(section, config)
        html_result = build_candidates(
            section, body, hashlib.sha256(body.encode("utf-8")).hexdigest(),
            hashlib.sha256(html_bytes).hexdigest(), proposal,
        )
        html_result["source"]["normalized_text_sha256"] = html_result["source"].pop("manual_sha256")
        del html_result["source"]["descriptor_sha256"]
        html_result["source"]["html_sha256"] = hashlib.sha256(html_bytes).hexdigest()
        html_result["source"]["selector"] = args.selector
        html_result["notice"] = (
            "未検証のルール候補です。引用検査は選択HTMLを変換したテキストに対する"
            "文字列照合であり、原文HTMLとの意味的な一致や正しさを保証しません。"
            "設定生成には使用しないでください。"
        )
        if not html_result["rules"]:
            warnings.append("HTML側からルール候補が抽出されませんでした")
        comparison = {
            "status": "candidate_comparison",
            "notice": (
                "文字列一致や件数は抽出精度を示しません。HTMLとPDFの対象範囲、"
                "製品・リリース、元文書と内容の対応を確認してください。"
                "PDF側のLLM設定は記録されていない場合があります。"
            ),
            "input": input_info,
            "html_model": config.model,
            "summary": {"pdf": pdf_summary, "html": summarize(html_result)},
            "pdf_candidates": baseline,
            "html_candidates": html_result,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(comparison, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (ComparisonError, RuleExtractionError, RAGError, TransformationError, OSError) as error:
        print(f"HTML比較エラー: {error}", file=sys.stderr)
        return 1
    if html_result["issues"] or warnings:
        print(f"HTML側に未解決項目があります: {args.output}", file=sys.stderr)
        return 2
    print(f"未検証の比較結果を保存しました: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
