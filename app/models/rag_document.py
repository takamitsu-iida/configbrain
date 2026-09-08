from __future__ import annotations

from enum import StrEnum
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class ContentType(StrEnum):
    CONCEPT = "concept"
    CONFIGURATION_EXAMPLE = "configuration_example"
    COMMAND_REFERENCE = "command_reference"
    WARNING = "warning"
    PARAMETER_REFERENCE = "parameter_reference"


class ChunkLevel(StrEnum):
    PARENT = "parent"
    CHILD = "child"


class AnnotationStatus(StrEnum):
    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    role: str = Field(min_length=1)


class LlmAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    prompt_hash: str = Field(min_length=64, max_length=64)
    input_hash: str = Field(min_length=64, max_length=64)
    created_at: str = Field(min_length=1)
    response_hash: str | None = Field(default=None, min_length=64, max_length=64)


class RagDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    source_url: HttpUrl
    section_url: HttpUrl
    section_title: str = Field(min_length=1)
    content_type: ContentType
    product: str = Field(min_length=1)
    os: str = Field(min_length=1)
    os_version: str = Field(min_length=1)
    source_text: str = Field(min_length=1)
    parent_block_id: str | None = None
    chunk_level: ChunkLevel = ChunkLevel.CHILD
    prerequisites: list[str] = Field(default_factory=list)
    commands: list[Command] = Field(default_factory=list)
    verification_commands: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    annotation_status: AnnotationStatus = AnnotationStatus.NEEDS_REVIEW
    annotation_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    validation_errors: list[str] = Field(default_factory=list)
    llm_annotation: LlmAnnotation | None = None

    @model_validator(mode="after")
    def validate_parent_relationship(self) -> RagDocument:
        if self.chunk_level is ChunkLevel.PARENT and self.parent_block_id is not None:
            raise ValueError("parent chunks must not have parent_block_id")
        if self.chunk_level is ChunkLevel.CHILD and not self.parent_block_id:
            raise ValueError("child chunks require parent_block_id")
        if self.annotation_status is AnnotationStatus.VERIFIED and self.validation_errors:
            raise ValueError("verified documents must not contain validation_errors")
        return self


def validate_commands_in_source(document: RagDocument) -> list[str]:
    """Return extracted commands and prerequisites missing from preserved source."""
    normalized_source = _normalize_source(document.source_text)
    errors: list[str] = []
    for command in document.commands:
        if _normalize_source(command.text) not in normalized_source:
            errors.append(f"command not found in source_text: {command.text}")
    for command in document.verification_commands:
        if _normalize_source(command) not in normalized_source:
            errors.append(f"verification command not found in source_text: {command}")
    for prerequisite in document.prerequisites:
        if _normalize_source(prerequisite) not in normalized_source:
            errors.append(f"prerequisite not found in source_text: {prerequisite}")
    return errors


def verify_annotation(document: RagDocument) -> RagDocument:
    """Mark an annotation verified only when every command exists in source_text."""
    validation_errors = validate_commands_in_source(document)
    if validation_errors:
        return document.model_copy(
            update={
                "annotation_status": AnnotationStatus.REJECTED,
                "validation_errors": validation_errors,
            }
        )
    return document.model_copy(
        update={
            "annotation_status": AnnotationStatus.VERIFIED,
            "validation_errors": [],
        }
    )


def rag_document_json_schema() -> dict[str, Any]:
    return RagDocument.model_json_schema()


def _normalize_source(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
