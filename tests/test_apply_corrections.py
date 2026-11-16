import copy
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from scripts import apply_corrections as corrections
from scripts.check_pdf_markdown_quality import QualityError


def small_pdf() -> bytes:
    content = b"BT /F1 10 Tf 20 700 Td (Complete PDF evidence for reviewing procedure commands and explanations.) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    result = b"%PDF-1.4\n"
    offsets = []
    for number, value in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{number} 0 obj\n".encode() + value + b"\nendobj\n"
    start = len(result)
    result += b"xref\n0 6\n0000000000 65535 f \n"
    result += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    return result + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()


@dataclass
class Case:
    path: Path
    spec: dict[str, Any]
    source: Path
    pdf: Path
    output: Path

    def save(self) -> None:
        self.path.write_text(json.dumps(self.spec), encoding="utf-8")

    def replace(self, content: bytes) -> None:
        entry = self.spec["corrections"][0]["replacement"]
        (self.path.parent / entry["path"]).write_bytes(content)
        entry["sha256"] = corrections.digest(content)
        self.save()

    def run(self, *, apply: bool = True) -> dict[str, Any]:
        return corrections.run_corrections(self.path, self.output, apply=apply)


@pytest.fixture
def make_case(tmp_path: Path):
    def create(
        source: bytes = b"PREFIX\nold-one\nBETWEEN\nold-two\nSUFFIX",
        patches: list[tuple[int, int, bytes]] | None = None,
    ) -> Case:
        original_dir = tmp_path / "inputs"
        original_dir.mkdir()
        correction_dir = tmp_path / "corrections"
        correction_dir.mkdir()
        markdown = original_dir / "source.md"
        markdown.write_bytes(source)
        pdf = original_dir / "source.pdf"
        pdf.write_bytes(small_pdf())
        values = []
        for number, (first, last, content) in enumerate(patches or [(2, 2, b"new-one\n")]):
            replacement = correction_dir / f"replacement-{number}.md"
            replacement.write_bytes(content)
            values.append({
                "id": f"patch-{number}", "reason": "Restore a PDF-grounded procedure.",
                "pdf_pages": [1, 1],
                "target": {
                    "lines": [first, last],
                    "sha256": corrections.digest(b"".join(source.splitlines(keepends=True)[first - 1:last])),
                },
                "replacement": {"path": replacement.name, "sha256": corrections.digest(content)},
                "review": {
                    "status": "approved", "pdf_compared": True,
                    "complete_procedure_checked": True, "notes": "Synthetic test review.",
                },
            })
        spec = {
            "schema_version": 1, "document_id": "test-document",
            "source": {
                "pdf": {"path": str(pdf), "sha256": corrections.digest(pdf.read_bytes())},
                "markdown": {"path": str(markdown), "sha256": corrections.digest(source)},
                "assets_dir": str(original_dir),
            },
            "open_issues": ["Other procedures are not approved."], "corrections": values,
        }
        case = Case(correction_dir / "corrections.json", spec, markdown, pdf, tmp_path / "output")
        case.save()
        return case
    return create


def test_multiple_original_ranges_crlf_assets_and_baseline_issues(make_case) -> None:
    original = (
        b'PREFIX <span id="keep"></span>\r\nold-one\r\nBETWEEN\r\nold-two\r\n'
        b'![diagram](diagram.png)\r\n[unresolved](#missing)\r\n'
        b'| A | B |\r\n| --- | --- |\r\n| incomplete |\r\nSUFFIX'
    )
    case = make_case(original, [(4, 4, b"new-two\n"), (2, 2, b"new-one\nextra\n\n")])
    image = case.source.parent / "diagram.png"
    image.write_bytes(b"unchanged image")
    manifest = case.run()
    expected = original.replace(b"old-one\r\n", b"new-one\r\nextra\r\n\r\n").replace(
        b"old-two\r\n", b"new-two\r\n",
    )
    assert (case.output / "manual.md").read_bytes() == expected
    assert case.source.read_bytes() == original
    assert (case.output / "diagram.png").read_bytes() == image.read_bytes()
    assert manifest["document_quality_approved"] is False
    assert manifest["open_issues"] == case.spec["open_issues"]
    assert [item["id"] for item in manifest["corrections"]] == ["patch-1", "patch-0"]
    report = json.loads((case.output / "validation.json").read_text())
    assert report["preexisting_broken_internal_links"] == {"missing": 1}
    assert report["preexisting_table_issues"]
    assert report["remaining_table_issues"]
    evidence = json.loads((case.output / "evidence" / "patch-0.json").read_text())
    assert evidence["lines"][0]["page"] == 1
    assert "Complete PDF evidence" in evidence["lines"][0]["text"]
    assert isinstance(evidence["lines"][0]["x"], float)
    for item in manifest["verified_inputs"]:
        assert corrections.digest(Path(item["path"]).read_bytes()) == item["sha256"]
    with pytest.raises(corrections.CorrectionError, match="already exists"):
        case.run()
    assert (case.output / "manual.md").read_bytes() == expected


@pytest.mark.parametrize("status", ["pending_review", "rejected"])
@pytest.mark.parametrize("mode", ["check", "apply"])
def test_cli_blocks_unapproved_without_emitting_manual(make_case, status, mode) -> None:
    case = make_case()
    case.spec["corrections"][0]["review"].update(
        status=status, pdf_compared=False, complete_procedure_checked=False,
    )
    case.save()
    result = subprocess.run(
        [sys.executable, str(Path(corrections.__file__)), mode, str(case.path),
         "--output-dir", str(case.output)], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2, result.stderr
    assert not (case.output / "manual.md").exists()
    assert (case.output / "diff.patch").is_file()
    manifest = json.loads((case.output / "manifest.json").read_text())
    assert manifest["blocked_corrections"] == ["patch-0"]
    assert manifest["output"] is None


def test_approved_cli_applies_and_check_never_emits_manual(make_case) -> None:
    case = make_case()
    manifest = case.run(apply=False)
    assert manifest["status"] == "validated"
    assert not (case.output / "manual.md").exists()
    destination = case.output.with_name("applied")
    result = subprocess.run(
        [sys.executable, str(Path(corrections.__file__)), "apply", str(case.path),
         "--output-dir", str(destination)], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (destination / "manual.md").read_bytes() == b"PREFIX\nnew-one\nBETWEEN\nold-two\nSUFFIX"
    assert "quality is not approved" in result.stdout


@pytest.mark.parametrize("location", ["pdf", "markdown", "target", "replacement"])
def test_hash_mismatch_never_publishes_output(make_case, location) -> None:
    case = make_case()
    entry = (
        case.spec["source"][location] if location in {"pdf", "markdown"}
        else case.spec["corrections"][0][location]
    )
    entry["sha256"] = "0" * 64
    case.save()
    with pytest.raises(corrections.CorrectionError, match="SHA-256 mismatch"):
        case.run()
    assert not case.output.exists()


@pytest.mark.parametrize("fault", [
    "boolean-version", "extra-field", "missing-field", "boolean-line", "zero-line",
    "large-line", "boolean-page", "large-page", "duplicate-id", "overlap",
    "invalid-id", "unreviewed-approval", "invalid-status", "duplicate-json-field",
])
def test_invalid_metadata_is_rejected(make_case, fault) -> None:
    case = make_case()
    patch = case.spec["corrections"][0]
    if fault == "boolean-version":
        case.spec["schema_version"] = True
    elif fault == "extra-field":
        patch["ignored_typo"] = "must not be ignored"
    elif fault == "missing-field":
        del patch["reason"]
    elif fault == "boolean-line":
        patch["target"]["lines"] = [True, 2]
    elif fault == "zero-line":
        patch["target"]["lines"] = [0, 2]
    elif fault == "large-line":
        patch["target"]["lines"] = [2, 200]
    elif fault == "boolean-page":
        patch["pdf_pages"] = [True, 1]
    elif fault == "large-page":
        patch["pdf_pages"] = [1, 2]
    elif fault in {"duplicate-id", "overlap"}:
        duplicate = copy.deepcopy(patch)
        if fault == "overlap":
            duplicate["id"] = "different-id"
        case.spec["corrections"].append(duplicate)
    elif fault == "invalid-id":
        patch["id"] = "../escaped"
    elif fault == "unreviewed-approval":
        patch["review"]["pdf_compared"] = False
    elif fault == "invalid-status":
        patch["review"]["status"] = []
    case.save()
    if fault == "duplicate-json-field":
        case.path.write_text(case.path.read_text().replace(
            '"schema_version": 1', '"schema_version": 1, "schema_version": 1',
        ))
    with pytest.raises((corrections.CorrectionError, QualityError)):
        case.run()
    assert not case.output.exists()


@pytest.mark.parametrize("content", [
    b"```console\nDevice> enable\n",
    b"```console\nDevice> enable\n```python\n",
    b"| A | B |\n| --- | --- |\n| one |\n",
    b"| A | B |\n| --- | --- |\n| ``` | broken |\n",
    b"[new](#absent)\n",
    b"\xef\xbb\xbfnew\n",
    b"---\nnew: metadata\n---\n",
    b"missing boundary newline",
    b"new\rold\n",
    b"old-one\n",
])
def test_invalid_replacement_never_publishes(make_case, content) -> None:
    case = make_case()
    case.replace(content)
    with pytest.raises(corrections.CorrectionError):
        case.run()
    assert not case.output.exists()


@pytest.mark.parametrize("reference", [
    '<span id="keep"></span>', "![diagram](diagram.png)",
    "[existing](https://example.com/manual)", "[unresolved](#already-missing)",
])
def test_existing_references_cannot_be_removed(make_case, reference) -> None:
    source = f"PREFIX\n{reference}\nSUFFIX\n".encode()
    case = make_case(source)
    with pytest.raises(corrections.CorrectionError, match="removes existing"):
        case.run()


def test_new_duplicate_anchor_is_rejected(make_case) -> None:
    case = make_case(b'<span id="keep"></span>\nold-one\nSUFFIX\n')
    case.replace(b'<span id="keep"></span>\n')
    with pytest.raises(corrections.CorrectionError, match="duplicate anchors"):
        case.run()


def test_table_damage_at_range_boundary_is_rejected(make_case) -> None:
    case = make_case(b"A | B\n--- | ---\na | b\nold\n", [(4, 4, b"a | b | c\n")])
    with pytest.raises(corrections.CorrectionError, match="range boundary"):
        case.run()


@pytest.mark.parametrize("ending", [b"", b"\n", b"\n\n"])
def test_eof_and_approved_blank_lines_preserved(make_case, ending) -> None:
    case = make_case(b"PREFIX\r\nold", [(2, 2, b"new\nsecond" + ending)])
    case.run()
    assert (case.output / "manual.md").read_bytes() == (
        b"PREFIX\r\nnew\r\nsecond" + ending.replace(b"\n", b"\r\n")
    )
    diff = (case.output / "diff.patch").read_bytes()
    assert b"-old\n\\ No newline at end of file\n" in diff


@pytest.mark.parametrize("destination", ["../outside.png", "/outside.png", "manual.md"])
def test_unsafe_image_destination_is_rejected(make_case, destination) -> None:
    case = make_case()
    case.replace(f"![image]({destination})\n".encode())
    with pytest.raises(corrections.CorrectionError, match="image destination"):
        case.run()
    assert not case.output.exists()


def test_image_symlink_cannot_escape_assets(make_case) -> None:
    case = make_case()
    outside = case.source.parent.parent / "outside.png"
    outside.write_bytes(b"outside")
    (case.source.parent / "linked.png").symlink_to(outside)
    case.replace(b"![image](linked.png)\n")
    with pytest.raises(corrections.CorrectionError, match="escapes assets"):
        case.run()
    assert outside.read_bytes() == b"outside"


def test_output_inside_inputs_is_rejected(make_case) -> None:
    case = make_case()
    case.output = case.path.parent / "result"
    with pytest.raises(corrections.CorrectionError, match="outside input directories"):
        case.run()
    assert not case.output.exists()


def test_local_non_image_link_cannot_become_broken_output(make_case) -> None:
    case = make_case()
    case.replace(b"[related procedure](related.md)\n")
    with pytest.raises(corrections.CorrectionError, match="Local non-image links"):
        case.run()
    assert not case.output.exists()


def test_concurrent_output_creation_is_not_overwritten(make_case, monkeypatch: pytest.MonkeyPatch) -> None:
    case = make_case()
    original_writer = corrections.write_json

    def writer(path: Path, value: Any) -> None:
        original_writer(path, value)
        if path.name == "manifest.json":
            case.output.mkdir()
            (case.output / "existing").write_bytes(b"other process")

    monkeypatch.setattr(corrections, "write_json", writer)
    with pytest.raises(FileExistsError):
        case.run()
    assert list(case.output.iterdir()) == [case.output / "existing"]
    assert (case.output / "existing").read_bytes() == b"other process"
    assert not list(case.output.parent.glob(".output-*"))


@pytest.mark.parametrize("failure", ["mutation", "write-error"])
def test_staging_failure_never_publishes_and_cleans_temporary_files(
    make_case, monkeypatch: pytest.MonkeyPatch, failure,
) -> None:
    case = make_case()
    original_writer = corrections.write_json

    def writer(path: Path, value: Any) -> None:
        if path.name == "validation.json":
            if failure == "mutation":
                case.source.write_bytes(b"concurrent edit")
            else:
                raise OSError("simulated full disk")
        original_writer(path, value)

    monkeypatch.setattr(corrections, "write_json", writer)
    with pytest.raises((corrections.CorrectionError, OSError), match="changed|full disk"):
        case.run()
    assert not case.output.exists()
    assert not list(case.output.parent.glob(".output-*"))
