import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from configbrain.verify_pdf import PdfVerificationError, verify_pdf_citations


class FakeTextPage:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_text_range(self) -> str:
        return self.text

    def close(self) -> None:
        pass


class FakePage:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_textpage(self) -> FakeTextPage:
        return FakeTextPage(self.text)


class FakePdfDocument:
    pages: list[str] = []

    def __init__(self, _path: Path) -> None:
        self.document = [FakePage(text) for text in self.pages]

    def __len__(self) -> int:
        return len(self.document)

    def __getitem__(self, index: int) -> FakePage:
        return self.document[index]

    def close(self) -> None:
        pass


def test_reference_only_citations_do_not_open_or_claim_pdf_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_pdf(_path: Path):
        pytest.fail("Reference-only citations must not open a PDF.")

    monkeypatch.setattr("configbrain.verify_pdf.pypdfium2.PdfDocument", unexpected_pdf)
    source_id = "reference-source-" + "a" * 64
    evidence_id = "reference-evidence-v1-" + "b" * 64
    citation = {
        "source_id": source_id, "evidence_id": evidence_id, "quote": "Ethernet is the default.",
        "html_verification": {"status": "matched"},
        "pdf_verification": {"status": "matched"},
    }
    draft = {
        "sources": {source_id: {"document_type": "command_reference"}},
        "items": [{"reference_notes": [{"citations": [citation]}]}],
    }
    evidence = {"evidence_atoms": {
        evidence_id: {"source_id": source_id, "text": citation["quote"]},
    }}

    verify_pdf_citations(draft, evidence)

    assert draft["pdf_verification_status"] == "not_verified"
    assert "pdf_verification" not in citation
    assert citation["html_verification"] == {"status": "matched"}


def build_case(
    tmp_path: Path, pages: list[str], next_chapter_page: int | None = 4,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    pdf_path = tmp_path / "guide.pdf"
    pdf_path.write_bytes(b"PDF source fixture")
    pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    manual_path = tmp_path / "manual.md"
    manual_path.write_text("# Fixture\n", encoding="utf-8")
    (tmp_path / "provenance.json").write_text(
        json.dumps({
            "document": {"document_id": "guide"},
            "source_pdf": {"sha256": pdf_hash},
            "chapters": [
                {"order": 1, "pdf_page_mapping": {"status": "matched", "page_index": 2}},
                {
                    "order": 2,
                    "pdf_page_mapping": {
                        "status": "matched" if next_chapter_page is not None else "unmapped",
                        "page_index": next_chapter_page,
                    },
                },
            ],
        }),
        encoding="utf-8",
    )
    evidence_id = "evidence-v1-" + "a" * 64
    fragments = ["Step 5", "ip address 192.0.2.1", "Configures the IP address."]
    source = {
        "document_id": "guide", "chapter_order": 1, "manual_path": str(manual_path),
        "source_pdf_path": str(pdf_path), "source_pdf_sha256": pdf_hash,
        "pdf_page_index": 2, "pdf_page_scope": "chapter_start",
    }
    draft = {
        "sources": {"source-1": source},
        "items": [{
            "configuration": [{
                "citations": [{
                    "source_id": "source-1", "evidence_id": evidence_id,
                    "quote": "Step 5 ip address 192.0.2.1 Configures the IP address.",
                }],
            }],
        }],
    }
    evidence = {
        "evidence_atoms": {
            evidence_id: {
                "source_id": "source-1", "text": draft["items"][0]["configuration"][0]
                ["citations"][0]["quote"], "pdf_match_fragments": fragments,
            },
        },
    }
    FakePdfDocument.pages = pages
    return draft, evidence, pdf_hash


def test_pdf_verification_finds_a_unique_page_within_the_chapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "configbrain.verify_pdf.pypdfium2.PdfDocument", FakePdfDocument,
    )
    draft, evidence, _ = build_case(
        tmp_path,
        [
            "Step 5 ip address 192.0.2.1 Configures the IP address.",
            "Chapter heading",
            "Step 5, ip address 192.0.2.1. Configures the IP address.",
            "End of chapter",
        ],
    )

    verify_pdf_citations(draft, evidence)

    citation = draft["items"][0]["configuration"][0]["citations"][0]
    assert draft["pdf_verification_status"] == "verified"
    assert draft["pdf_verification_version"] == 1
    assert citation["pdf_verification"] == {
        "status": "matched", "page_indexes": [3], "fragment_count": 3,
        "method": "normalized_text_fragments",
    }


def test_pdf_verification_marks_multiple_matching_pages_as_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "configbrain.verify_pdf.pypdfium2.PdfDocument", FakePdfDocument,
    )
    draft, evidence, _ = build_case(
        tmp_path,
        [
            "Cover",
            "Step 5 ip address 192.0.2.1 Configures the IP address.",
            "Step 5 ip address 192.0.2.1 Configures the IP address.",
            "End of chapter",
        ],
    )

    verify_pdf_citations(draft, evidence)

    citation = draft["items"][0]["configuration"][0]["citations"][0]
    assert draft["pdf_verification_status"] == "partial"
    assert citation["pdf_verification"]["status"] == "ambiguous"
    assert citation["pdf_verification"]["page_indexes"] == [2, 3]


def test_pdf_verification_marks_missing_text_without_claiming_a_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "configbrain.verify_pdf.pypdfium2.PdfDocument", FakePdfDocument,
    )
    draft, evidence, _ = build_case(tmp_path, ["Cover", "Unrelated content", "Other"])

    verify_pdf_citations(draft, evidence)

    citation = draft["items"][0]["configuration"][0]["citations"][0]
    assert draft["pdf_verification_status"] == "partial"
    assert citation["pdf_verification"]["status"] == "not_found"
    assert citation["pdf_verification"]["page_indexes"] == []


def test_pdf_verification_does_not_search_past_an_unmapped_chapter_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "configbrain.verify_pdf.pypdfium2.PdfDocument", FakePdfDocument,
    )
    draft, evidence, _ = build_case(
        tmp_path, ["Cover", "Step 5 ip address 192.0.2.1 Configures the IP address.", "Other"],
        next_chapter_page=None,
    )

    verify_pdf_citations(draft, evidence)

    citation = draft["items"][0]["configuration"][0]["citations"][0]
    assert draft["pdf_verification_status"] == "partial"
    assert citation["pdf_verification"]["status"] == "unmapped"
    assert citation["pdf_verification"]["page_indexes"] == []


def test_pdf_verification_rejects_a_changed_source_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "configbrain.verify_pdf.pypdfium2.PdfDocument", FakePdfDocument,
    )
    draft, evidence, _ = build_case(tmp_path, ["Cover", "Text", "Other"])
    pdf_path = Path(draft["sources"]["source-1"]["source_pdf_path"])
    pdf_path.write_bytes(b"changed")

    with pytest.raises(PdfVerificationError, match="SHA-256 mismatch"):
        verify_pdf_citations(draft, evidence)
