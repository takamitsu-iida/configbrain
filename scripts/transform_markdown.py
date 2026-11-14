"""Transform Markdown chunks through an OpenAI-compatible API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests
from dotenv import dotenv_values


class TransformationError(RuntimeError):
    """Raised when an LLM transformation cannot be completed safely."""


class QuotaThresholdError(TransformationError):
    """Raised after the configured LLM quota threshold is reached."""


class GracefulStopRequested(RuntimeError):
    """Raised at a checkpoint after a graceful stop is requested."""


SYSTEM_PROMPT = """あなたはCiscoの英語マニュアルを整形する専門家です。
入力本文は英語のまま維持し、意味、コマンド、製品名、リンク、画像参照を変更しないでください。
次の変換だけを行ってください。
1. CLIプロンプトと設定例を適切なMarkdownコードブロックにする。
2. Warning、Caution、NoteをGitHub Alerts（> [!WARNING]等）へ変換する。
3. Markerが作った不要なヘッダー、フッター、ページ番号だけを削除する。
4. 文書のタイトルと要約をmetadataへ抽出する。sourceには出版社または提供元組織を記載する。
必ずJSONオブジェクトだけを返してください。キーはmarkdownとmetadataです。
metadataはtitle、description、product、version、sourceのキーを持つオブジェクトにしてください。
値が不明な場合は空文字列にしてください。markdown内にYAMLフロントマターは含めないでください。"""
SYSTEM_PROMPT += """
CONFIGBRAIN_LITERAL_から始まる保護トークンはMarkdownリンク、画像、HTMLアンカーの値です。
周囲のMarkdownまたはHTML構文を含め、削除、変更、移動、重複させず、そのまま出力してください。"""

FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})")
PROTECTED_LITERAL_PATTERN = re.compile(
    r"!\[[^\]]*]\([^)\n]+\)"
    r"|(?<!!)\[[^\]]+]\([^)\n]+\)"
    r"|<span\s+id=[\"'][^\"']+[\"']\s*></span>"
)
MARKER_RETURN_NAVIGATION_PATTERN = re.compile(
    r"^(?:<span\s+id=[\"'][^\"']+[\"']\s*></span>)?"
    r"Return to .+?,?\s+on "
    r"(?:page|\[page]\([^)\n]+\))\s+\d+\.?\s*$",
    re.MULTILINE | re.IGNORECASE,
)
REQUIRED_METADATA_KEYS = ("title", "description", "product", "version", "source")
MAX_RESPONSE_VALIDATION_ATTEMPTS = 2


@dataclass(frozen=True)
class LLMConfig:
    api_url: str
    api_key: str
    model: str | None
    api_key_header: str
    timeout: float = 180.0
    quota_stop_percent: float = 95.0


def load_config(
    env_file: Path = Path(".env"),
    quota_stop_percent: float | None = None,
) -> LLMConfig:
    values = dotenv_values(env_file) if env_file.is_file() else {}

    def optional_value(*names: str) -> str | None:
        for name in names:
            value = os.environ.get(name) or values.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    try:
        timeout = float(optional_value("LLM_TIMEOUT") or "180")
    except ValueError as error:
        raise TransformationError("LLM_TIMEOUT must be a positive number") from error
    if timeout <= 0:
        raise TransformationError("LLM_TIMEOUT must be a positive number")
    if quota_stop_percent is None:
        try:
            quota_stop_percent = float(
                optional_value("LLM_QUOTA_STOP_PERCENT") or "95"
            )
        except ValueError as error:
            raise TransformationError(
                "LLM_QUOTA_STOP_PERCENT must be a number from 0 to 100"
            ) from error
    if not 0 <= quota_stop_percent <= 100:
        raise TransformationError(
            "LLM quota stop percent must be from 0 to 100"
        )
    api_url = optional_value("LLM_API_URL", "CLOUD_GPT_API_URL")
    api_key = optional_value("LLM_API_KEY", "CLOUD_GPT_API_KEY")
    if api_url is None:
        raise TransformationError(
            "Missing LLM setting: LLM_API_URL or CLOUD_GPT_API_URL"
        )
    if api_key is None:
        raise TransformationError(
            "Missing LLM setting: LLM_API_KEY or CLOUD_GPT_API_KEY"
        )
    api_key_header = optional_value("LLM_API_KEY_HEADER")
    if api_key_header is None:
        api_key_header = (
            "api-key"
            if optional_value("CLOUD_GPT_API_URL") and not optional_value("LLM_API_URL")
            else "Authorization"
        )
    return LLMConfig(
        api_url=api_url,
        api_key=api_key,
        model=optional_value("LLM_MODEL"),
        api_key_header=api_key_header,
        timeout=timeout,
        quota_stop_percent=quota_stop_percent,
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_prompt(
    markdown: str,
    protected_tokens: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    if not markdown.strip():
        raise TransformationError("Input Markdown is empty")
    token_instruction = ""
    if protected_tokens:
        token_instruction = (
            "\n保護トークンは入力に存在する回数と同じ回数だけ、完全一致で出力してください。"
            "次のトークンは一つも省略、統合、複製しないでください:\n"
            + ", ".join(protected_tokens)
        )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "次のMarkdownチャンクだけを変換してください。JSON以外の文字は返さないでください。\n\n"
                + token_instruction
                + "\n\n"
                + markdown
            ),
        },
    ]


def remove_marker_navigation(markdown: str) -> str:
    cleaned = MARKER_RETURN_NAVIGATION_PATTERN.sub("", markdown)
    return re.sub(r"\n{3,}", "\n\n", cleaned)


def protect_literals(markdown: str) -> tuple[str, dict[str, str]]:
    literals: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        token = f"CONFIGBRAIN_LITERAL_{len(literals) + 1:06d}"
        literal = match.group()
        if literal.startswith("<span"):
            id_match = re.search(r"\bid=([\"'])([^\"']+)\1", literal)
            if id_match is None:
                raise TransformationError("Could not protect HTML anchor")
            value = id_match.group(2)
        else:
            target_start = literal.rfind("](")
            if target_start < 0 or not literal.endswith(")"):
                raise TransformationError("Could not protect Markdown literal")
            value = literal[target_start + 2 : -1]
        literals[token] = value
        return literal.replace(value, token, 1)

    return PROTECTED_LITERAL_PATTERN.sub(replace, markdown), literals


def restore_literals(markdown: str, literals: dict[str, str]) -> str:
    for token, literal in literals.items():
        count = markdown.count(token)
        if count != 1:
            raise TransformationError(
                "LLM did not preserve protected literal exactly once: "
                f"{token} (found {count})"
            )
        markdown = markdown.replace(token, literal)
    if "CONFIGBRAIN_LITERAL_" in markdown:
        raise TransformationError("LLM returned an unknown protected literal")
    return markdown


def _validate_fences(markdown: str) -> None:
    active: str | None = None
    for line in markdown.splitlines():
        match = FENCE_PATTERN.match(line)
        if match is None:
            continue
        marker = match.group(1)[0]
        if active is None:
            active = marker
        elif marker == active:
            active = None
    if active is not None:
        raise TransformationError("LLM returned Markdown with an unclosed code fence")


def validate_result(value: Any) -> tuple[str, dict[str, str]]:
    if not isinstance(value, dict):
        raise TransformationError("LLM response must be a JSON object")
    markdown = value.get("markdown")
    metadata = value.get("metadata")
    if not isinstance(markdown, str) or not markdown.strip():
        raise TransformationError("LLM response has no non-empty markdown")
    if not isinstance(metadata, dict):
        raise TransformationError("LLM response metadata must be an object")
    normalized: dict[str, str] = {}
    for key in REQUIRED_METADATA_KEYS:
        item = metadata.get(key, "")
        if not isinstance(item, str):
            raise TransformationError(f"Metadata field must be a string: {key}")
        normalized[key] = item.strip()
    _validate_fences(markdown)
    if re.match(r"^\s*---\s*$", markdown):
        raise TransformationError("LLM response must not contain YAML frontmatter")
    return markdown.rstrip() + "\n", normalized


def render_frontmatter(metadata: dict[str, str]) -> str:
    # JSON is a valid YAML 1.2 document and avoids unsafe handwritten escaping.
    return "---\n" + json.dumps(metadata, ensure_ascii=False, indent=2) + "\n---\n\n"


def aggregate_metadata(records: list[dict[str, Any]]) -> dict[str, str]:
    aggregated: dict[str, str] = {}
    for key in REQUIRED_METADATA_KEYS:
        values = [
            metadata[key]
            for record in records
            if isinstance((metadata := record.get("metadata")), dict)
            and isinstance(metadata.get(key), str)
            and metadata[key]
        ]
        if not values:
            aggregated[key] = ""
            continue
        aggregated[key] = values[0]
    return aggregated


def _write_manifest(output_dir: Path, records: list[dict[str, Any]]) -> None:
    manifest = {
        "schema_version": 1,
        "complete": False,
        "document_metadata": aggregate_metadata(records),
        "chunks": records,
    }
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_dir,
            prefix=".manifest.",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(manifest, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
        os.replace(temporary_path, output_dir / "manifest.json")
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise TransformationError("Could not save transformation manifest") from error


def _write_text_atomic(path: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
        os.replace(temporary_path, path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise TransformationError(f"Could not save transformed chunk: {path}") from error


def _load_resume_records(output_dir: Path, chunks: list[Path]) -> list[dict[str, Any]]:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        if any(output_dir.iterdir()):
            raise TransformationError(
                f"Resume output has no manifest but is not empty: {output_dir}"
            )
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TransformationError(f"Could not read resume manifest: {manifest_path}") from error
    records = manifest.get("chunks") if isinstance(manifest, dict) else None
    if not isinstance(records, list) or len(records) > len(chunks):
        raise TransformationError("Resume manifest has an invalid chunks array")
    for index, record in enumerate(records):
        chunk_path = chunks[index]
        output_path = output_dir / f"chunk-{index + 1:04d}.md"
        if not isinstance(record, dict) or record.get("id") != chunk_path.stem:
            raise TransformationError("Resume manifest does not match input chunk order")
        try:
            source = chunk_path.read_text(encoding="utf-8")
            output = output_path.read_text(encoding="utf-8")
        except OSError as error:
            raise TransformationError(
                f"Could not validate completed resume chunk: {output_path}"
            ) from error
        if (
            record.get("source_sha256") != sha256_text(source)
            or record.get("output_sha256") != sha256_text(output)
        ):
            raise TransformationError(
                f"Completed resume chunk checksum mismatch: {output_path}"
            )
    return records


class OpenAICompatibleClient:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.quota_usage_percent: float | None = None

    def raise_if_quota_reached(self) -> None:
        if (
            self.quota_usage_percent is not None
            and self.quota_usage_percent >= self.config.quota_stop_percent
        ):
            raise QuotaThresholdError(
                "LLM quota usage reached "
                f"{self.quota_usage_percent:g}% "
                f"(stop threshold: {self.config.quota_stop_percent:g}%)"
            )

    def transform(self, markdown: str) -> tuple[str, dict[str, str]]:
        cleaned_markdown = remove_marker_navigation(markdown)
        protected_markdown, literals = protect_literals(cleaned_markdown)
        messages = build_prompt(
            protected_markdown,
            tuple(literals),
        )
        validation_error: TransformationError | None = None
        for attempt in range(MAX_RESPONSE_VALIDATION_ATTEMPTS):
            attempt_messages = list(messages)
            if attempt > 0 and validation_error is not None:
                attempt_messages.insert(
                    1,
                    {
                        "role": "system",
                        "content": (
                            "前回の応答は検証に失敗しました。元入力から再生成してください。"
                            "コードフェンスは必ず閉じ、保護トークンを正確に1回ずつ保持してください。"
                            f"検証エラー: {validation_error}"
                        ),
                    },
                )
            result = self._request(attempt_messages)
            try:
                transformed, metadata = validate_result(result)
                return restore_literals(transformed, literals), metadata
            except TransformationError as error:
                validation_error = error
                self.raise_if_quota_reached()
        if validation_error is None:
            raise TransformationError("LLM response validation failed")
        raise validation_error

    def _request(self, messages: list[dict[str, str]]) -> Any:
        payload: dict[str, Any] = {
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        if self.config.model is not None:
            payload["model"] = self.config.model
        credential = self.config.api_key
        if self.config.api_key_header.lower() == "authorization":
            credential = "Bearer " + credential
        try:
            response = requests.post(
                self.config.api_url,
                headers={
                    self.config.api_key_header: credential,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.config.timeout,
            )
        except requests.RequestException as error:
            raise TransformationError(f"LLM request failed: {error}") from error
        if not response.ok:
            raise TransformationError(f"LLM returned HTTP {response.status_code}")
        raw_quota = getattr(response, "headers", {}).get(
            "X-Quota-Usage-Percent"
        )
        if raw_quota is None:
            print("LLM quota usage: unavailable", flush=True)
        else:
            try:
                quota_usage = float(raw_quota.strip().removesuffix("%"))
            except (AttributeError, ValueError) as error:
                raise TransformationError(
                    "LLM returned invalid X-Quota-Usage-Percent header: "
                    f"{raw_quota!r}"
                ) from error
            if not 0 <= quota_usage <= 100:
                raise TransformationError(
                    "LLM returned out-of-range X-Quota-Usage-Percent header: "
                    f"{raw_quota!r}"
                )
            self.quota_usage_percent = quota_usage
            print(f"LLM quota usage: {quota_usage:g}%", flush=True)
        try:
            body = response.json()
        except (requests.JSONDecodeError, ValueError) as error:
            raise TransformationError("LLM returned invalid JSON") from error
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise TransformationError("LLM response has no chat completion content") from error
        if not isinstance(content, str):
            raise TransformationError("LLM chat completion content must be a string")
        try:
            return json.loads(content)
        except json.JSONDecodeError as error:
            raise TransformationError("LLM content is not valid JSON") from error


def _load_chunks(input_dir: Path) -> list[Path]:
    manifest_path = input_dir / "manifest.json"
    if not manifest_path.is_file():
        raise TransformationError(f"Chunk manifest does not exist: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TransformationError(f"Could not read chunk manifest: {manifest_path}") from error
    entries = manifest.get("chunks") if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or not entries:
        raise TransformationError("Chunk manifest has no chunks")
    paths: list[Path] = []
    for entry in entries:
        filename = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise TransformationError("Chunk manifest contains an invalid chunk path")
        path = input_dir / filename
        if not path.is_file():
            raise TransformationError(f"Chunk does not exist: {path}")
        paths.append(path)
    return paths


def transform_chunks(
    input_dir: Path,
    output_dir: Path,
    client: OpenAICompatibleClient,
    resume: bool = False,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    if output_dir.exists() and not resume:
        raise TransformationError(f"Output directory already exists: {output_dir}")
    chunks = _load_chunks(input_dir)
    if resume:
        output_dir.mkdir(parents=True, exist_ok=True)
        records = _load_resume_records(output_dir, chunks)
        for index, chunk_path in enumerate(chunks[len(records) :], start=len(records) + 1):
            try:
                source = chunk_path.read_text(encoding="utf-8")
            except OSError as error:
                raise TransformationError(f"Could not read chunk: {chunk_path}") from error
            markdown, metadata = client.transform(source)
            output = render_frontmatter(metadata) + markdown
            filename = f"chunk-{index:04d}.md"
            output_path = output_dir / filename
            _write_text_atomic(output_path, output)
            records.append(
                {
                    "id": chunk_path.stem,
                    "path": filename,
                    "source_sha256": sha256_text(source),
                    "output_sha256": sha256_text(output),
                    "metadata": metadata,
                }
            )
            _write_manifest(output_dir, records)
            print(
                f"Transformed chunk {index}/{len(chunks)}: {chunk_path.name}",
                flush=True,
            )
            if stop_requested is not None and stop_requested():
                raise GracefulStopRequested(
                    f"stop requested after checkpointing {chunk_path.name}"
                )
            quota_check = getattr(client, "raise_if_quota_reached", None)
            if quota_check is not None:
                quota_check()
        manifest_path = output_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["complete"] = True
        temporary_path = output_dir / ".manifest.complete"
        temporary_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, manifest_path)
        return

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        records: list[dict[str, Any]] = []
        for index, chunk_path in enumerate(chunks, start=1):
            try:
                source = chunk_path.read_text(encoding="utf-8")
            except OSError as error:
                raise TransformationError(f"Could not read chunk: {chunk_path}") from error
            markdown, metadata = client.transform(source)
            output = render_frontmatter(metadata) + markdown
            filename = f"chunk-{index:04d}.md"
            (temporary_dir / filename).write_text(output, encoding="utf-8")
            records.append(
                {
                    "id": chunk_path.stem,
                    "path": filename,
                    "source_sha256": sha256_text(source),
                    "output_sha256": sha256_text(output),
                    "metadata": metadata,
                }
            )
            print(
                f"Transformed chunk {index}/{len(chunks)}: {chunk_path.name}",
                flush=True,
            )
            quota_check = getattr(client, "raise_if_quota_reached", None)
            if quota_check is not None:
                quota_check()
        _write_manifest(temporary_dir, records)
        manifest_path = temporary_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["complete"] = True
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_dir, output_dir)
    except Exception:
        for path in temporary_dir.glob("*"):
            path.unlink(missing_ok=True)
        temporary_dir.rmdir()
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transform Markdown chunks with an LLM.")
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--quota-stop-percent",
        type=float,
        default=95.0,
        help=(
            "Stop after saving a response whose X-Quota-Usage-Percent is at "
            "or above this value (default: 95)."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Keep each completed chunk and safely resume after interruption.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        transform_chunks(
            args.input_dir,
            args.output_dir,
            OpenAICompatibleClient(
                load_config(
                    args.env_file,
                    quota_stop_percent=args.quota_stop_percent,
                )
            ),
            resume=args.resume,
        )
    except TransformationError as error:
        print(f"Markdown transformation failed: {error}", file=sys.stderr)
        return 1
    print(f"Saved transformed Markdown to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
