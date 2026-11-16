import json
from pathlib import Path
from typing import Any

import pytest

from scripts.check_pdf_markdown_quality import PdfLine, QualityError
from scripts.repair_pdf_markdown import run_repair, validate_proposal
from scripts.transform_markdown import TransformationError, protect_literals


GOOD = """\
| Step | Command or Action | Purpose |
| --- | --- | --- |
| Step 1 | interface vlan vlan-id<br>Example:<br>Device(config)# interface vlan 99 | Enter interface |
| Step 2 | ip address ip-address subnet-mask<br>Example:<br>Device(config-if)# ip address 10.0.0.1 255.255.255.0 | Set address |
"""
BAD = """\
| Step | Command or Action Purpose |
| --- | --- |
| Step 1 | interface vlan vlan-id |
| Step 2 | ip address ip-address subnet-mask |
"""


class FakeClient:
    model = "test-model"

    def __init__(self, proposals: list[Any]) -> None:
        self.proposals = proposals
        self.messages: list[list[dict[str, str]]] = []

    def propose(self, messages: list[dict[str, str]]) -> Any:
        self.messages.append(messages)
        value = self.proposals[len(self.messages) - 1]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    pdf = tmp_path / "source.pdf"
    markdown = tmp_path / "manual.md"
    pdf.write_bytes(b"PDF source")
    markdown.write_bytes(("OUTSIDE BEFORE\r\n" + BAD.replace("\n", "\r\n") + "OUTSIDE AFTER").encode())
    lines = [
        PdfLine(1, "Command or Action Purpose", 100, 100, 94, "Command or Action"),
        PdfLine(1, "Step 1", 50, 90, 84),
        PdfLine(1, "interface vlan vlan-id Enter interface", 100, 90, 84, "interface vlan vlan-id"),
        PdfLine(1, "Device(config)# interface vlan 99", 100, 75, 70),
        PdfLine(1, "Step 2", 50, 60, 54),
        PdfLine(1, "ip address ip-address subnet-mask Set address", 100, 60, 54,
                "ip address ip-address subnet-mask"),
        PdfLine(1, "Device(config-if)# ip address 10.0.0.1 255.255.255.0", 100, 45, 40),
    ]
    monkeypatch.setattr("scripts.repair_pdf_markdown.pdf_lines", lambda data, pages: (lines, []))
    return pdf, markdown, tmp_path / "repair"


def test_repairs_rechecks_and_preserves_outside_bytes(inputs: tuple[Path, Path, Path]) -> None:
    pdf, markdown, output = inputs
    before = markdown.read_bytes()
    client = FakeClient([{"markdown": GOOD, "unresolved": []}])
    result = run_repair(pdf, markdown, (1, 1), (2, 5), output, client=client)
    assert result["status"] == "checks_passed_review_required"
    assert len(client.messages) == 1
    assert "OUTSIDE" not in str(client.messages)
    payload = json.loads(client.messages[0][-1]["content"])
    assert payload["pdf_lines"][0]["page"] == 1
    assert payload["quality_report"]["command_findings"]
    assert markdown.read_bytes() == before
    candidate = (output / result["latest_candidate"]).read_bytes()
    assert candidate == ("OUTSIDE BEFORE\r\n" + GOOD.replace("\n", "\r\n") + "OUTSIDE AFTER").encode()
    assert json.loads((output / "check-0000.json").read_text())["status"] == "review_required"
    assert json.loads((output / "check-0001.json").read_text())["status"] == "no_anomalies_detected"
    assert (output / "manifest.json").is_file()
    with pytest.raises(QualityError, match="新規ディレクトリ"):
        run_repair(pdf, markdown, (1, 1), (2, 5), output, client=client)
    assert len(client.messages) == 1


def test_clean_input_never_loads_llm(
    inputs: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf, markdown, output = inputs
    markdown.write_text(GOOD, encoding="utf-8")
    monkeypatch.setattr("scripts.repair_pdf_markdown.load_config", lambda: pytest.fail("LLM requested"))
    result = run_repair(pdf, markdown, (1, 1), (1, 4), output)
    assert result["status"] == "no_changes_needed"
    assert result["attempts"] == []
    assert result["latest_candidate"] is None


def test_unresolved_llm_response_is_not_a_pass_even_when_checks_pass(
    inputs: tuple[Path, Path, Path],
) -> None:
    pdf, markdown, output = inputs
    client = FakeClient([{"markdown": GOOD, "unresolved": ["Condition requires the adjacent page"]}])
    result = run_repair(pdf, markdown, (1, 1), (2, 5), output, client=client)
    assert result["status"] == "unresolved"
    assert len(client.messages) == 1
    assert result["attempts"][0]["unresolved"]


def test_retries_are_bounded_and_each_candidate_is_rechecked(inputs: tuple[Path, Path, Path]) -> None:
    pdf, markdown, output = inputs
    client = FakeClient([
        {"markdown": BAD + "\nFirst revision.\n", "unresolved": []},
        {"markdown": BAD + "\nSecond revision.\n", "unresolved": []},
    ])
    result = run_repair(pdf, markdown, (1, 1), (2, 5), output, max_attempts=2, client=client)
    assert result["status"] == "unresolved"
    assert "上限" in result["stop_reason"]
    assert len(client.messages) == 2
    assert len(list(output.glob("check-*.json"))) == 3
    assert len(list(output.glob("candidate-*.md"))) == 2
    assert "First revision." in client.messages[1][-1]["content"]


def test_second_attempt_can_resolve_first_candidate_findings(inputs: tuple[Path, Path, Path]) -> None:
    pdf, markdown, output = inputs
    client = FakeClient([
        {"markdown": BAD + "\nRevision.\n", "unresolved": []},
        {"markdown": GOOD, "unresolved": []},
    ])
    result = run_repair(pdf, markdown, (1, 1), (2, 5), output, client=client)
    assert result["status"] == "checks_passed_review_required"
    assert result["latest_candidate"] == "candidate-0002.md"
    assert len(client.messages) == 2
    assert json.loads((output / "check-0002.json").read_text())["status"] == "no_anomalies_detected"


def test_no_change_stops_early(inputs: tuple[Path, Path, Path]) -> None:
    pdf, markdown, output = inputs
    client = FakeClient([{"markdown": BAD, "unresolved": []}])
    result = run_repair(pdf, markdown, (1, 1), (2, 5), output, client=client)
    assert result["status"] == "unresolved"
    assert "変化がない" in result["stop_reason"]
    assert len(client.messages) == 1


@pytest.mark.parametrize("response", [
    {"markdown": "", "unresolved": []},
    {"markdown": "```console\nDevice# enable", "unresolved": []},
    {"markdown": GOOD, "unresolved": "not a list"},
    {"markdown": GOOD},
])
def test_invalid_response_records_failure_without_candidate(
    inputs: tuple[Path, Path, Path], response: dict[str, Any],
) -> None:
    pdf, markdown, output = inputs
    original = markdown.read_bytes()
    with pytest.raises((QualityError, TransformationError)):
        run_repair(pdf, markdown, (1, 1), (2, 5), output, client=FakeClient([response]))
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["attempts"][-1]["status"] == "failed"
    assert manifest["latest_candidate"] is None
    assert (output / "response-0001.json").is_file()
    assert not list(output.glob("candidate-*.md"))
    assert markdown.read_bytes() == original


def test_request_failure_keeps_before_report(inputs: tuple[Path, Path, Path]) -> None:
    pdf, markdown, output = inputs
    with pytest.raises(TransformationError, match="HTTP 503"):
        run_repair(pdf, markdown, (1, 1), (2, 5), output,
                   client=FakeClient([TransformationError("LLM returned HTTP 503")]))
    assert json.loads((output / "manifest.json").read_text())["status"] == "failed"
    assert (output / "check-0000.json").is_file()


def test_literal_and_heading_changes_are_rejected() -> None:
    original = "# Heading\n[manual](https://example.test/guide)\n"
    protected, literals = protect_literals(original)
    assert validate_proposal({"markdown": protected, "unresolved": []}, literals, original)[0] == original
    with pytest.raises(QualityError, match="見出し"):
        validate_proposal(
            {"markdown": protected.replace("# Heading", "# Changed"), "unresolved": []},
            literals, original,
        )
    with pytest.raises(TransformationError, match="protected literal"):
        validate_proposal({"markdown": "# Heading\n", "unresolved": []}, literals, original)


def test_sparse_pdf_does_not_trigger_speculative_repair(
    inputs: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf, markdown, output = inputs
    monkeypatch.setattr("scripts.repair_pdf_markdown.pdf_lines", lambda data, pages: ([], [1]))
    monkeypatch.setattr("scripts.repair_pdf_markdown.load_config", lambda: pytest.fail("LLM requested"))
    result = run_repair(pdf, markdown, (1, 1), (2, 5), output)
    assert result["status"] == "unresolved"
    assert result["attempts"] == []


def test_input_change_during_repair_is_not_reported_as_passed(
    inputs: tuple[Path, Path, Path],
) -> None:
    pdf, markdown, output = inputs

    class MutatingClient(FakeClient):
        def propose(self, messages: list[dict[str, str]]) -> Any:
            markdown.write_text("changed by another process", encoding="utf-8")
            return super().propose(messages)

    with pytest.raises(QualityError, match="入力ファイルが変更"):
        run_repair(pdf, markdown, (1, 1), (2, 5), output,
                   client=MutatingClient([{"markdown": GOOD, "unresolved": []}]))
    assert json.loads((output / "manifest.json").read_text())["status"] == "failed"
