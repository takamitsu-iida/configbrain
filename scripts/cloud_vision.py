"""Run image or PDF OCR with a Google Vision-compatible API."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values


class CloudVisionError(RuntimeError):
    """Raised when Cloud Vision OCR cannot be completed."""


@dataclass(frozen=True)
class CloudVisionConfig:
    image_api_url: str
    file_api_url: str
    api_key: str


def load_config(env_file: Path = Path(".env")) -> CloudVisionConfig:
    values = dotenv_values(env_file) if env_file.is_file() else {}

    def get_value(name: str) -> str:
        value = os.environ.get(name) or values.get(name)
        if not isinstance(value, str) or not value.strip():
            raise CloudVisionError(f"Missing Cloud Vision setting: {name}")
        return value.strip()

    return CloudVisionConfig(
        image_api_url=get_value("CLOUD_VISION_API_URL"),
        file_api_url=get_value("CLOUD_VISION_FILE_API_URL"),
        api_key=get_value("CLOUD_VISION_API_KEY"),
    )


def parse_pages(value: str) -> list[int]:
    try:
        pages = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as error:
        raise argparse.ArgumentTypeError("Pages must be comma-separated integers") from error
    if not pages:
        raise argparse.ArgumentTypeError("At least one page is required")
    if len(pages) > 5:
        raise argparse.ArgumentTypeError("Cloud Vision accepts at most 5 PDF pages")
    if any(page < 1 for page in pages):
        raise argparse.ArgumentTypeError("PDF page numbers are 1-based")
    if len(set(pages)) != len(pages):
        raise argparse.ArgumentTypeError("PDF page numbers must be unique")
    return pages


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CloudVisionClient:
    def __init__(self, config: CloudVisionConfig, timeout: float = 180.0) -> None:
        if timeout <= 0:
            raise CloudVisionError("Timeout must be greater than zero")
        self.config = config
        self.timeout = timeout

    def annotate_image(self, image_path: Path) -> dict[str, Any]:
        self._validate_file(image_path, "image")
        payload = {
            "requests": [
                {
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                    "image": {"content": self._encode_file(image_path)},
                }
            ]
        }
        response = self._post(self.config.image_api_url, payload)
        self._validate_image_response(response)
        return self._build_result(image_path, "image", response)

    def annotate_pdf(self, pdf_path: Path, pages: list[int]) -> dict[str, Any]:
        self._validate_file(pdf_path, "pdf")
        if pdf_path.suffix.lower() != ".pdf":
            raise CloudVisionError(f"Input file is not a PDF: {pdf_path}")
        if not pages or len(pages) > 5 or any(page < 1 for page in pages):
            raise CloudVisionError("PDF OCR requires 1 to 5 positive page numbers")
        if len(set(pages)) != len(pages):
            raise CloudVisionError("PDF page numbers must be unique")

        payload = {
            "requests": [
                {
                    "inputConfig": {
                        "content": self._encode_file(pdf_path),
                        "mimeType": "application/pdf",
                    },
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                    "pages": pages,
                }
            ]
        }
        response = self._post(self.config.file_api_url, payload)
        self._validate_pdf_response(response)
        return self._build_result(pdf_path, "pdf", response, pages)

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = requests.post(
                url,
                headers={
                    "Content-type": "application/json",
                    "api-key": self.config.api_key,
                },
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise CloudVisionError(f"Cloud Vision request failed: {error}") from error

        if not response.ok:
            raise CloudVisionError(f"Cloud Vision returned HTTP {response.status_code}")
        try:
            data = response.json()
        except (requests.JSONDecodeError, ValueError) as error:
            raise CloudVisionError("Cloud Vision returned invalid JSON") from error
        if not isinstance(data, dict):
            raise CloudVisionError("Cloud Vision response must be a JSON object")
        if isinstance(data.get("error"), dict):
            raise CloudVisionError(self._api_error_message(data["error"]))
        return data

    @staticmethod
    def _validate_image_response(response: dict[str, Any]) -> None:
        responses = response.get("responses")
        if not isinstance(responses, list) or len(responses) != 1:
            raise CloudVisionError("Image OCR response has an invalid responses array")
        item = responses[0]
        if not isinstance(item, dict):
            raise CloudVisionError("Image OCR response item must be an object")
        if isinstance(item.get("error"), dict):
            raise CloudVisionError(CloudVisionClient._api_error_message(item["error"]))
        if not isinstance(item.get("fullTextAnnotation"), dict):
            raise CloudVisionError("Image OCR response is missing fullTextAnnotation")

    @staticmethod
    def _validate_pdf_response(response: dict[str, Any]) -> None:
        file_responses = response.get("responses")
        if not isinstance(file_responses, list) or len(file_responses) != 1:
            raise CloudVisionError("PDF OCR response has an invalid responses array")
        file_response = file_responses[0]
        if not isinstance(file_response, dict):
            raise CloudVisionError("PDF OCR file response must be an object")
        if isinstance(file_response.get("error"), dict):
            raise CloudVisionError(
                CloudVisionClient._api_error_message(file_response["error"])
            )
        page_responses = file_response.get("responses")
        if not isinstance(page_responses, list) or not page_responses:
            raise CloudVisionError("PDF OCR response does not contain page results")
        for page_response in page_responses:
            if not isinstance(page_response, dict):
                raise CloudVisionError("PDF OCR page response must be an object")
            if isinstance(page_response.get("error"), dict):
                raise CloudVisionError(
                    CloudVisionClient._api_error_message(page_response["error"])
                )
            if not isinstance(page_response.get("fullTextAnnotation"), dict):
                raise CloudVisionError(
                    "PDF OCR page response is missing fullTextAnnotation"
                )

    @staticmethod
    def _api_error_message(error: dict[str, Any]) -> str:
        code = error.get("code")
        message = error.get("message")
        details = f"Cloud Vision API error {code}" if code is not None else "Cloud Vision API error"
        if isinstance(message, str) and message:
            details = f"{details}: {message}"
        return details

    @staticmethod
    def _validate_file(path: Path, kind: str) -> None:
        if not path.is_file():
            raise CloudVisionError(f"Input {kind} does not exist: {path}")
        if path.stat().st_size == 0:
            raise CloudVisionError(f"Input {kind} is empty: {path}")

    @staticmethod
    def _encode_file(path: Path) -> str:
        try:
            return base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as error:
            raise CloudVisionError(f"Could not read input file: {path}") from error

    @staticmethod
    def _build_result(
        source_path: Path,
        request_type: str,
        response: dict[str, Any],
        pages: list[int] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source": {
                "path": str(source_path),
                "sha256": sha256_file(source_path),
                "size_bytes": source_path.stat().st_size,
            },
            "request": {
                "type": request_type,
                "pages": pages,
            },
            "response": response,
        }


def save_result(result: dict[str, Any], output_path: Path, force: bool = False) -> None:
    validate_output_path(output_path, force)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(result, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
        os.replace(temporary_path, output_path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise CloudVisionError(f"Could not save OCR result: {output_path}") from error


def validate_output_path(output_path: Path, force: bool = False) -> None:
    if output_path.exists() and not force:
        raise CloudVisionError(f"Output file already exists: {output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run OCR with a Google Vision-compatible Cloud Vision API."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Path to the environment file (default: .env).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Request timeout in seconds (default: 180).",
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    image_parser = subparsers.add_parser("image", help="OCR one image.")
    image_parser.add_argument("input", type=Path, help="Path to the input image.")
    image_parser.add_argument("--output", required=True, type=Path)
    image_parser.add_argument("--force", action="store_true")

    pdf_parser = subparsers.add_parser("pdf", help="OCR selected PDF pages.")
    pdf_parser.add_argument("input", type=Path, help="Path to the input PDF.")
    pdf_parser.add_argument("--pages", required=True, type=parse_pages)
    pdf_parser.add_argument("--output", required=True, type=Path)
    pdf_parser.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        validate_output_path(args.output, args.force)
        client = CloudVisionClient(load_config(args.env_file), timeout=args.timeout)
        if args.mode == "image":
            result = client.annotate_image(args.input)
        else:
            result = client.annotate_pdf(args.input, args.pages)
        save_result(result, args.output, args.force)
    except CloudVisionError as error:
        print(f"Cloud Vision OCR failed: {error}", file=sys.stderr)
        return 1

    print(f"Saved Cloud Vision OCR result to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())