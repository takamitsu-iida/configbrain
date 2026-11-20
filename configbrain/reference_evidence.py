"""Register explanatory command evidence and verify it against saved HTML."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from configbrain.command_references import (
    EXTRACTOR_VERSION, _text as reference_text, extract_command_chapter,
)
from configbrain.manual_errors import ConversionError
from configbrain.verify_pdf import _citations

REFERENCE_EVIDENCE_VERSION = 1
HTML_VERIFICATION_VERSION = 1


class ReferenceEvidenceError(RuntimeError):
    """Raised when a reference explanation cannot be traced to saved HTML."""


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _source_id(
    document_id: str, command_id: str, html_sha256: str, url: str,
) -> str:
    return "reference-source-" + _digest({
        "document_id": document_id, "command_id": command_id,
        "html_sha256": html_sha256, "url": url,
    })


def _spans(command: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    spans = [(reference_text(BeautifulSoup(command["content_html"], "html.parser")), {
        "kind": "reference_command", "sources": [command["source"]],
    })]
    spans.extend(
        (section["text"], {
            "kind": "reference_section", "section_index": index,
            "section_kind": section["kind"], "sources": section["sources"],
        })
        for index, section in enumerate(command["sections"], 1)
    )
    spans.extend(
        (child["text"], {
            "kind": "reference_subcommand", "name": child["name"],
            "parent_command_id": command["command_id"], "sources": [child["source"]],
        })
        for child in command["subcommands"]
    )
    return spans


def _atom_id(source: dict[str, Any], text: str, locator: dict[str, Any]) -> str:
    return f"reference-evidence-v{REFERENCE_EVIDENCE_VERSION}-" + _digest({
        "source_id": source["source_id"], "html_sha256": source["html_content_sha256"],
        "command_id": source["command_id"], "text": text, "locator": locator,
    })


def register_reference_evidence(
    contexts: dict[str, Any], item: dict[str, Any],
    sources: dict[str, dict[str, Any]], atoms: dict[str, dict[str, Any]],
    reference_root: Path,
) -> dict[str, Any]:
    lookup = item.get("command_references", {"status": "unavailable", "queries": []})
    source_ids: list[str] = []
    evidence_ids: list[str] = []
    queries = []
    for query in lookup["queries"]:
        guide = query["guide"]
        queries.append({
            "syntax": guide["syntax"], "name": query["name"],
            "mode": guide.get("mode"), "parent": guide.get("parent"),
            "status": query["status"],
            "retrieval_reasons": [entry["retrieval_reason"] for entry in query["adopted"]],
            "coverage": [
                {"document_id": result["document_id"], "coverage": result["coverage"]}
                for result in query["results"]
            ],
        })
        if query["status"] != "unique_match":
            if query["adopted"]:
                raise ReferenceEvidenceError("Uncertain reference matches cannot be adopted.")
            continue
        matches = [
            (result, match)
            for result in query["results"] for match in result["matches"]
        ]
        if len(matches) != 1 or len(query["adopted"]) != 1 or not guide.get("mode"):
            raise ReferenceEvidenceError("Adopted reference must have one mode-resolved match.")
        result, match = matches[0]
        command = match["command"]
        child = match["matched_subcommand"]
        adopted = query["adopted"][0]
        if (
            result["status"] != "unique_match"
            or result["product"] != contexts["product"]
            or result["release"] != contexts["release"]
            or adopted["document_id"] != result["document_id"]
            or adopted["command_id"] != command["command_id"]
            or adopted["source"] != command["source"]
            or (child is not None and not guide.get("parent"))
        ):
            raise ReferenceEvidenceError("Adopted reference identity or scope is inconsistent.")
        metadata = [
            source for source in result["sources"]
            if source["order"] == command["source"]["chapter_order"]
        ]
        if len(metadata) != 1:
            raise ReferenceEvidenceError("Reference command must have unique HTML provenance.")
        chapter = metadata[0]
        document_id = result["document_id"]
        source_id = _source_id(
            document_id, command["command_id"], chapter["html_sha256"], chapter["url"],
        )
        sources[source_id] = {
            "source_id": source_id, "document_type": "command_reference",
            "document_id": document_id, "document_title": result["document_title"],
            "product": contexts["product"], "release": contexts["release"],
            "chapter_order": chapter["order"], "chapter_title": chapter["title"],
            "chapter_url": chapter["url"], "section_path": [command["name"]],
            "roles": ["command_explanation"], "command_id": command["command_id"],
            "command_name": command["name"], "command_source": command["source"],
            "content_html": command["content_html"],
            "html_path": str((reference_root / document_id / chapter["html_file"]).resolve()),
            "html_content_sha256": chapter["html_sha256"],
            "extractor_version": result["extractor_version"],
        }
        source = sources[source_id]
        source_ids.append(source_id)
        for text, locator in _spans(command):
            if locator["kind"] == "reference_subcommand" and (
                child is None or locator["sources"] != [child["source"]]
            ):
                continue
            if not text.strip():
                continue
            evidence_id = _atom_id(source, text, locator)
            atoms[evidence_id] = {
                "evidence_id": evidence_id, "source_id": source_id,
                "text": text, "locator": locator, "evidence_role": "explanation_only",
            }
            evidence_ids.append(evidence_id)
    return {
        "reference_source_ids": list(dict.fromkeys(source_ids)),
        "reference_evidence_ids": list(dict.fromkeys(evidence_ids)),
        "reference_lookup": {"status": lookup["status"], "queries": queries},
    }


def verify_reference_citations(draft: dict[str, Any], evidence: dict[str, Any]) -> None:
    sources = draft["sources"]
    atoms = evidence["evidence_atoms"]
    chapters: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    count = 0
    for citation in _citations(draft["items"]):
        source = sources.get(citation["source_id"])
        if not isinstance(source, dict):
            raise ReferenceEvidenceError("Unknown citation source.")
        if source.get("document_type") != "command_reference":
            continue
        if source.get("extractor_version") != EXTRACTOR_VERSION:
            raise ReferenceEvidenceError("Reference extractor version does not match.")
        if source.get("source_id") != citation["source_id"] or _source_id(
            source["document_id"], source["command_id"],
            source["html_content_sha256"], source["chapter_url"],
        ) != citation["source_id"]:
            raise ReferenceEvidenceError("Reference source identity does not match.")
        atom = atoms.get(citation["evidence_id"])
        if (
            not isinstance(atom, dict) or atom["source_id"] != citation["source_id"]
            or atom.get("evidence_id") != citation["evidence_id"]
            or atom.get("evidence_role") != "explanation_only"
            or atom["text"] != citation["quote"] or atom["locator"] != citation.get("locator")
            or _atom_id(source, atom["text"], atom["locator"]) != citation["evidence_id"]
        ):
            raise ReferenceEvidenceError("Reference citation differs from its evidence registry.")
        key = (
            source["html_path"], source["html_content_sha256"],
            source["chapter_order"], source["chapter_url"],
        )
        if key not in chapters:
            try:
                raw = Path(source["html_path"]).read_bytes()
            except OSError as error:
                raise ReferenceEvidenceError(f"Could not read reference HTML: {key[0]}") from error
            if hashlib.sha256(raw).hexdigest() != source["html_content_sha256"]:
                raise ReferenceEvidenceError(f"Reference HTML SHA-256 mismatch: {key[0]}")
            try:
                chapters[key] = extract_command_chapter(raw, {
                    "order": source["chapter_order"], "title": source["chapter_title"],
                    "url": source["chapter_url"],
                })
            except ConversionError as error:
                raise ReferenceEvidenceError(str(error)) from error
        commands = [
            command for command in chapters[key]["commands"]
            if command["command_id"] == source["command_id"]
        ]
        if (
            len(commands) != 1 or commands[0]["content_html"] != source["content_html"]
            or commands[0]["source"] != source["command_source"]
            or (atom["text"], atom["locator"]) not in _spans(commands[0])
        ):
            raise ReferenceEvidenceError("Reference citation is outside its saved command span.")
        citation.pop("pdf_verification", None)
        citation["html_verification"] = {
            "status": "matched", "method": "stored_html_command_span",
            "html_sha256": source["html_content_sha256"],
            "command_id": source["command_id"],
        }
        count += 1
    draft["html_verification_status"] = "verified" if count else "not_verified"
    draft["html_verification_version"] = HTML_VERIFICATION_VERSION
