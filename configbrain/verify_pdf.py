"""Verify cited evidence text against the original source PDF."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pypdfium2

PDF_VERIFICATION_VERSION = 1


class PdfVerificationError(RuntimeError):
    """Raised when an original source PDF cannot be verified safely."""


def _normalized(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise PdfVerificationError(f"Could not read source PDF: {path}") from error
    return digest.hexdigest()


def _page_end_index(source: dict[str, Any], start: int) -> tuple[int | None, bool]:
    manual_path_value = source.get("manual_path")
    chapter_order = source.get("chapter_order")
    if (
        not isinstance(manual_path_value, str) or not manual_path_value
        or type(chapter_order) is not int or chapter_order < 1
    ):
        raise PdfVerificationError("Source manual path or chapter order is invalid.")
    manual_path = Path(manual_path_value)
    provenance_path = manual_path.with_name("provenance.json")
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PdfVerificationError(
            f"Could not read PDF chapter mapping: {provenance_path}"
        ) from error

    document = provenance.get("document") if isinstance(provenance, dict) else None
    source_pdf = provenance.get("source_pdf") if isinstance(provenance, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("document_id") != source.get("document_id")
        or not isinstance(source_pdf, dict)
        or source_pdf.get("sha256") != source.get("source_pdf_sha256")
    ):
        raise PdfVerificationError("PDF chapter mapping does not match the cited source.")
    chapters = provenance.get("chapters")
    if not isinstance(chapters, list):
        raise PdfVerificationError("PDF chapter mapping has no chapter list.")
    current = [
        chapter for chapter in chapters
        if isinstance(chapter, dict) and chapter.get("order") == chapter_order
    ]
    if len(current) != 1:
        raise PdfVerificationError("PDF chapter mapping is missing or ambiguous.")
    mapping = current[0].get("pdf_page_mapping")
    if (
        not isinstance(mapping, dict)
        or mapping.get("status") != "matched"
        or mapping.get("page_index") != start
    ):
        raise PdfVerificationError("PDF chapter start page differs from its mapping.")

    next_chapters = [
        chapter for chapter in chapters
        if isinstance(chapter, dict)
        and type(chapter.get("order")) is int
        and chapter["order"] > chapter_order
    ]
    if not next_chapters:
        return None, True
    next_chapter = min(next_chapters, key=lambda chapter: chapter["order"])
    next_mapping = next_chapter.get("pdf_page_mapping")
    if (
        not isinstance(next_mapping, dict)
        or next_mapping.get("status") != "matched"
        or type(next_mapping.get("page_index")) is not int
        or next_mapping["page_index"] <= start
    ):
        return None, False
    return next_mapping["page_index"] - 1, True


def _extract_pages(path: Path) -> list[str]:
    try:
        pdf = pypdfium2.PdfDocument(path)
    except (OSError, pypdfium2.PdfiumError) as error:
        raise PdfVerificationError(f"Could not open source PDF: {path}") from error

    pages = []
    try:
        for page_index in range(len(pdf)):
            text_page = pdf[page_index].get_textpage()
            try:
                pages.append(_normalized(text_page.get_text_range()))
            finally:
                text_page.close()
    except pypdfium2.PdfiumError as error:
        raise PdfVerificationError(f"Could not extract source PDF text: {path}") from error
    finally:
        pdf.close()
    return pages


def _citations(value: Any):
    if isinstance(value, dict):
        if all(key in value for key in ("source_id", "evidence_id", "quote")):
            yield value
        for child in value.values():
            yield from _citations(child)
    elif isinstance(value, list):
        for child in value:
            yield from _citations(child)


def verify_pdf_citations(
    draft: dict[str, Any], evidence: dict[str, Any],
) -> None:
    """Attach per-citation PDF text matches and summarize the verification state."""
    atoms = evidence.get("evidence_atoms")
    sources = draft.get("sources")
    if not isinstance(atoms, dict) or not isinstance(sources, dict):
        raise PdfVerificationError("Evidence atoms and draft sources are required.")

    citations = list(_citations(draft.get("items", [])))
    citations.extend(_citations(draft.get("requirement_graph", {})))
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for citation in citations:
        key = citation["source_id"], citation["evidence_id"]
        if key in unique and unique[key]["quote"] != citation["quote"]:
            raise PdfVerificationError("One evidence ID has conflicting citation text.")
        unique[key] = citation

    if not unique:
        draft["pdf_verification_status"] = "not_verified"
        draft["pdf_verification_version"] = PDF_VERIFICATION_VERSION
        return

    results: dict[tuple[str, str], dict[str, Any]] = {}
    pages_by_path: dict[str, list[str]] = {}
    expected_hash_by_path: dict[str, str] = {}
    page_ranges_by_source: dict[str, tuple[int | None, bool]] = {}
    for (source_id, evidence_id), citation in unique.items():
        source = sources.get(source_id)
        atom = atoms.get(evidence_id)
        if (
            not isinstance(source, dict) or not isinstance(atom, dict)
            or atom.get("source_id") != source_id
            or atom.get("text") != citation["quote"]
        ):
            raise PdfVerificationError("Citation does not match its evidence registry.")

        if source.get("document_type") == "command_reference":
            continue
        page_start = source.get("pdf_page_index")
        if page_start is None:
            results[(source_id, evidence_id)] = {
                "status": "unmapped", "page_indexes": [], "fragment_count": 0,
                "method": "normalized_text_fragments",
            }
            continue
        if type(page_start) is not int or page_start < 1:
            raise PdfVerificationError("Source PDF chapter start page is invalid.")

        path_text = source.get("source_pdf_path")
        expected_hash = source.get("source_pdf_sha256")
        if (
            not isinstance(path_text, str) or not path_text
            or not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(character not in "0123456789abcdef" for character in expected_hash)
        ):
            raise PdfVerificationError("Source PDF path or SHA-256 is invalid.")
        path = str(Path(path_text))
        if path not in pages_by_path:
            if _sha256_file(Path(path)) != expected_hash:
                raise PdfVerificationError(f"Source PDF SHA-256 mismatch: {path}")
            pages_by_path[path] = _extract_pages(Path(path))
            expected_hash_by_path[path] = expected_hash
        elif expected_hash_by_path[path] != expected_hash:
            raise PdfVerificationError("One source PDF path has conflicting SHA-256 values.")

        if source_id not in page_ranges_by_source:
            page_ranges_by_source[source_id] = _page_end_index(source, page_start)
        page_end, page_range_bounded = page_ranges_by_source[source_id]
        if not page_range_bounded:
            results[(source_id, evidence_id)] = {
                "status": "unmapped", "page_indexes": [],
                "fragment_count": 0, "method": "normalized_text_fragments",
            }
            continue
        if page_end is not None and (type(page_end) is not int or page_end < page_start):
            raise PdfVerificationError("Source PDF chapter page range is invalid.")
        pdf_pages = pages_by_path[path]
        if page_start > len(pdf_pages) or (
            page_end is not None and page_end > len(pdf_pages)
        ):
            raise PdfVerificationError("Source PDF chapter page range exceeds the document.")

        fragments = atom.get("pdf_match_fragments")
        if not isinstance(fragments, list) or not fragments or any(
            not isinstance(fragment, str) or not fragment.strip()
            for fragment in fragments
        ):
            raise PdfVerificationError("Evidence has no valid PDF text fragments.")
        normalized_fragments = list(dict.fromkeys(
            normalized for fragment in fragments
            if (normalized := _normalized(fragment))
        ))
        if not normalized_fragments:
            raise PdfVerificationError("Evidence has no searchable PDF text fragments.")

        end = page_end if page_end is not None else len(pdf_pages)
        matches = [
            page_index
            for page_index in range(page_start, end + 1)
            if all(
                fragment in pdf_pages[page_index - 1]
                for fragment in normalized_fragments
            )
        ]
        status = (
            "matched" if len(matches) == 1
            else "ambiguous" if matches
            else "not_found"
        )
        results[(source_id, evidence_id)] = {
            "status": status,
            "page_indexes": matches,
            "fragment_count": len(normalized_fragments),
            "method": "normalized_text_fragments",
        }

    for citation in citations:
        key = citation["source_id"], citation["evidence_id"]
        if key in results:
            citation["pdf_verification"] = results[key]
        else:
            citation.pop("pdf_verification", None)
    draft["pdf_verification_status"] = (
        "verified"
        if results and all(result["status"] == "matched" for result in results.values())
        else "partial" if results else "not_verified"
    )
    draft["pdf_verification_version"] = PDF_VERIFICATION_VERSION
    old_limitation = (
        "PDF hashes are checked, but PDF content and exact procedure pages are not verified."
    )
    limitations = draft.get("limitations")
    if results and isinstance(limitations, list):
        draft["limitations"] = [
            limitation for limitation in limitations if limitation != old_limitation
        ]
        draft["limitations"].append(
            "PDF引用は抽出テキストの断片を同一物理ページ上で照合します。"
            "PDFの視覚的なレイアウト、意味、適用性は保証しません。"
        )
