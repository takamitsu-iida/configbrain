import hashlib
import json
from pathlib import Path
import random
import sys

import pypdfium2
import pytest

from scripts.audit_corpus import (
    AuditError, Markdown, align_pages, audit_page, compact, coverage,
    exclude_pdf_margins, feature_changes, main, markdown_findings, read_pdf, run_audit,
)


def page_text(seed: int) -> str:
    generator = random.Random(seed)
    return " ".join(
        "".join(generator.choices("abcdefghijklmnopqrstuvwxyz", k=10)) for _ in range(90)
    )


def markdown(tmp_path: Path, name: str, text: str) -> Markdown:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return Markdown.read(path)


def pages(texts: list[str]) -> list[dict]:
    return [
        {"page": number, "text": compact(text), "lines": text.splitlines(),
         "images": 0, "ignored_edge_lines": []}
        for number, text in enumerate(texts, 1)
    ]


def package_fixture(tmp_path: Path, document_id: str = "guide") -> tuple[Path, Path]:
    root = tmp_path / "product"
    raw = root / "raw" / document_id
    raw.mkdir(parents=True)
    document = pypdfium2.PdfDocument.new()
    document.new_page(200, 200).close()
    document.save(raw / "source.pdf")
    document.close()
    extracted = root / "extracted" / document_id
    extracted.mkdir(parents=True)
    extracted.joinpath("source.md").write_text("No PDF text available.\n", encoding="utf-8")
    package = root / "packages" / document_id
    package.mkdir(parents=True)
    manual = package / "manual.md"
    manual.write_text("No PDF text available.\n", encoding="utf-8")
    descriptor = {
        "name": document_id, "profile": "data-package",
        "homepage": "https://example.test/guide.pdf",
        "resources": [{
            "name": "manual", "path": "manual.md", "type": "text",
            "hash": "sha256:" + hashlib.sha256(manual.read_bytes()).hexdigest(),
            "bytes": manual.stat().st_size,
        }],
    }
    package.joinpath("datapackage.json").write_text(json.dumps(descriptor), encoding="utf-8")
    return root, manual


def test_alignment_covers_every_page_with_bounded_line_references(tmp_path: Path) -> None:
    texts = [page_text(1), page_text(2), page_text(3)]
    target = markdown(tmp_path, "manual.md", "\n\n".join(texts))
    pdf_pages = pages(texts)
    mappings = align_pages(pdf_pages, target)
    assert len(mappings) == 3
    assert all(mapping["status"] == "aligned" for mapping in mappings)
    assert mappings[0]["anchor_lines"] == [1, 1]
    assert mappings[1]["anchor_lines"] == [3, 3]
    assert mappings[2]["anchor_lines"] == [5, 5]
    for page, mapping in zip(pdf_pages, mappings):
        result = audit_page(page, target, target, mapping, mapping)
        assert result["status"] == "checked"
        assert result["coverage"]["package"]["ratio"] == 1


def test_deleted_page_cannot_be_matched_to_a_repeated_command_elsewhere(tmp_path: Path) -> None:
    command = "\nDevice(config)# interface vlan 99\n"
    texts = [page_text(11) + command, page_text(12) + command, page_text(13)]
    target = markdown(tmp_path, "manual.md", "\n\n".join([texts[0], texts[2]]))
    mappings = align_pages(pages(texts), target)
    assert mappings[1]["status"] == "unverifiable"
    result = audit_page(pages(texts)[1], target, target, mappings[1], mappings[1])
    assert result["status"] == "unverifiable"
    assert result["coverage"] == {}


def test_duplicate_text_and_reordered_pages_are_not_silently_aligned(tmp_path: Path) -> None:
    first, second = page_text(21), page_text(22)
    target = markdown(tmp_path, "manual.md", second + "\n\n" + first)
    assert all(mapping["status"] == "unverifiable"
               for mapping in align_pages(pages([first, second]), target))
    duplicate = markdown(tmp_path, "duplicate.md", first + "\n\n" + first)
    assert all(mapping["status"] == "unverifiable"
               for mapping in align_pages(pages([first, first]), duplicate))


def test_reports_small_numeric_cli_changes_and_missing_constraints(tmp_path: Path) -> None:
    base = page_text(30)
    text = base + "\nDevice(config)# interface vlan 99\n" + (
        "Caution You must not enable this feature without configuring authentication.\n"
    )
    source = markdown(tmp_path, "source.md", text)
    target = markdown(tmp_path, "manual.md", base + "\n```console\n"
                      "Device(config)# interface vlan 98\n```\n")
    pdf_pages = pages([text])
    result = audit_page(
        pdf_pages[0], source, target,
        align_pages(pdf_pages, source)[0], align_pages(pdf_pages, target)[0],
    )
    kinds = {finding["kind"] for finding in result["findings"]}
    assert result["status"] == "review_required"
    assert {"possible_missing_or_changed_prompt_command",
            "possible_missing_constraint_or_alert", "pdf_text_fragments_not_found"} <= kinds
    assert result["coverage"]["extracted"]["ratio"] == 1
    assert result["coverage"]["package"]["ratio"] < 1


def test_cli_punctuation_is_not_ignored(tmp_path: Path) -> None:
    text = page_text(31) + "\nDevice(config)# ip address 10.1.1.1 255.255.255.0\n"
    source = markdown(tmp_path, "source.md", text)
    target = markdown(tmp_path, "manual.md", text.replace("10.1.1.1", "101.1.1"))
    pdf_pages = pages([text])
    result = audit_page(pdf_pages[0], source, target,
                        align_pages(pdf_pages, source)[0], align_pages(pdf_pages, target)[0])
    assert any(f["kind"] == "possible_missing_or_changed_prompt_command"
               for f in result["findings"])


def test_downstream_body_additions_and_deletions_are_audited(tmp_path: Path) -> None:
    first, middle, last = page_text(51), page_text(52), page_text(53)
    text = first + "\n" + middle + "\n" + last
    changed = first + "\n" + page_text(54) + "\n" + last
    source = markdown(tmp_path, "source.md", text)
    target = markdown(tmp_path, "manual.md", changed)
    pdf_pages = pages([text])
    result = audit_page(pdf_pages[0], source, target,
                        align_pages(pdf_pages, source)[0], align_pages(pdf_pages, target)[0])
    directions = {finding["direction"] for finding in result["findings"]
                  if finding["kind"] == "possible_transformation_text_change"}
    assert directions == {"extracted_to_package", "package_to_extracted"}
    assert result["coverage"]["extracted"]["ratio"] == 1
    assert result["coverage"]["package"]["ratio"] < 0.9


def test_images_require_visual_review_even_when_text_matches(tmp_path: Path) -> None:
    text = page_text(60)
    target = markdown(tmp_path, "manual.md", text)
    pdf_pages = pages([text])
    pdf_pages[0]["images"] = 1
    mapping = align_pages(pdf_pages, target)[0]
    result = audit_page(pdf_pages[0], target, target, mapping, mapping)
    assert result["status"] == "review_required"
    assert result["findings"] == [{"kind": "visual_review_required", "pdf_images": 1}]


def test_footer_exclusion_uses_coordinates_not_text_extraction_order() -> None:
    pdf_pages = [
        {"raw": f"Repeated body constraint\nUnique content {i}\nGuide footer\n{i}\nChapter header",
         "margin_indices": [2, 3, 4]}
        for i in range(1, 4)
    ]
    exclude_pdf_margins(pdf_pages)
    for page in pdf_pages:
        assert page["lines"][0] == "Repeated body constraint"
        assert len(page["lines"]) == 2
        assert "Guide footer" in page["ignored_edge_lines"]
        assert "Chapter header" in page["ignored_edge_lines"]


def test_structure_features_and_coverage_include_tail(tmp_path: Path) -> None:
    source = markdown(tmp_path, "source.md",
                      '# Heading\n<span id="original"></span>\n![](_page_1_Picture_0.jpeg)\n')
    target = markdown(tmp_path, "manual.md",
                      "| Command or Action Purpose | extra |\n| --- | --- |\n"
                      "| one |\n```console\nDevice# enable\n")
    assert {f["kind"] for f in markdown_findings(target)} == {
        "invalid_code_fence", "inconsistent_table_columns", "merged_procedure_header",
    }
    assert {f["kind"] for f in feature_changes(source, target)} == {
        "heading_count_changed", "anchor_count_changed", "image_count_changed",
    }
    assert coverage("a" * 100 + "changedtail", "a" * 100)["missing"] > 0


def test_frontmatter_is_not_used_as_a_pdf_text_anchor(tmp_path: Path) -> None:
    body = page_text(41)
    target = markdown(tmp_path, "manual.md", '---\n{"title":"not body"}\n---\n' + body)
    assert target.text == compact(body)
    assert align_pages(pages([body]), target)[0]["anchor_lines"] == [4, 4]
    with pytest.raises(AuditError, match="Unclosed frontmatter"):
        markdown(tmp_path, "broken.md", "---\nunfinished")


def test_real_blank_pdf_is_unverifiable_and_resume_revalidates_inputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root, manual = package_fixture(tmp_path)
    original = manual.read_bytes()
    output = tmp_path / "audit"
    report = run_audit(root, output)
    assert report["status"] == "completed"
    assert report["page_status_counts"] == {"unverifiable": 1}
    assert report["page_count"] == 1
    assert json.loads((output / "documents" / "guide.json").read_text())["pages"][0]["coverage"] == {}
    run_audit(root, output, resume=True)
    assert "[resumed]" in capsys.readouterr().out
    assert manual.read_bytes() == original
    with pytest.raises(FileExistsError):
        run_audit(root, output)
    manual.write_text("Changed without updating checksum.\n", encoding="utf-8")
    report = run_audit(root, output, resume=True)
    assert report["status"] == "completed_with_errors"
    detail = json.loads((output / "documents" / "guide.json").read_text())
    assert detail["package_validation"]["status"] == "error"
    assert "hash" in detail["package_validation"]["error"].lower()


def test_continues_after_missing_inputs_and_cli_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = package_fixture(tmp_path)
    (root / "packages" / "missing-inputs").mkdir()
    output = tmp_path / "audit"
    monkeypatch.setattr(sys, "argv", ["audit_corpus", "--data-root", str(root),
                                    "--output-dir", str(output)])
    assert main() == 1
    report = json.loads((output / "manifest.json").read_text())
    assert report["document_count"] == report["expected_documents"] == 2
    assert report["page_count"] == 1
    assert report["documents"][1]["status"] == "error"
    assert not (output / ".audit.lock").exists()
    (root / "packages" / "missing-inputs").rmdir()
    monkeypatch.setattr(sys, "argv", ["audit_corpus", "--data-root", str(root),
                                    "--output-dir", str(output), "--resume"])
    assert main() == 2
    with pytest.raises(AuditError, match="outside"):
        run_audit(root, root / "audit")


def test_pdf_page_text_errors_are_recorded_not_treated_as_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = package_fixture(tmp_path)
    def fail(self: pypdfium2.PdfPage) -> None:
        raise ValueError("text failed")

    monkeypatch.setattr(pypdfium2.PdfPage, "get_textpage", fail)
    result = read_pdf(next((root / "raw").rglob("*.pdf")))
    assert result[0]["error"] == "text failed"
