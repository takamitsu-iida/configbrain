import json
from pathlib import Path

import pytest

from scripts.extract import ExtractionError, build_parser, extract_markdown


@pytest.fixture(autouse=True)
def one_page_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.extract.pdf_page_count", lambda input_pdf: 1)


def test_extract_markdown_runs_command_and_returns_generated_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_pdf = tmp_path / "manual.pdf"
    input_pdf.write_bytes(b"%PDF")
    output_dir = tmp_path / "raw"
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:8080")
    monkeypatch.setenv("http_proxy", "http://proxy.example.test:8080")

    monkeypatch.setattr("scripts.extract.shutil.which", lambda command: "/bin/marker_single")
    captured_command: list[str] = []
    captured_environment: dict[str, str] = {}

    def fake_run(command: list[str], **kwargs: object) -> object:
        captured_command.extend(command)
        captured_environment.update(kwargs["env"])  # type: ignore[arg-type]
        (output_dir / "manual.md").write_text("# Manual\n", encoding="utf-8")
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("scripts.extract.subprocess.run", fake_run)

    markdown_files = extract_markdown(input_pdf, output_dir)

    assert markdown_files == [output_dir / "manual.md"]
    assert captured_command == [
        "/bin/marker_single",
        str(input_pdf),
        "--output_dir",
        str(output_dir),
    ]
    assert captured_environment["HF_HUB_DISABLE_XET"] == "1"
    assert "HTTPS_PROXY" not in captured_environment
    assert "http_proxy" not in captured_environment
    assert captured_environment["NO_PROXY"] == "127.0.0.1,localhost"
    assert captured_environment["no_proxy"] == "127.0.0.1,localhost"


def test_extract_markdown_rejects_non_empty_output_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_pdf = tmp_path / "manual.pdf"
    input_pdf.write_bytes(b"%PDF")
    output_dir = tmp_path / "raw"
    output_dir.mkdir()
    (output_dir / "old.md").write_text("old", encoding="utf-8")
    monkeypatch.setattr("scripts.extract.shutil.which", lambda command: "/bin/marker-pdf")

    with pytest.raises(ExtractionError, match="not empty"):
        extract_markdown(input_pdf, output_dir, command="missing-marker")


def test_extract_markdown_can_disable_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_pdf = tmp_path / "manual.pdf"
    input_pdf.write_bytes(b"%PDF")
    output_dir = tmp_path / "raw"

    monkeypatch.setattr("scripts.extract.shutil.which", lambda command: "/bin/marker_single")
    captured_command: list[str] = []

    def fake_run(command: list[str], **_: object) -> object:
        captured_command.extend(command)
        (output_dir / "manual.md").write_text("# Manual\n", encoding="utf-8")
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("scripts.extract.subprocess.run", fake_run)

    extract_markdown(input_pdf, output_dir, disable_ocr=True)

    assert captured_command[-1] == "--disable_ocr"


def test_extract_cli_disables_ocr_by_default_and_can_enable_it() -> None:
    parser = build_parser()

    defaults = parser.parse_args(["manual.pdf", "--output-dir", "output"])
    enabled = parser.parse_args(
        ["manual.pdf", "--output-dir", "output", "--enable-ocr"]
    )

    assert defaults.disable_ocr is True
    assert enabled.disable_ocr is False


def test_extract_markdown_streams_marker_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_pdf = tmp_path / "manual.pdf"
    input_pdf.write_bytes(b"%PDF")
    output_dir = tmp_path / "raw"
    monkeypatch.setattr("scripts.extract.shutil.which", lambda command: "/bin/marker_single")

    class FakeProcess:
        stdout = iter(("Extracting page 1\n", "Extracting page 2\n"))

        def wait(self) -> int:
            (output_dir / "manual.md").write_text("# Manual\n", encoding="utf-8")
            return 0

    monkeypatch.setattr("scripts.extract.subprocess.Popen", lambda *args, **kwargs: FakeProcess())

    extract_markdown(input_pdf, output_dir, stream_output=True)

    assert capsys.readouterr().out == "Extracting page 1\nExtracting page 2\n"


def test_extract_markdown_batches_large_pdf_and_merges_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_pdf = tmp_path / "manual.pdf"
    input_pdf.write_bytes(b"%PDF")
    output_dir = tmp_path / "raw"
    monkeypatch.setattr("scripts.extract.shutil.which", lambda command: "/bin/marker_single")
    monkeypatch.setattr("scripts.extract.pdf_page_count", lambda input_pdf: 3)
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> object:
        commands.append(command)
        batch_root = Path(command[command.index("--output_dir") + 1])
        page_range = command[command.index("--page_range") + 1]
        start = int(page_range.split("-", maxsplit=1)[0])
        marker_output = batch_root / "manual"
        marker_output.mkdir(parents=True)
        (marker_output / "manual.md").write_text(
            f"# Page {start + 1}\n\n![](_page_{start}_Picture_0.jpeg)\n",
            encoding="utf-8",
        )
        (marker_output / "manual_meta.json").write_text(
            json.dumps(
                {
                    "table_of_contents": [{"title": f"Page {start + 1}"}],
                    "page_stats": [{"page_id": start}],
                }
            ),
            encoding="utf-8",
        )
        (marker_output / f"_page_{start}_Picture_0.jpeg").write_bytes(b"image")
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("scripts.extract.subprocess.run", fake_run)

    markdown_files = extract_markdown(input_pdf, output_dir, page_batch_size=2)

    assert len(commands) == 2
    assert "--page_range" in commands[0]
    assert commands[0][commands[0].index("--page_range") + 1] == "0-1"
    assert commands[1][commands[1].index("--page_range") + 1] == "2-2"
    assert markdown_files == [output_dir / "manual" / "manual.md"]
    assert "# Page 1" in markdown_files[0].read_text(encoding="utf-8")
    assert "# Page 3" in markdown_files[0].read_text(encoding="utf-8")
    metadata = json.loads(
        (output_dir / "manual" / "manual_meta.json").read_text(encoding="utf-8")
    )
    assert [page["page_id"] for page in metadata["page_stats"]] == [0, 2]
    assert (output_dir / "manual" / "_page_0_Picture_0.jpeg").is_file()
    assert (output_dir / "manual" / "_page_2_Picture_0.jpeg").is_file()


def test_extract_markdown_reports_missing_command(tmp_path: Path) -> None:
    input_pdf = tmp_path / "manual.pdf"
    input_pdf.write_bytes(b"%PDF")

    with pytest.raises(ExtractionError, match="not found"):
        extract_markdown(input_pdf, tmp_path / "raw", command="missing-marker")