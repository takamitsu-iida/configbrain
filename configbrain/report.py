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

from configbrain.generate_config import (
    EVIDENCE_REGISTRY_VERSION, GenerationError, _array, _fulfillment_status,
    _single_line, _text,
)
from configbrain.select_manuals import SelectionError, _write_output
from configbrain.reference_evidence import HTML_VERIFICATION_VERSION, REFERENCE_EVIDENCE_VERSION
from configbrain.semantic_review import validate_saved_reviews
from configbrain.verify_pdf import PDF_VERIFICATION_VERSION


class ReportError(RuntimeError):
    """Raised when a draft cannot be rendered safely."""


STATUS_LABELS = {
    "supported": "根拠あり",
    "partial": "部分対応",
    "not_found": "根拠未発見",
    "conflict": "矛盾あり",
    "needs_input": "追加情報が必要",
}
GRAPH_ROLE_LABELS = {
    "configuration": "設定",
    "templates": "設定テンプレート",
    "verification": "確認",
    "verification_templates": "確認テンプレート",
    "shared_context": "共有コンテキスト",
    "withheld": "保留",
}
PIPELINE_STAGE_LABELS = {
    "selection": "選定",
    "search": "検索",
    "contexts": "文脈取得",
    "generation": "コンフィグ案生成",
    "report": "レポート生成",
}
PIPELINE_STATUS_LABELS = {
    "not_started": "未実行",
    "running": "実行中",
    "completed": "成功",
    "completed_with_warnings": "警告あり",
    "failed": "失敗",
}
FULFILLMENT_STATUS_LABELS = {
    "complete": "全項目が supported（判定上）",
    "incomplete": "未完了（追加情報・未充足項目あり）",
    "undetermined": "未判定（生成エラー等）",
}


def _md(value: Any) -> str:
    text = " ".join(_text(value, "Report text").split())
    return re.sub(r"([\\`*_\[\]#|])", r"\\\1", html.escape(text, quote=False))


def _objects(value: Any, label: str) -> list[dict[str, Any]]:
    entries = _array(value, label)
    if any(not isinstance(entry, dict) for entry in entries):
        raise ReportError(f"{label} must contain objects.")
    return entries


def _strings(value: Any, label: str) -> list[str]:
    return [_text(entry, label) for entry in _array(value, label)]


def _code(text: str) -> str:
    fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", text)), default=0))
    return f"{fence}text\n{text}\n{fence}\n"


def _render_report(
    draft: dict[str, Any], pipeline_stages: dict[str, str] | None = None,
) -> str:
    if (
        draft.get("schema_version") != 1 or draft.get("purpose") != "evaluation_only"
        or any(draft.get(key) is not False for key in (
            "ready_to_apply", "configuration_complete", "device_changes_performed",
        ))
        or draft.get("evidence_status") != "candidates_only"
        or draft.get("pdf_verification_status") not in (
            "not_verified", "partial", "verified",
        )
    ):
        raise ReportError("Expected an evaluation-only generate_config draft with safety flags.")
    if draft.get("pdf_verification_status") != "not_verified" and (
        type(draft.get("pdf_verification_version")) is not int
        or draft["pdf_verification_version"] != PDF_VERIFICATION_VERSION
    ):
        raise ReportError("Unsupported PDF verification version.")
    if draft.get("evidence_registry_version") not in (None, EVIDENCE_REGISTRY_VERSION):
        raise ReportError("Unsupported evidence registry version.")
    sources = draft.get("sources")
    if not isinstance(sources, dict):
        raise ReportError("Draft sources must be an object.")
    items = _objects(draft.get("items"), "Draft items")
    if not items:
        raise ReportError("Draft items must not be empty.")
    validate_saved_reviews(draft)
    generation_status = draft.get("generation_status", "complete")
    failed_items = [item for item in items if item.get("generation_error") is not None]
    reference_failures = [
        item for item in items if item.get("reference_generation_error") is not None
    ]
    semantic_failures = [
        item for item in items if item.get("semantic_review_error") is not None
    ]
    consistency_warnings = _objects(
        draft.get("consistency_warnings", []), "Consistency warnings",
    )
    if generation_status not in ("complete", "partial") or (
        (generation_status == "partial") != bool(
            failed_items or reference_failures or semantic_failures or consistency_warnings
        )
    ):
        raise ReportError("Draft generation_status does not match its incomplete items.")
    computed_fulfillment_status = _fulfillment_status(items, consistency_warnings)
    if "fulfillment_status" in draft:
        fulfillment_status = draft["fulfillment_status"]
        if (
            not isinstance(fulfillment_status, str)
            or fulfillment_status not in FULFILLMENT_STATUS_LABELS
            or fulfillment_status != computed_fulfillment_status
        ):
            raise ReportError("Draft fulfillment_status does not match its requirement items.")
    else:
        fulfillment_status = computed_fulfillment_status
    generation_rounds = draft.get("generation_rounds")
    generation_rounds_used = draft.get("generation_rounds_used")
    if generation_rounds is not None or generation_rounds_used is not None:
        if (
            type(generation_rounds) is not int or generation_rounds < 1
            or type(generation_rounds_used) is not int
            or not 1 <= generation_rounds_used <= generation_rounds
        ):
            raise ReportError("Draft generation round counts are invalid.")
    if pipeline_stages is not None and (
        set(pipeline_stages) != set(PIPELINE_STAGE_LABELS)
        or any(status not in PIPELINE_STATUS_LABELS for status in pipeline_stages.values())
    ):
        raise ReportError("Pipeline stage statuses are incomplete or invalid.")
    cited: dict[str, dict[str, Any]] = {}
    pdf_verifications: list[dict[str, Any]] = []
    html_verifications: list[dict[str, Any]] = []
    pdf_verification_status = draft["pdf_verification_status"]
    has_reference_sources = any(
        source.get("document_type") == "command_reference"
        for source in sources.values() if isinstance(source, dict)
    )

    def reference(citation: dict[str, Any], *, allow_reference: bool = False) -> str:
        sid = _text(citation.get("source_id"), "Source ID")
        if sid not in sources:
            raise ReportError(f"Unknown source reference: {sid}")
        source = sources[sid]
        if not isinstance(source, dict):
            raise ReportError(f"Invalid source: {sid}")
        cited[sid] = source
        location = f"[{sid}](#{sid})"
        evidence_id = citation.get("evidence_id")
        if source.get("document_type") == "command_reference":
            if not allow_reference:
                raise ReportError("Command references are explanation-only, not executable CLI evidence.")
            if (
                not re.fullmatch(r"reference-source-[0-9a-f]{64}", sid)
                or not isinstance(evidence_id, str)
                or not re.fullmatch(
                    rf"reference-evidence-v{REFERENCE_EVIDENCE_VERSION}-[0-9a-f]{{64}}", evidence_id,
                )
                or not isinstance(citation.get("quote"), str) or not citation["quote"].strip()
                or "pdf_verification" in citation
                or draft.get("html_verification_status") != "verified"
                or draft.get("html_verification_version") != HTML_VERIFICATION_VERSION
                or not isinstance(source.get("html_content_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", source["html_content_sha256"])
            ):
                raise ReportError("Reference citations require HTML-verified explanatory evidence.")
            verification = citation.get("html_verification")
            if verification != {
                "status": "matched", "method": "stored_html_command_span",
                "html_sha256": source.get("html_content_sha256"),
                "command_id": source.get("command_id"),
            }:
                raise ReportError("Invalid per-citation HTML verification.")
            locator = citation.get("locator")
            if not isinstance(locator, dict):
                raise ReportError("Reference citation requires its HTML locator.")
            positions = _objects(locator.get("sources"), "Reference HTML positions")
            if not positions:
                raise ReportError("Reference citation requires its HTML positions.")
            for position in positions:
                anchor = position.get("anchor")
                if anchor is not None:
                    anchor = _text(anchor, "Reference anchor")
                url = source["chapter_url"] + (f"#{anchor}" if anchor else "")
                if (
                    position.get("chapter_url") != source["chapter_url"]
                    or position.get("chapter_order") != source["chapter_order"]
                    or position.get("url") != url
                ):
                    raise ReportError("Reference citation position differs from its source chapter.")
                location += (
                    f" / [引用位置]({quote(url, safe=':/?=&%#')})"
                    f" / HTML内位置: {_md(position['locator'])}"
                )
            html_verifications.append(verification)
            return (
                location + f" / 説明用根拠ID `{evidence_id}`"
                + " / 保存HTML照合済み（PDF照合ではありません）"
            )
        if not re.fullmatch(r"source-[1-9][0-9]*", sid):
            raise ReportError("Invalid guide source ID.")
        if evidence_id is not None:
            if not isinstance(evidence_id, str) or not re.fullmatch(
                rf"evidence-v{EVIDENCE_REGISTRY_VERSION}-[0-9a-f]{{64}}", evidence_id,
            ):
                raise ReportError("Invalid evidence ID.")
            location += f" / 根拠ID `{_md(evidence_id)}`"
        if "step_label" in citation:
            location += f" / {_md(citation['step_label'])}"
            location += (
                f" / 範囲内の表 {_md(str(citation['table_index_in_context']))}"
                f"・行 {_md(str(citation['row_index']))}"
            )
        if pdf_verification_status != "not_verified":
            if (
                evidence_id is None
                or not isinstance(citation.get("quote"), str)
                or not citation["quote"].strip()
            ):
                raise ReportError("Verified PDF status requires a quoted evidence ID.")
            verification = citation.get("pdf_verification")
            if not isinstance(verification, dict):
                raise ReportError("Verified PDF status requires per-citation verification.")
            status = verification.get("status")
            page_indexes = verification.get("page_indexes")
            fragment_count = verification.get("fragment_count")
            if (
                status not in ("matched", "ambiguous", "not_found", "unmapped")
                or not isinstance(page_indexes, list)
                or any(type(page) is not int or page < 1 for page in page_indexes)
                or page_indexes != sorted(set(page_indexes))
                or type(fragment_count) is not int or fragment_count < 0
                or verification.get("method") != "normalized_text_fragments"
            ):
                raise ReportError("Invalid per-citation PDF verification.")
            if (
                (status == "matched" and (len(page_indexes) != 1 or fragment_count < 1))
                or (status == "ambiguous" and (len(page_indexes) < 2 or fragment_count < 1))
                or (status == "not_found" and (page_indexes or fragment_count < 1))
                or (status == "unmapped" and (page_indexes or fragment_count != 0))
            ):
                raise ReportError("Per-citation PDF verification status is inconsistent.")
            pdf_verifications.append(verification)
            if status == "matched":
                location += f" / PDF物理ページ {page_indexes[0]}（テキスト照合）"
            elif status == "ambiguous":
                location += (
                    " / PDF候補ページ "
                    + ", ".join(str(page) for page in page_indexes)
                    + "（複数一致）"
                )
            elif status == "unmapped":
                location += " / PDFページ未対応"
            else:
                location += " / PDFテキスト未一致"
        return location

    def bullets(title: str, values: Any) -> None:
        lines.extend([f"### {title}", ""])
        entries = _array(values, title)
        lines.extend([f"- {_md(entry)}" for entry in entries] or ["なし。"])
        lines.append("")

    lines = [
        "# コンフィグ案レポート", "",
        (
            "> **評価用・実機投入不可。** "
            + {
                "not_verified": "原本PDFの内容は未照合です。",
                "partial": "原本PDFの引用照合に未一致または複数候補があります。",
                "verified": (
                    "ガイドの引用箇所のテキストを原本PDFと照合しました。"
                    if has_reference_sources else "引用箇所のテキストを原本PDFと照合しました。"
                ),
            }[pdf_verification_status]
        ),
        "> 出典・構文の検証は、設定の意味、順序、モード、許容値や手順の網羅性を保証しません。",
        "> 状態はLLMの判定です。この表示処理自体は元資料の再検証を行いません。", "",
        *(
            [
                "> コマンドリファレンスは説明用です。ガイドのPDF照合とは別に、"
                "引用と保存HTML内のコマンド範囲・ハッシュを照合します。",
                "",
            ]
            if has_reference_sources else []
        ),
        *(
            [
                *(
                    [
                        "> **一部の要件項目は自動生成未完了で、充足状態は未判定です。"
                        "以下の部分出力がある場合は個別検証を通過したものだけです。**"
                    ]
                    if failed_items else []
                ),
                *(
                    ["> **項目間に整合性確認が必要な警告があります。**"]
                    if consistency_warnings else []
                ),
                *(
                    ["> **リファレンスの補足生成が未完了です。既存のCLI案は保持しています。**"]
                    if reference_failures else []
                ),
                *(
                    ["> **説明の意味検証に未解決の問題があります。該当説明は保留し、CLI案は保持しています。**"]
                    if semantic_failures else []
                ),
                "",
            ]
            if generation_status == "partial" else []
        ),
        f"対象: {_md(draft['product'])} / {_md(draft['release'])}", "",
        (
            f"**要件充足判定: {FULFILLMENT_STATUS_LABELS[fulfillment_status]} "
            f"(`{fulfillment_status}`)**"
        ),
        "",
        *(
            [f"生成レビュー: {generation_rounds_used}/{generation_rounds} ラウンド", ""]
            if generation_rounds is not None else []
        ),
        *(
            [
                "## パイプライン実行状況", "",
                "| フェーズ | 結果 |", "| --- | --- |",
                *[
                    f"| {PIPELINE_STAGE_LABELS[stage]} (`{stage}`) "
                    f"| {PIPELINE_STATUS_LABELS[pipeline_stages[stage]]} |"
                    for stage in PIPELINE_STAGE_LABELS
                ],
                "",
            ]
            if pipeline_stages is not None else []
        ),
        "## 入力要件", "", _md(draft["requirement"]), "",
        "## 要件ごとの結果", "",
        "| 項目 | 要件 | 状態 |", "| --- | --- | --- |",
    ]
    seen = set()
    for item in items:
        item_id = _text(item.get("item_id"), "Item ID")
        if not re.fullmatch(r"item-[1-9][0-9]*", item_id) or item_id in seen:
            raise ReportError("Item IDs must be unique item-N identifiers.")
        seen.add(item_id)
        generation_error = item.get("generation_error")
        if generation_error is not None:
            if not isinstance(generation_error, str) or not generation_error.strip():
                raise ReportError("Failed item must include a generation error.")
            if item.get("status") is not None:
                raise ReportError("Failed item must not claim a requirement fulfillment status.")
            item_status = "生成未完了 (generation_failed)"
        else:
            status = item.get("status")
            if status not in STATUS_LABELS:
                raise ReportError(f"Unknown item status: {status!r}")
            item_status = f"{STATUS_LABELS[status]} ({status})"
        lines.append(
            f"| [{item_id}](#{item_id}) | {_md(item['requirement'])} "
            f"| {item_status} |"
        )
    lines.append("")
    if "semantic_reviews" in draft:
        lines.extend([
            "## 説明の意味検証", "",
            "引用との整合と適用性は別のLLM呼び出しによる判定です。"
            "独立した正しさの保証や実機状態の確認ではありません。"
            "保留した説明は根拠として採用せず、条件未確認の説明は条件付きで表示します。"
            "CLIはこの検証・説明修正では変更しません。", "",
        ])
    item_by_id = {item["item_id"]: item for item in items}
    dependency_adjustments = _objects(
        draft.get("dependency_adjustments", []), "Dependency adjustments",
    )
    for adjustment in dependency_adjustments:
        item_id = _text(adjustment.get("item_id"), "Dependency adjustment item ID")
        claimed = _strings(
            adjustment.get("claimed_depends_on"), "Claimed dependency ID",
        )
        verified = _strings(
            adjustment.get("verified_depends_on"), "Verified dependency ID",
        )
        _text(adjustment.get("reason"), "Dependency adjustment reason")
        if (
            item_id not in item_by_id
            or set(claimed) == set(verified)
            or any(dependency not in item_by_id for dependency in claimed + verified)
        ):
            raise ReportError("Dependency adjustment does not match the draft items.")

    requirement_graph = draft.get("requirement_graph")
    if requirement_graph is not None:
        if not isinstance(requirement_graph, dict):
            raise ReportError("Requirement graph must be an object.")
        dependency_evidence_version = requirement_graph.get("dependency_evidence_version")
        if dependency_evidence_version is not None and (
            type(dependency_evidence_version) is not int or dependency_evidence_version != 1
        ):
            raise ReportError("Unsupported dependency evidence version.")
        graph_requirements = _objects(
            requirement_graph.get("requirements"), "Graph requirements",
        )
        if len(graph_requirements) != len(items):
            raise ReportError("Requirement graph must retain every requirement item.")
        graph_requirement_ids = set()
        dependency_edges = set()
        dependency_reference_text = {}
        for node in graph_requirements:
            item_id = _text(node.get("item_id"), "Graph requirement ID")
            if item_id in graph_requirement_ids or item_id not in item_by_id:
                raise ReportError("Requirement graph contains an unknown or duplicate item.")
            graph_requirement_ids.add(item_id)
            dependencies = node.get("depends_on")
            if not isinstance(dependencies, list) or not all(
                isinstance(dependency, str) for dependency in dependencies
            ):
                raise ReportError("Graph dependencies must be an array of item IDs.")
            if dependencies != item_by_id[item_id].get("depends_on", []):
                raise ReportError("Graph dependencies differ from the item results.")
            if node.get("status") != item_by_id[item_id].get("status"):
                raise ReportError("Graph requirement status differs from the item results.")
            proofs = _objects(
                node.get("dependency_evidence", []), "Dependency evidence",
            )
            if dependency_evidence_version == 1 and (
                proofs != item_by_id[item_id].get("dependency_evidence", [])
            ):
                raise ReportError("Graph dependency evidence differs from the item results.")
            proof_by_dependency = {}
            for proof in proofs:
                dependency_id = _text(
                    proof.get("dependency_item_id"), "Dependency evidence item ID",
                )
                if (
                    dependency_id in proof_by_dependency
                    or dependency_id not in dependencies
                    or proof.get("relation") != "procedure_order"
                ):
                    raise ReportError("Dependency evidence does not match a verified dependency.")
                prerequisite_command_id = _text(
                    proof.get("prerequisite_command_id"), "Prerequisite command ID",
                )
                dependent_command_id = _text(
                    proof.get("dependent_command_id"), "Dependent command ID",
                )
                citations = _objects(proof.get("citations"), "Dependency citations")
                if len(citations) != 2:
                    raise ReportError("Procedure-order dependency requires two citations.")
                for citation in citations:
                    reference(citation)
                prerequisite_citation, dependent_citation = citations
                source_id = prerequisite_citation.get("source_id")
                table_index = prerequisite_citation.get("table_index_in_context")
                prerequisite_row = prerequisite_citation.get("row_index")
                dependent_row = dependent_citation.get("row_index")
                if (
                    dependent_citation.get("source_id") != source_id
                    or dependent_citation.get("table_index_in_context") != table_index
                    or type(table_index) is not int or table_index < 1
                    or type(prerequisite_row) is not int or prerequisite_row < 1
                    or type(dependent_row) is not int or dependent_row <= prerequisite_row
                ):
                    raise ReportError("Dependency citations do not establish procedure order.")

                def command_has_citation(
                    item_id: str, command_id: str, citation: dict[str, Any],
                ) -> bool:
                    for key in ("configuration", "templates"):
                        for command in _objects(item_by_id[item_id].get(key, []), key):
                            if command.get("command_id") != command_id:
                                continue
                            if any(
                                entry.get("evidence_id") == citation.get("evidence_id")
                                and entry.get("source_id") == citation.get("source_id")
                                for entry in _objects(command.get("citations"), "Command citations")
                            ):
                                return True
                    return False

                if not (
                    command_has_citation(
                        dependency_id, prerequisite_command_id, prerequisite_citation,
                    )
                    and command_has_citation(
                        item_id, dependent_command_id, dependent_citation,
                    )
                ):
                    raise ReportError("Dependency evidence does not match its command owners.")
                proof_by_dependency[dependency_id] = " → ".join(
                    reference(citation) for citation in citations
                )
            if dependency_evidence_version == 1 and set(proof_by_dependency) != set(dependencies):
                raise ReportError("Every verified dependency must include source evidence.")
            for dependency_id, references in proof_by_dependency.items():
                dependency_reference_text[(item_id, dependency_id)] = references
            for dependency in dependencies:
                if dependency not in item_by_id or dependency == item_id:
                    raise ReportError("Requirement graph contains an invalid dependency.")
                dependency_edges.add((item_id, dependency, "depends_on"))
        if graph_requirement_ids != set(item_by_id):
            raise ReportError("Requirement graph omits requirement items.")

        graph_commands = _objects(requirement_graph.get("commands"), "Graph commands")
        command_node_ids = set()
        ownership_edges = set()
        for node in graph_commands:
            node_id = _text(node.get("node_id"), "Graph command node ID")
            if not re.fullmatch(r"command-node-[1-9][0-9]*", node_id) or node_id in command_node_ids:
                raise ReportError("Graph command node IDs must be unique command-node-N identifiers.")
            command_node_ids.add(node_id)
            _text(node.get("command_id"), "Graph source command ID")
            _single_line(node.get("cli"), "Graph command CLI")
            role = node.get("role")
            if role not in GRAPH_ROLE_LABELS:
                raise ReportError("Requirement graph contains an unknown command role.")
            required_by = node.get("required_by")
            if not isinstance(required_by, list) or not required_by or not all(
                isinstance(item_id, str) and item_id in item_by_id
                for item_id in required_by
            ) or len(set(required_by)) != len(required_by):
                raise ReportError("Graph command required_by must contain unique item IDs.")
            owner_item_id = node.get("owner_item_id")
            if role == "shared_context":
                if owner_item_id is not None:
                    raise ReportError("Shared context commands must not have a single owner.")
                edge_kind = "uses_context"
            else:
                if owner_item_id not in required_by:
                    raise ReportError("Graph command owner must be one of its required items.")
                edge_kind = "withholds" if role == "withheld" else "owns"
            for item_id in required_by:
                item = item_by_id[item_id]
                if role == "withheld":
                    matches = any(
                        step.get("command_id") == node["command_id"]
                        and step.get("source_syntax") == node["cli"]
                        for step in _objects(item.get("withheld_steps"), "Withheld steps")
                    )
                elif role == "shared_context":
                    matches = any(
                        command.get("command_id") == node["command_id"]
                        and command.get("cli") == node["cli"]
                        for key in (
                            "configuration", "templates", "verification",
                            "verification_templates",
                        )
                        for command in _objects(item.get(key), key)
                    )
                else:
                    matches = any(
                        command.get("command_id") == node["command_id"]
                        and command.get("cli") == node["cli"]
                        for command in _objects(item.get(role), role)
                    )
                if not matches:
                    raise ReportError("Graph command does not match its requirement item.")
            evidence_ids = node.get("evidence_ids")
            if not isinstance(evidence_ids, list) or not all(
                isinstance(evidence_id, str)
                and re.fullmatch(
                    rf"evidence-v{EVIDENCE_REGISTRY_VERSION}-[0-9a-f]{{64}}",
                    evidence_id,
                )
                for evidence_id in evidence_ids
            ):
                raise ReportError("Graph command evidence IDs are invalid.")
            ownership_edges.update(
                (item_id, node_id, edge_kind) for item_id in required_by
            )
        graph_edges = _objects(requirement_graph.get("edges"), "Graph edges")
        parsed_edges = set()
        for edge in graph_edges:
            parsed_edges.add((
                _text(edge.get("from"), "Graph edge source"),
                _text(edge.get("to"), "Graph edge target"),
                _text(edge.get("kind"), "Graph edge kind"),
            ))
        if parsed_edges != dependency_edges | ownership_edges:
            raise ReportError("Requirement graph edges do not match its nodes.")

        lines.extend(["## 要件・コマンド依存グラフ", ""])
        lines.extend(["### 要件の前提", ""])
        for node in graph_requirements:
            dependencies = node["depends_on"]
            lines.append(
                f"- [{node['item_id']}](#{node['item_id']}) は "
                + (
                    ", ".join(f"[{dependency}](#{dependency})" for dependency in dependencies)
                    + " に依存します。"
                    if dependencies else "他の要件項目に依存しません。"
                )
            )
            for dependency in dependencies:
                references = dependency_reference_text.get((node["item_id"], dependency))
                if references:
                    lines.append(
                        f"  - 手順順序の根拠: {_md(references)}"
                    )
        lines.extend(["", "### コマンドの所有・共有関係", ""])
        if graph_commands:
            lines.extend([
                "| CLI | 役割 | 所有項目 | 必要な項目 | 根拠ID |",
                "| --- | --- | --- | --- | --- |",
            ])
            for node in graph_commands:
                owner = node.get("owner_item_id")
                owner_text = (
                    f"[{owner}](#{owner})" if owner is not None else "共有コンテキスト"
                )
                required = ", ".join(
                    f"[{item_id}](#{item_id})" for item_id in node["required_by"]
                )
                evidence_refs = ", ".join(
                    f"`{_md(evidence_id)}`" for evidence_id in node["evidence_ids"]
                ) or "なし"
                lines.append(
                    f"| {_md(_single_line(node['cli'], 'Graph command CLI'))} "
                    f"| {GRAPH_ROLE_LABELS[node['role']]} | {owner_text} "
                    f"| {required} | {evidence_refs} |"
                )
        else:
            lines.append("コマンドノードはありません。")
        lines.append("")

    if dependency_adjustments:
        lines.extend(["## 依存関係の検証", ""])
        for adjustment in dependency_adjustments:
            item_id = adjustment["item_id"]
            claimed = adjustment["claimed_depends_on"]
            verified = adjustment["verified_depends_on"]
            claimed_text = ", ".join(
                f"[{dependency}](#{dependency})" for dependency in claimed
            ) or "なし"
            verified_text = ", ".join(
                f"[{dependency}](#{dependency})" for dependency in verified
            ) or "なし"
            lines.append(
                f"- [{item_id}](#{item_id}): 申告された依存 ({claimed_text}) は採用せず、"
                f"根拠で確認できた依存 ({verified_text}) を採用しました。"
                f" {_md(adjustment['reason'])}"
            )
        lines.append("")

    for warning in consistency_warnings:
        owner_id = _text(warning.get("item_id"), "Consistency warning item ID")
        related_id = _text(warning.get("related_item_id"), "Related item ID")
        kind = warning.get("kind")
        if (
            owner_id not in item_by_id or related_id not in item_by_id
            or owner_id == related_id
            or kind not in (None, "duplicate_command_owner", "dependency_cycle")
            or (
                kind is None
                and (
                    item_by_id[related_id].get("status")
                    not in (None, "not_found", "partial", "needs_input")
                    and item_by_id[related_id].get("generation_error") is None
                )
            )
            or (
                kind is None
                and item_by_id[owner_id].get("generation_error") is not None
            )
        ):
            raise ReportError("Consistency warning does not match the draft items.")
    if consistency_warnings:
        lines.extend(["## 項目間の整合性警告", ""])
        lines.extend([
            f"- [{warning['item_id']}](#{warning['item_id']}) と "
            f"[{warning['related_item_id']}](#{warning['related_item_id']}): "
            f"{_md(warning['message'])}"
            for warning in consistency_warnings
        ])
        lines.append("")
    for item in items:
        semantic = draft.get("semantic_reviews", {}).get(item["item_id"])

        def explanation(text: str, field: str, index: int | None = None) -> str:
            if semantic is None:
                return _md(text)
            target = next(
                target for target in semantic["targets"]
                if target["field"] == field and target["index"] == index
            )
            unit_ids = {
                unit["unit_id"] for unit in semantic["units"]
                if unit["target_id"] == target["target_id"]
            }
            claims = [
                claim for review in semantic["reviews"] if review["unit_id"] in unit_ids
                for claim in review["claims"]
            ]
            held = not claims or any(claim["support"] != "supported" for claim in claims)
            pending = any(claim["applicability"] != "confirmed" for claim in claims)
            heading = (
                "**意味検証: 保留（この説明を根拠として採用していません）**"
                if held else "**意味検証: 支持・適用条件未確認（条件付きの説明）**"
                if pending else "**意味検証: 支持・要件上適用可（LLM判定）**"
            )
            result = [heading, "", f"> {_md(text)}" if held else _md(text), ""]
            if not claims:
                result.extend([f"- 検証未完了: {_md(semantic['error'])}", ""])
            support_labels = {
                "supported": "支持", "contradicted": "矛盾", "insufficient_evidence": "根拠不足",
            }
            applicability_labels = {
                "confirmed": "要件上適用可", "conditional": "条件付き", "unknown": "不明",
            }
            for claim in claims:
                result.extend([
                    f"- 主張: {_md(claim['text'])}",
                    f"  - 対象／操作: {_md(claim['target'])}／{_md(claim['operation'])}",
                    f"  - 出典の支持: {support_labels[claim['support']]} / "
                    f"適用性: {applicability_labels[claim['applicability']]}",
                    f"  - 判定理由: {_md(claim['reason'])}",
                ])
                result.extend(f"  - 未解決の適用条件: {_md(value)}" for value in claim["conditions"])
                for proof in claim["evidence"]:
                    atom = draft["semantic_review_evidence"][proof["evidence_id"]]
                    sid = atom["source_id"]
                    cited[sid] = sources[sid]
                    result.extend([
                        f"  - 判断に用いた原文の断片 [{_md(sid)}](#{sid})"
                        "（文字列一致確認済み・この断片単独のPDF照合ではありません）:",
                        f"    > {_md(proof['quote'])}",
                    ])
            result.append("")
            return "\n".join(result)

        generation_error = item.get("generation_error")
        if generation_error is not None:
            partial_components = any(
                _array(item.get(key), key)
                for key in (
                    "configuration", "templates", "verification", "verification_templates",
                    "evidence", "constraints", "withheld_steps", "inference_candidates",
                )
            )
            validation_errors = _array(item.get("validation_errors", []), "Validation errors")
            if not all(isinstance(error, str) and error.strip() for error in validation_errors):
                raise ReportError("Validation errors must be nonempty strings.")
            lines.extend([
                f"## {item['item_id']}", "", _md(item["requirement"]), "",
                "**状態: 生成未完了 (generation_failed)**", "",
                (
                    "この項目の充足状態は判定されていません。表示する部分出力は個別検証済みです。"
                    if partial_components
                    else "この項目の充足状態は判定されておらず、検証済み部分出力はありません。"
                ),
                "",
                "### 生成時の警告", "", _md(generation_error), "",
            ])
            if validation_errors:
                lines.extend(["### 検証で除外した内容", ""])
                lines.extend(f"- {_md(error)}" for error in validation_errors)
                lines.append("")
        else:
            lines.extend([
                f"## {item['item_id']}", "", _md(item["requirement"]), "",
                f"**状態: {STATUS_LABELS[item['status']]} ({item['status']})**", "",
                explanation(item["assessment"], "assessment"), "",
            ])
        for key, title in (
            ("configuration", "設定案"),
            ("templates", "設定テンプレート（未指定値の解決が必要）"),
            ("verification", "確認コマンド"),
            ("verification_templates", "確認テンプレート（未指定値の解決が必要）"),
        ):
            lines.extend([f"### {title}", ""])
            commands = _objects(item.get(key), key)
            command_lines = [_single_line(c.get("cli"), "CLI") for c in commands]
            if key in ("configuration", "templates") and any(
                re.match(r"show(?:\s|$)", cli, re.IGNORECASE)
                for cli in command_lines
            ):
                raise ReportError(
                    "Read-only show commands must be listed under verification, not configuration."
                )
            if not commands:
                lines.extend(["なし。", ""])
                continue
            lines.append(_code("\n".join(command_lines)))
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
        if "reference_status" in item:
            lines.extend(["### コマンドリファレンスの補足（説明用・CLI変更なし）", ""])
            reference_status = item["reference_status"]
            notes = _objects(item.get("reference_notes"), "Reference notes")
            if reference_status not in ("complete", "failed", "not_available"):
                raise ReportError("Invalid reference explanation status.")
            if (reference_status == "complete") != bool(notes):
                raise ReportError("Reference explanation status differs from its notes.")
            error = item.get("reference_generation_error")
            if (reference_status == "failed") != (isinstance(error, str) and bool(error.strip())):
                raise ReportError("Reference failure must retain its generation error.")
            if error:
                lines.extend([f"**補足生成未完了:** {_md(error)}", ""])
            lookup = item.get("reference_lookup", {})
            if not isinstance(lookup, dict):
                raise ReportError("Reference lookup must be an object.")
            if lookup.get("status") in ("unavailable", "no_guide_commands"):
                lines.extend(["照合対象のリファレンス、またはガイドのコマンド構文がありません。", ""])
            queries = _objects(lookup.get("queries", []), "Reference lookup queries")
            if any(
                entry["coverage"] == "partial"
                for query in queries
                for entry in _objects(query.get("coverage", []), "Reference coverage")
            ):
                lines.append("- 参照文書には未解析範囲があります。照合できた範囲だけを根拠にします。")
            for query in queries:
                if query["status"] != "unique_match":
                    lines.append(
                        f"- 未確定: {_md(query['syntax'])} / {_md(query['status'])}"
                        f" / モード: {_md(str(query.get('mode')))}"
                        f" / 親: {_md(str(query.get('parent')))}"
                    )
            repair_errors = _strings(item.get("reference_generation_errors", []), "Reference repair errors")
            if repair_errors and not error:
                lines.extend(["補足応答の再検証履歴:", *[f"- {_md(value)}" for value in repair_errors], ""])
            labels = {
                "default": "デフォルト", "mode": "実行モード",
                "guideline": "留意事項", "conflict": "資料間の矛盾",
                "not_applicable": "適用しない理由",
            }
            for note_index, note in enumerate(notes):
                if note.get("kind") not in labels:
                    raise ReportError("Unknown reference explanation kind.")
                conditions = _strings(note.get("conditions"), "Reference conditions")
                if note["kind"] == "default" and not conditions:
                    raise ReportError("Default explanations require conditions.")
                lines.extend([
                    f"**{labels[note['kind']]}:**",
                    explanation(note["explanation"], "reference_notes", note_index), "",
                ] if semantic is not None else [
                    f"**{labels[note['kind']]}:** {_md(note['explanation'])}", "",
                ])
                lines.extend(f"- 適用条件・未解決の前提: {_md(value)}" for value in conditions)
                citations = _objects(note.get("citations"), "Reference note citations")
                locations = [
                    reference(citation, allow_reference=True) for citation in citations
                ]
                reference_citations = [
                    citation for citation in citations
                    if sources[citation["source_id"]].get("document_type") == "command_reference"
                ]
                if not reference_citations or (
                    note["kind"] == "conflict" and len(reference_citations) == len(citations)
                ):
                    raise ReportError("Reference note citation roles are inconsistent.")
                for citation, location in zip(citations, locations, strict=True):
                    lines.extend([
                        f"- {location}",
                        f"  > {_md(citation['quote'])}", "",
                    ])
            lines.append("")
        for key, title in (("constraints", "注意事項・制約"), ("evidence", "根拠の説明")):
            lines.extend([f"### {title}", ""])
            findings = _objects(item.get(key), key)
            if not findings:
                lines.extend(["なし。", ""])
            for finding_index, finding in enumerate(findings):
                lines.extend([explanation(finding["explanation"], key, finding_index), ""])
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
        for step_index, step in enumerate(withheld):
            lines.extend([_code(_single_line(step.get("source_syntax"), "Source syntax")),
                          explanation(step["reason"], "withheld_steps", step_index), ""])
            lines.extend(f"- {reference(c)}" for c in _objects(step.get("citations"), "Citations"))
            lines.append("")
        lines.extend(["### 推論候補（根拠付き設定とは別・未検証）", ""])
        inferences = _objects(item.get("inference_candidates"), "Inference candidates")
        if not inferences:
            lines.extend(["なし。", ""])
        for inference in inferences:
            lines.extend([_md(inference["description"]), ""])
            lines.extend(
                f"- 成立条件: {_md(condition)}"
                for condition in _array(inference.get("conditions", []), "Conditions")
            )
            lines.extend(f"- 必要な前提: {_md(a)}" for a in _array(inference.get("assumptions"), "Assumptions"))
            lines.append("")
    generation_requests = _objects(
        draft.get("generation_requests", []), "Generation requests",
    )
    if generation_requests:
        outcome_labels = {
            "resolved": "解消",
            "issues_remain": "要修正",
            "no_progress": "進展なし・再試行停止",
            "request_failed": "API失敗",
        }

        def request_issue_map(
            value: Any, label: str, item_ids: set[str],
        ) -> dict[str, list[str]]:
            if not isinstance(value, dict):
                raise ReportError(f"{label} must be an object.")
            result = {}
            for item_id, messages in value.items():
                if item_id not in item_ids:
                    raise ReportError(f"{label} references an item outside its request group.")
                entries = _array(messages, label)
                if not all(isinstance(message, str) and message.strip() for message in entries):
                    raise ReportError(f"{label} must contain nonempty messages.")
                result[item_id] = entries
            return result

        lines.extend(["## 生成ラウンド・リクエスト履歴", ""])
        lines.extend([f"リクエストグループ数: {len(generation_requests)}", ""])
        lines.extend([
            "| ラウンド | リクエスト項目 | 結果 | 検証済み項目 | 部分出力項目 | 応答エラー・残件 |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        seen_requests = set()
        for request in generation_requests:
            round_number = request.get("round")
            kind = request.get("kind")
            if (
                type(round_number) is not int
                or generation_rounds_used is None
                or not 1 <= round_number <= generation_rounds_used
                or kind not in ("initial", "review")
                or (kind == "initial") != (round_number == 1)
            ):
                raise ReportError("Generation request has an invalid round or kind.")
            item_ids = _array(request.get("item_ids"), "Request item IDs")
            if (
                not item_ids
                or not all(isinstance(item_id, str) and item_id in item_by_id for item_id in item_ids)
                or len(set(item_ids)) != len(item_ids)
            ):
                raise ReportError("Generation request must contain unique known item IDs.")
            request_key = (round_number, tuple(sorted(item_ids)))
            if request_key in seen_requests:
                raise ReportError("Generation request groups must be unique within a round.")
            seen_requests.add(request_key)
            outcome = request.get("outcome")
            no_progress = request.get("no_progress")
            if outcome not in outcome_labels or type(no_progress) is not bool:
                raise ReportError("Generation request has an invalid outcome.")
            if (outcome == "no_progress") != no_progress and outcome != "request_failed":
                raise ReportError("Generation request no-progress flag conflicts with its outcome.")
            group_ids = set(item_ids)
            issues = request_issue_map(request.get("issues"), "Request issues", group_ids)
            response_errors = request_issue_map(
                request.get("response_errors", {}), "Response errors", group_ids,
            )
            unresolved = request_issue_map(
                request.get("unresolved_issues"), "Unresolved issues", group_ids,
            )
            validated_ids = _array(
                request.get("validated_item_ids"), "Validated request items",
            )
            partial_ids = _array(
                request.get("partial_item_ids"), "Partially validated request items",
            )
            if (
                not all(isinstance(item_id, str) and item_id in group_ids
                        for item_id in validated_ids + partial_ids)
                or len(set(validated_ids)) != len(validated_ids)
                or len(set(partial_ids)) != len(partial_ids)
                or set(validated_ids) & set(partial_ids)
            ):
                raise ReportError("Generation request validation results are inconsistent.")
            request_error = request.get("request_error")
            if request_error is not None and (
                not isinstance(request_error, str) or not request_error.strip()
            ):
                raise ReportError("Generation request error must be nonempty.")
            outcome_text = outcome_labels[outcome]
            if outcome == "request_failed" and no_progress:
                outcome_text = "API失敗・進展なしで停止"
            elif outcome == "resolved":
                outcome_text = (
                    "初回生成成功" if kind == "initial"
                    else "以前の検証済み結果を保持"
                    if response_errors else "修正で解消"
                )
            response_pairs = {
                (item_id, message)
                for item_id, messages in response_errors.items()
                for message in messages
            }
            unresolved_pairs = {
                (item_id, message)
                for item_id, messages in unresolved.items()
                for message in messages
            }
            issue_lines = []
            for item_id, message in sorted(response_pairs | unresolved_pairs):
                if (item_id, message) in response_pairs and (item_id, message) in unresolved_pairs:
                    label = "応答エラー（未解決）"
                elif (item_id, message) in response_pairs:
                    label = "今回の応答エラー"
                else:
                    label = "未解決"
                issue_lines.append(f"{item_id} {label}: {message}")
            if request_error is not None and not any(
                message == request_error for _, message in response_pairs | unresolved_pairs
            ):
                issue_lines.insert(0, f"API: {request_error}")
            feedback_lines = [
                f"{item_id}: {'; '.join(messages)}"
                for item_id, messages in sorted(issues.items())
            ]
            lines.append(
                f"| {round_number} ({'初回' if kind == 'initial' else '修正'}) "
                f"| {', '.join(f'[{item_id}](#{item_id})' for item_id in item_ids)} "
                f"| {outcome_text} "
                f"| {', '.join(validated_ids) or 'なし'} "
                f"| {', '.join(partial_ids) or 'なし'} "
                f"| {_md(' / '.join(issue_lines) if issue_lines else 'なし')} |"
            )
            if feedback_lines:
                lines.append(
                    f"| - | 修正依頼時の指摘 |  |  |  | {_md(' / '.join(feedback_lines))} |"
                )
        lines.append("")
    lines.extend(["## 次に確認すること", ""])
    lines.extend(f"- {_md(check)}" for check in _array(draft.get("next_checks"), "Next checks"))
    lines.extend(["", "## 制限事項", ""])
    lines.extend(f"- {_md(limit)}" for limit in _array(draft.get("limitations"), "Limitations"))
    lines.extend(["", "## 応答修正履歴", ""])
    repairs = _objects(draft.get("validation_repairs", []), "Validation repairs")
    repair_lines = []
    for repair in repairs:
        round_number = repair.get("round")
        if round_number is not None and (type(round_number) is not int or round_number < 1):
            raise ReportError("Validation repair round must be a positive integer.")
        round_label = f" (round {round_number})" if round_number is not None else ""
        repair_lines.append(
            f"- {_md(repair['item_id'])}{round_label}: {_md(repair['validation_error'])}"
        )
    lines.extend(repair_lines or ["修正依頼なし。"])
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
        ])
        if source.get("document_type") == "command_reference":
            command_source = source["command_source"]
            command_url = url + (f"#{command_source['anchor']}" if command_source["anchor"] else "")
            if command_source["url"] != command_url:
                raise ReportError("Reference command URL differs from its source chapter.")
            lines.extend([
                "- 文書種別: コマンドリファレンス（説明用・保存HTML照合）",
                f"- コマンド: {_md(source['command_name'])}",
                f"- 原文位置: [コマンド項目]({quote(command_url, safe=':/?=&%#')})",
                f"- HTML内位置: {_md(command_source['locator'])}",
                f"- 保存HTML: {_md(source['html_path'])}",
                f"- HTML SHA-256: {_md(source['html_content_sha256'])}",
                "",
            ])
            continue
        lines.extend([
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
    if pdf_verification_status != "not_verified":
        if not pdf_verifications:
            raise ReportError("PDF verification status requires cited evidence.")
        has_unverified_citations = any(
            verification["status"] != "matched"
            for verification in pdf_verifications
        )
        if (
            (pdf_verification_status == "verified" and has_unverified_citations)
            or (pdf_verification_status == "partial" and not has_unverified_citations)
        ):
            raise ReportError("Global PDF verification status does not match citations.")
    if draft.get("html_verification_status", "not_verified") not in ("verified", "not_verified"):
        raise ReportError("Invalid HTML verification status.")
    if (draft.get("html_verification_status") == "verified") != bool(html_verifications):
        raise ReportError("Global HTML verification status does not match citations.")
    return "\n".join(lines).rstrip() + "\n"


def render_report(
    draft: dict[str, Any], pipeline_stages: dict[str, str] | None = None,
) -> str:
    try:
        return _render_report(draft, pipeline_stages)
    except (GenerationError, KeyError, TypeError, ValueError, StopIteration) as error:
        raise ReportError(f"Could not render draft: {error}") from error


def render_pipeline_failure_report(
    requirement: str, pipeline_stages: dict[str, str], error: str,
) -> str:
    if (
        set(pipeline_stages) != set(PIPELINE_STAGE_LABELS)
        or any(status not in PIPELINE_STATUS_LABELS for status in pipeline_stages.values())
    ):
        raise ReportError("Pipeline stage statuses are incomplete or invalid.")
    lines = [
        "# パイプライン実行診断", "",
        "> パイプラインが途中で失敗したため、設定案レポートは生成されていません。",
        "> この診断には未検証の設定コマンドを含みません。", "",
        "## パイプライン実行状況", "",
        "| フェーズ | 結果 |", "| --- | --- |",
        *[
            f"| {PIPELINE_STAGE_LABELS[stage]} (`{stage}`) "
            f"| {PIPELINE_STATUS_LABELS[pipeline_stages[stage]]} |"
            for stage in PIPELINE_STAGE_LABELS
        ],
        "",
        "## 失敗内容", "",
        _md(error), "",
        "## 入力要件", "", _md(requirement), "",
    ]
    return "\n".join(lines)


def _load_draft(path: Path) -> dict[str, Any]:
    try:
        draft = json.loads(path.read_bytes())
    except (UnicodeDecodeError, ValueError) as error:
        raise ReportError(f"Could not read draft JSON: {path}") from error
    if not isinstance(draft, dict):
        raise ReportError("Draft must be a JSON object.")
    return draft


def load_report(
    path: Path, pipeline_stages: dict[str, str] | None = None,
) -> str:
    return render_report(_load_draft(path), pipeline_stages)


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
                    for key in ("manual_path", "source_pdf_path", "html_path"):
                        if isinstance(source.get(key), str):
                            protected.add(Path(source[key]).resolve())
        if output in protected or any(
            output.is_relative_to(Path(root).resolve())
            for root in ("data/okf", "data/source_manuals", "data/command_references", "data/qdrant")
        ):
            raise ReportError("Output must not overwrite the draft, sources or configuration.")
        _write_output(args.output, render_report(draft))
    except (ReportError, SelectionError, OSError) as error:
        print(f"Report generation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
