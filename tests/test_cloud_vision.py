import base64
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.cloud_vision import (
    CloudVisionClient,
    CloudVisionConfig,
    CloudVisionError,
    save_result,
    validate_output_path,
)


class FakeResponse:
    def __init__(self, data: dict[str, Any], status_code: int = 200) -> None:
        self.data = data
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self) -> dict[str, Any]:
        return self.data


@pytest.fixture
def config() -> CloudVisionConfig:
    return CloudVisionConfig(
        image_api_url="https://example.test/images:annotate",
        file_api_url="https://example.test/files:annotate",
        api_key="test-api-key",
    )


def test_annotate_image_sends_document_text_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: CloudVisionConfig,
) -> None:
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"image-data")
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(
            {"responses": [{"fullTextAnnotation": {"text": "Switch 1"}}]}
        )

    monkeypatch.setattr("scripts.cloud_vision.requests.post", fake_post)

    result = CloudVisionClient(config).annotate_image(image_path)

    request = captured["json"]["requests"][0]
    assert captured["url"] == config.image_api_url
    assert captured["headers"]["api-key"] == config.api_key
    assert request["features"] == [{"type": "DOCUMENT_TEXT_DETECTION"}]
    assert base64.b64decode(request["image"]["content"]) == b"image-data"
    assert result["request"] == {"type": "image", "pages": None}
    assert result["response"]["responses"][0]["fullTextAnnotation"]["text"] == "Switch 1"


def test_annotate_pdf_sends_selected_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: CloudVisionConfig,
) -> None:
    pdf_path = tmp_path / "manual.pdf"
    pdf_path.write_bytes(b"%PDF-test")
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(
            {
                "responses": [
                    {
                        "responses": [
                            {
                                "context": {"pageNumber": 24},
                                "fullTextAnnotation": {"text": "Interface"},
                            }
                        ]
                    }
                ]
            }
        )

    monkeypatch.setattr("scripts.cloud_vision.requests.post", fake_post)

    result = CloudVisionClient(config).annotate_pdf(pdf_path, [24])

    request = captured["json"]["requests"][0]
    assert captured["url"] == config.file_api_url
    assert request["inputConfig"]["mimeType"] == "application/pdf"
    assert base64.b64decode(request["inputConfig"]["content"]) == b"%PDF-test"
    assert request["pages"] == [24]
    assert result["request"] == {"type": "pdf", "pages": [24]}


def test_api_error_is_reported_without_saving_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: CloudVisionConfig,
) -> None:
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"image-data")
    monkeypatch.setattr(
        "scripts.cloud_vision.requests.post",
        lambda *args, **kwargs: FakeResponse(
            {"responses": [{"error": {"code": 400, "message": "Bad image"}}]}
        ),
    )

    with pytest.raises(CloudVisionError, match="API error 400: Bad image"):
        CloudVisionClient(config).annotate_image(image_path)


def test_save_result_is_atomic_and_rejects_overwrite(tmp_path: Path) -> None:
    output_path = tmp_path / "ocr" / "result.json"
    result = {"schema_version": 1, "response": {"responses": []}}

    save_result(result, output_path)

    assert json.loads(output_path.read_text(encoding="utf-8")) == result
    with pytest.raises(CloudVisionError, match="already exists"):
        save_result(result, output_path)


def test_validate_output_path_rejects_existing_file_before_request(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "result.json"
    output_path.write_text("existing", encoding="utf-8")

    with pytest.raises(CloudVisionError, match="already exists"):
        validate_output_path(output_path)