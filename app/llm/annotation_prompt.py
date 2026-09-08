from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

PROMPT_VERSION = "rag-annotation-v1"

SYSTEM_PROMPT = """You classify Cisco network documentation for retrieval.
Return JSON only. Do not invent, correct, or normalize commands.
Every command must be copied from SOURCE_TEXT exactly, except whitespace differences.
Use only the allowed content_type values.
"""

USER_PROMPT_TEMPLATE = """Annotate the following source block.

Allowed content_type values:
- concept
- configuration_example
- command_reference
- warning
- parameter_reference

Return an object with exactly these fields:
content_type, prerequisites, commands, verification_commands, keywords, annotation_confidence

`commands` must be an array of objects with `text` and `role`.
If a value is not present in SOURCE_TEXT, return an empty array.
Do not include document IDs, URLs, section titles, or other metadata.

SOURCE_TEXT:
{source_text}
"""


@dataclass(frozen=True)
class AnnotationRequest:
    prompt_version: str
    prompt_hash: str
    input_hash: str
    system_prompt: str
    user_prompt: str

    def cache_key(self) -> str:
        return f"{self.prompt_version}:{self.prompt_hash}:{self.input_hash}"

    def to_dict(self) -> dict[str, str]:
        return {
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "input_hash": self.input_hash,
            "system_prompt": self.system_prompt,
            "user_prompt": self.user_prompt,
        }


def build_annotation_request(source_text: str) -> AnnotationRequest:
    if not source_text.strip():
        raise ValueError("source_text must not be empty")
    user_prompt = USER_PROMPT_TEMPLATE.format(source_text=source_text)
    return AnnotationRequest(
        prompt_version=PROMPT_VERSION,
        prompt_hash=_sha256(f"{SYSTEM_PROMPT}\n{USER_PROMPT_TEMPLATE}"),
        input_hash=_sha256(source_text),
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
    )


def annotation_metadata(
    request: AnnotationRequest,
    *,
    model: str,
    created_at: str,
    response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "model": model,
        "prompt_version": request.prompt_version,
        "prompt_hash": request.prompt_hash,
        "input_hash": request.input_hash,
        "created_at": created_at,
    }
    if response is not None:
        serialized = json.dumps(response, ensure_ascii=False, sort_keys=True)
        metadata["response_hash"] = _sha256(serialized)
    return metadata


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
