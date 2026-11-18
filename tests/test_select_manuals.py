import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import requests

from configbrain.select_manuals import (
    CloudConfig,
    SelectionError,
    load_catalog,
    load_cloud_config,
    main,
    select_manuals,
    validate_selection,
)


@pytest.fixture
def catalog_file(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "product": "catalyst9300",
                "release": "26.x",
                "documents": [
                    {
                        "document_id": "vlan-guide",
                        "title": "VLAN guide",
                        "category": "Configuration Guides",
                        "html_status": "available",
                        "chapters": [
                            {
                                "order": 1,
                                "title": "Configuring VLANs",
                                "url": "https://www.cisco.com/vlan.html",
                            }
                        ],
                    },
                    {
                        "document_id": "ip-guide",
                        "title": "IP guide",
                        "category": "Configuration Guides",
                        "html_status": "available",
                        "chapters": [
                            {
                                "order": 3,
                                "title": "Configuring DHCP",
                                "url": "https://www.cisco.com/dhcp.html",
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def candidate(document_id: str, order: int) -> dict:
    return {
        "document_id": document_id,
        "chapter_order": order,
        "reason": "The title matches the requirement.",
        "uncertainty": "The chapter body is not verified.",
    }


def selection() -> dict:
    return {
        "items": [
            {
                "requirement": "Create VLAN 120",
                "uncertainty": "The chapter body is not verified.",
                "candidates": [candidate("vlan-guide", 1)],
            },
            {
                "requirement": "Configure DHCP relay",
                "uncertainty": "The chapter body is not verified.",
                "candidates": [candidate("ip-guide", 3)],
            },
        ]
    }


class FakeResponse:
    status_code = 200

    def __init__(self, value: dict | None = None, finish_reason: str = "stop") -> None:
        self.value = value if value is not None else selection()
        self.finish_reason = finish_reason

    def json(self) -> dict:
        return {
            "model": "test-model",
            "choices": [
                {
                    "finish_reason": self.finish_reason,
                    "message": {"content": json.dumps(self.value)},
                }
            ],
        }


def test_full_catalog_is_sent_and_multiple_requirements_are_resolved(
    catalog_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def post(url: str, **kwargs: Any) -> FakeResponse:
        assert url == "https://cloud.example.test/chat"
        assert kwargs["headers"] == {"api-key": "test-key"}
        assert kwargs["allow_redirects"] is False
        payload = kwargs["json"]
        assert payload["response_format"] == {"type": "json_object"}
        sent = json.loads(payload["messages"][1]["content"])
        assert sent["requirement"] == "VLAN 120とDHCPリレーを設定"
        assert [x["document_id"] for x in sent["documents"]] == ["vlan-guide", "ip-guide"]
        assert [len(x["chapters"]) for x in sent["documents"]] == [1, 1]
        return FakeResponse()

    monkeypatch.setattr("configbrain.select_manuals.requests.post", post)
    result = select_manuals(
        "VLAN 120とDHCPリレーを設定",
        catalog_file,
        "catalyst9300",
        "26.x",
        CloudConfig("https://cloud.example.test/chat", "test-key", None),
    )
    assert result["model"] == "test-model"
    assert len(result["catalog_sha256"]) == 64
    assert [item["requirement"] for item in result["items"]] == [
        "Create VLAN 120",
        "Configure DHCP relay",
    ]
    assert result["items"][0]["candidates"][0]["chapter_url"] == "https://www.cisco.com/vlan.html"
    assert result["items"][1]["candidates"][0]["chapter_title"] == "Configuring DHCP"


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value["items"][0]["candidates"][0].update(document_id="unknown"),
        lambda value: value["items"][0]["candidates"][0].update(chapter_order=3),
        lambda value: value["items"][0]["candidates"][0].update(chapter_order=True),
        lambda value: value["items"][0]["candidates"][0].update(reason=""),
        lambda value: value["items"][0]["candidates"].append(candidate("vlan-guide", 1)),
        lambda value: value["items"][0].update(uncertainty=""),
        lambda value: value.update(items=[]),
    ],
)
def test_invalid_llm_output_is_rejected(
    catalog_file: Path, change: Callable[[dict[str, Any]], None]
) -> None:
    catalog = load_catalog(catalog_file, "catalyst9300", "26.x")
    value = selection()
    change(value)
    with pytest.raises(SelectionError):
        validate_selection(value, catalog)


def test_unresolved_item_is_preserved(catalog_file: Path) -> None:
    catalog = load_catalog(catalog_file, "catalyst9300", "26.x")
    result = {"items": [{"requirement": "Unknown feature", "uncertainty": "Not found", "candidates": []}]}
    assert validate_selection(result, catalog) == result["items"]


def test_mismatched_or_incomplete_catalog_fails_before_network(
    catalog_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "configbrain.select_manuals.requests.post",
        lambda *args, **kwargs: pytest.fail("No network request expected"),
    )
    config = CloudConfig("https://cloud.example.test/chat", "test-key", None)
    with pytest.raises(SelectionError, match="product or release"):
        select_manuals("VLAN", catalog_file, "catalyst9300", "17.x", config)
    catalog = json.loads(catalog_file.read_text())
    catalog["documents"][1]["chapters"] = []
    catalog_file.write_text(json.dumps(catalog))
    with pytest.raises(SelectionError, match="chapters are unavailable"):
        select_manuals("VLAN", catalog_file, "catalyst9300", "26.x", config)


def test_catalog_rejects_untrusted_chapter_urls(catalog_file: Path) -> None:
    catalog = json.loads(catalog_file.read_text())
    catalog["documents"][0]["chapters"][0]["url"] = "https://example.test/vlan.html"
    catalog_file.write_text(json.dumps(catalog))
    with pytest.raises(SelectionError, match="chapter URL"):
        load_catalog(catalog_file, "catalyst9300", "26.x")


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
def test_incomplete_or_filtered_response_is_rejected(
    catalog_file: Path, monkeypatch: pytest.MonkeyPatch, finish_reason: str
) -> None:
    monkeypatch.setattr(
        "configbrain.select_manuals.requests.post",
        lambda *args, **kwargs: FakeResponse(finish_reason=finish_reason),
    )
    with pytest.raises(SelectionError, match="incomplete or filtered"):
        select_manuals(
            "VLAN", catalog_file, "catalyst9300", "26.x",
            CloudConfig("https://cloud.example.test/chat", "test-key", None),
        )


def test_cloud_configuration_and_request_failures_do_not_leak_secrets(
    tmp_path: Path, catalog_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLOUD_GPT_API_URL", raising=False)
    monkeypatch.delenv("CLOUD_GPT_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CLOUD_GPT_API_URL=https://cloud.example.test/chat\n"
        "CLOUD_GPT_API_KEY=private-key\nLLM_MODEL=test-model\n"
    )
    config = load_cloud_config(env_file)
    assert config.model == "test-model"

    def fail(*args: object, **kwargs: object) -> None:
        raise requests.ConnectionError("private-key")

    monkeypatch.setattr("configbrain.select_manuals.requests.post", fail)
    with pytest.raises(SelectionError) as error:
        select_manuals("VLAN", catalog_file, "catalyst9300", "26.x", config)
    assert "private-key" not in str(error.value)
    assert error.value.__suppress_context__
    assert "private-key" not in repr(config)


def test_redirect_response_is_not_followed(
    catalog_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = FakeResponse()
    response.status_code = 302
    monkeypatch.setattr(
        "configbrain.select_manuals.requests.post", lambda *args, **kwargs: response
    )
    with pytest.raises(SelectionError, match="HTTP 302"):
        select_manuals(
            "VLAN", catalog_file, "catalyst9300", "26.x",
            CloudConfig("https://cloud.example.test/chat", "test-key", None),
        )


def test_cli_saves_valid_result_and_preserves_output_on_failure(
    tmp_path: Path, catalog_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("CLOUD_GPT_API_URL", "https://cloud.example.test/chat")
    monkeypatch.setenv("CLOUD_GPT_API_KEY", "test-key")
    monkeypatch.setattr(
        "configbrain.select_manuals.requests.post", lambda *args, **kwargs: FakeResponse()
    )
    requirement_file = tmp_path / "requirement.txt"
    requirement_file.write_text("VLAN 120とDHCPリレーを設定", encoding="utf-8")
    output = tmp_path / "selection.json"
    args = ["--catalog", str(catalog_file), "--requirement-file", str(requirement_file), "--output", str(output)]
    assert main(args) == 0
    saved = output.read_text(encoding="utf-8")
    assert len(json.loads(saved)["items"]) == 2
    assert capsys.readouterr().out == ""

    monkeypatch.setattr(
        "configbrain.select_manuals.requests.post",
        lambda *args, **kwargs: FakeResponse(finish_reason="length"),
    )
    assert main(args) == 1
    assert output.read_text(encoding="utf-8") == saved
    assert "incomplete" in capsys.readouterr().err


def test_cli_refuses_to_overwrite_inputs_before_request(
    tmp_path: Path, catalog_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(
        "configbrain.select_manuals.requests.post",
        lambda *args, **kwargs: pytest.fail("No network request expected"),
    )
    original = catalog_file.read_bytes()
    assert main(["--catalog", str(catalog_file), "--requirement", "VLAN", "--output", str(catalog_file)]) == 1
    assert catalog_file.read_bytes() == original
    assert "must not overwrite" in capsys.readouterr().err


def test_current_catalog_includes_every_document_and_chapter() -> None:
    path = Path("data/catalog/c9300-iosxe26.json")
    source = json.loads(path.read_text(encoding="utf-8"))
    catalog = load_catalog(path, "catalyst9300", "26.x")
    assert catalog.documents == [
        {
            "document_id": document["document_id"],
            "title": document["title"],
            "category": document["category"],
            "chapters": [
                {"order": chapter["order"], "title": chapter["title"]}
                for chapter in document["chapters"]
            ],
        }
        for document in source["documents"]
    ]
