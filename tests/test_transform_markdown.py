import json
from pathlib import Path

import pytest

from scripts.transform_markdown import (
    GracefulStopRequested,
    LLMConfig,
    OpenAICompatibleClient,
    QuotaThresholdError,
    TransformationError,
    aggregate_metadata,
    load_config,
    protect_literals,
    remove_marker_navigation,
    render_frontmatter,
    restore_literals,
    transform_chunks,
    validate_result,
)


def metadata() -> dict[str, str]:
    return {key: value for key, value in (
        ("title", "Guide"),
        ("description", "Summary"),
        ("product", "Catalyst"),
        ("version", "17"),
        ("source", "Cisco"),
    )}


def test_validate_result_rejects_unclosed_fence() -> None:
    with pytest.raises(TransformationError, match="unclosed"):
        validate_result({"markdown": "```text\nbroken", "metadata": metadata()})


def test_validate_result_normalizes_markdown_and_metadata() -> None:
    markdown, result_metadata = validate_result(
        {"markdown": " # Heading \n", "metadata": {"title": " Guide "}}
    )

    assert markdown == " # Heading\n"
    assert result_metadata["title"] == "Guide"
    assert result_metadata["source"] == ""


def test_protected_literals_round_trip() -> None:
    markdown = (
        '<span id="page-1"></span> [Section](#page-1) '
        "![](_page_1_Picture_0.jpeg)\n"
    )

    protected, literals = protect_literals(markdown)

    assert len(literals) == 3
    assert '<span id="CONFIGBRAIN_LITERAL_000001"></span>' in protected
    assert "[Section](CONFIGBRAIN_LITERAL_000002)" in protected
    assert "![](CONFIGBRAIN_LITERAL_000003)" in protected
    assert restore_literals(protected, literals) == markdown
    with pytest.raises(TransformationError, match="exactly once"):
        restore_literals(protected.replace("CONFIGBRAIN_LITERAL_000001", ""), literals)


def test_restore_literals_reports_token_count() -> None:
    with pytest.raises(TransformationError, match=r"found 0"):
        restore_literals("", {"CONFIGBRAIN_LITERAL_000001": "#page-1"})


def test_client_lists_every_protected_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_messages: list[dict[str, str]] = []

    def fake_request(
        self: OpenAICompatibleClient,
        messages: list[dict[str, str]],
    ) -> dict[str, object]:
        del self
        captured_messages.extend(messages)
        return {
            "markdown": (
                '# <span id="CONFIGBRAIN_LITERAL_000001"></span>Guide\n'
                "![](CONFIGBRAIN_LITERAL_000002)"
            ),
            "metadata": metadata(),
        }

    monkeypatch.setattr(OpenAICompatibleClient, "_request", fake_request)
    client = OpenAICompatibleClient(
        LLMConfig(
            api_url="https://example.test/chat",
            api_key="secret",
            model=None,
            api_key_header="api-key",
        )
    )
    markdown, _ = client.transform(
        '# <span id="page-1"></span>Guide\n![](_page_1.jpeg)'
    )

    user_prompt = captured_messages[-1]["content"]
    assert "CONFIGBRAIN_LITERAL_000001" in user_prompt
    assert "CONFIGBRAIN_LITERAL_000002" in user_prompt
    assert '<span id="page-1"></span>' in markdown
    assert "![](_page_1.jpeg)" in markdown


def test_remove_marker_navigation_removes_only_generated_return_lines() -> None:
    markdown = (
        "# Guide\n\n"
        '<span id="page-73-0"></span>Return to Verifying Spine Multicast '
        "[Replication,](#page-66-1) on page 45.\n\n"
        "Return to Verifying Optimized Multicast, on "
        "[page](#page-365-1) 344\n\n"
        "Return to the device prompt after configuration.\n\n"
        "![](_page_80_Diagram_7.jpeg)\n"
    )

    cleaned = remove_marker_navigation(markdown)

    assert "on page 45" not in cleaned
    assert "[page](#page-365-1) 344" not in cleaned
    assert "Return to the device prompt after configuration." in cleaned
    assert "![](_page_80_Diagram_7.jpeg)" in cleaned


def test_render_frontmatter_is_delimited_and_json_compatible() -> None:
    rendered = render_frontmatter(metadata())
    assert rendered.startswith("---\n")
    assert rendered.endswith("---\n\n")
    assert json.loads(rendered.split("\n", 1)[1].rsplit("\n---", 1)[0])["title"] == "Guide"


def test_load_config_supports_cloud_gpt_names(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CLOUD_GPT_API_URL=https://example.test/gpt/gpt-5.1\n"
        "CLOUD_GPT_API_KEY=secret\n",
        encoding="utf-8",
    )

    config = load_config(env_file)

    assert config == LLMConfig(
        api_url="https://example.test/gpt/gpt-5.1",
        api_key="secret",
        model=None,
        api_key_header="api-key",
    )


def test_client_retries_response_validation_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        {"markdown": "```text\nbroken", "metadata": metadata()},
        {"markdown": "```text\nfixed\n```", "metadata": metadata()},
    ]
    payloads: list[dict[str, object]] = []

    class FakeResponse:
        ok = True
        status_code = 200

        def json(self) -> dict[str, object]:
            result = responses.pop(0)
            return {
                "choices": [
                    {"message": {"content": json.dumps(result)}}
                ]
            }

    def fake_post(*args: object, **kwargs: object) -> FakeResponse:
        payloads.append(kwargs["json"])  # type: ignore[arg-type]
        return FakeResponse()

    monkeypatch.setattr("scripts.transform_markdown.requests.post", fake_post)
    client = OpenAICompatibleClient(
        LLMConfig(
            api_url="https://example.test/chat",
            api_key="secret",
            model="test-model",
            api_key_header="api-key",
        )
    )

    markdown, _ = client.transform("# Guide\n")

    assert markdown == "```text\nfixed\n```\n"
    assert len(payloads) == 2
    retry_messages = payloads[1]["messages"]  # type: ignore[index]
    assert "前回の応答は検証に失敗しました" in retry_messages[1]["content"]


def test_client_reads_quota_header_and_stops_at_threshold(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeResponse:
        ok = True
        status_code = 200
        headers = {"X-Quota-Usage-Percent": "95%"}

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"markdown": "# Guide", "metadata": metadata()}
                            )
                        }
                    }
                ]
            }

    monkeypatch.setattr(
        "scripts.transform_markdown.requests.post",
        lambda *args, **kwargs: FakeResponse(),
    )
    client = OpenAICompatibleClient(
        LLMConfig(
            api_url="https://example.test/chat",
            api_key="secret",
            model=None,
            api_key_header="api-key",
            quota_stop_percent=95,
        )
    )

    client.transform("# Guide\n")

    assert client.quota_usage_percent == 95
    assert "LLM quota usage: 95%" in capsys.readouterr().out
    with pytest.raises(QuotaThresholdError, match="95%"):
        client.raise_if_quota_reached()


def test_client_reports_when_quota_header_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeResponse:
        ok = True
        status_code = 200
        headers: dict[str, str] = {}

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"markdown": "# Guide", "metadata": metadata()}
                            )
                        }
                    }
                ]
            }

    monkeypatch.setattr(
        "scripts.transform_markdown.requests.post",
        lambda *args, **kwargs: FakeResponse(),
    )
    client = OpenAICompatibleClient(
        LLMConfig(
            api_url="https://example.test/chat",
            api_key="secret",
            model=None,
            api_key_header="api-key",
        )
    )

    client.transform("# Guide\n")

    assert "LLM quota usage: unavailable" in capsys.readouterr().out


def test_transform_chunks_saves_checkpoint_before_quota_stop(tmp_path: Path) -> None:
    input_dir = tmp_path / "chunks"
    input_dir.mkdir()
    for index in (1, 2):
        (input_dir / f"chunk-{index:04d}.md").write_text(
            f"# Guide {index}\n", encoding="utf-8"
        )
    (input_dir / "manifest.json").write_text(
        json.dumps(
            {
                "chunks": [
                    {"path": "chunk-0001.md"},
                    {"path": "chunk-0002.md"},
                ]
            }
        ),
        encoding="utf-8",
    )

    class QuotaClient(FakeClient):
        calls = 0

        def transform(self, markdown: str) -> tuple[str, dict[str, str]]:
            self.calls += 1
            return super().transform(markdown)

        def raise_if_quota_reached(self) -> None:
            raise QuotaThresholdError("quota reached")

    output_dir = tmp_path / "transformed"
    client = QuotaClient()

    with pytest.raises(QuotaThresholdError, match="quota reached"):
        transform_chunks(input_dir, output_dir, client, resume=True)

    assert client.calls == 1
    assert (output_dir / "chunk-0001.md").is_file()
    manifest = json.loads((output_dir / "manifest.json").read_text())
    assert len(manifest["chunks"]) == 1
    assert manifest["complete"] is False


def test_transform_chunks_stops_after_saving_requested_checkpoint(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "chunks"
    input_dir.mkdir()
    for index in (1, 2):
        (input_dir / f"chunk-{index:04d}.md").write_text(
            f"# Guide {index}\n", encoding="utf-8"
        )
    (input_dir / "manifest.json").write_text(
        json.dumps(
            {
                "chunks": [
                    {"path": "chunk-0001.md"},
                    {"path": "chunk-0002.md"},
                ]
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "transformed"

    with pytest.raises(GracefulStopRequested, match="chunk-0001.md"):
        transform_chunks(
            input_dir,
            output_dir,
            FakeClient(),
            resume=True,
            stop_requested=lambda: True,
        )

    manifest = json.loads((output_dir / "manifest.json").read_text())
    assert [chunk["id"] for chunk in manifest["chunks"]] == ["chunk-0001"]
    assert manifest["complete"] is False


def test_aggregate_metadata_uses_earliest_document_metadata() -> None:
    records = [
        {"metadata": {**metadata(), "product": "First"}},
        {"metadata": {**metadata(), "product": "Consensus", "description": "Later"}},
        {"metadata": {**metadata(), "product": "Consensus", "description": "Other"}},
    ]

    result = aggregate_metadata(records)

    assert result["product"] == "First"
    assert result["description"] == "Summary"


class FakeClient:
    def transform(self, markdown: str) -> tuple[str, dict[str, str]]:
        return markdown.upper(), metadata()


def test_transform_chunks_writes_atomic_output_and_manifest(tmp_path: Path) -> None:
    input_dir = tmp_path / "chunks"
    input_dir.mkdir()
    (input_dir / "chunk-0001.md").write_text("# Guide\n", encoding="utf-8")
    (input_dir / "manifest.json").write_text(
        json.dumps({"chunks": [{"path": "chunk-0001.md"}]}), encoding="utf-8"
    )

    output_dir = tmp_path / "transformed"
    transform_chunks(input_dir, output_dir, FakeClient())

    output = (output_dir / "chunk-0001.md").read_text(encoding="utf-8")
    assert output.startswith("---\n")
    assert "# GUIDE" in output
    assert json.loads((output_dir / "manifest.json").read_text())["chunks"][0]["id"] == "chunk-0001"
    assert json.loads((output_dir / "manifest.json").read_text())["complete"] is True
    with pytest.raises(TransformationError, match="already exists"):
        transform_chunks(input_dir, output_dir, FakeClient())


def test_transform_chunks_resumes_completed_chunks(tmp_path: Path) -> None:
    input_dir = tmp_path / "chunks"
    input_dir.mkdir()
    for index in (1, 2):
        (input_dir / f"chunk-{index:04d}.md").write_text(
            f"# Guide {index}\n", encoding="utf-8"
        )
    (input_dir / "manifest.json").write_text(
        json.dumps(
            {
                "chunks": [
                    {"path": "chunk-0001.md"},
                    {"path": "chunk-0002.md"},
                ]
            }
        ),
        encoding="utf-8",
    )

    class CountingClient(FakeClient):
        calls = 0

        def transform(self, markdown: str) -> tuple[str, dict[str, str]]:
            self.calls += 1
            return super().transform(markdown)

    output_dir = tmp_path / "transformed"
    client = CountingClient()
    transform_chunks(input_dir, output_dir, client, resume=True)
    transform_chunks(input_dir, output_dir, client, resume=True)

    assert client.calls == 2
    manifest = json.loads((output_dir / "manifest.json").read_text())
    assert manifest["complete"] is True
    assert manifest["document_metadata"]["title"] == "Guide"
