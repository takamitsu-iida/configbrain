"""Index Cisco data packages and generate grounded configurations."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import requests
import torch
import torch.nn.functional as functional
from frictionless import Package
from qdrant_client import QdrantClient, models
from transformers import AutoModel, AutoTokenizer

if __package__:
    from .config_generation_service import (
        DEFAULT_KNOWLEDGE_POLICY,
        KNOWLEDGE_POLICIES,
        ConfigurationGenerationError,
        ConfigurationGenerationRequest,
        ConfigurationGenerationService,
    )
    from .transform_markdown import LLMConfig, load_config
else:
    from config_generation_service import (
        DEFAULT_KNOWLEDGE_POLICY,
        KNOWLEDGE_POLICIES,
        ConfigurationGenerationError,
        ConfigurationGenerationRequest,
        ConfigurationGenerationService,
    )
    from transform_markdown import LLMConfig, load_config


class RAGError(RuntimeError):
    """Raised when RAG indexing or generation cannot be completed safely."""


DEFAULT_MODEL = "intfloat/multilingual-e5-small"
DEFAULT_COLLECTION = "cisco_manual_sections"
SECTION_INDEX_VERSION = 2
FRONTMATTER_PATTERN = re.compile(r"\A---\n.*?\n---\n\n", re.DOTALL)
HEADING_PATTERN = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*$")
FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})")
SEARCH_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.:/-]*", re.IGNORECASE)
PRODUCT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")
IPV4_PATTERN = re.compile(
    r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"
)
IPV4_PREFIX_PATTERN = re.compile(
    r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}/(?:[0-9]|[12]\d|3[0-2])(?!\d)"
)
IPV4_ADDRESS_MASK_PATTERN = re.compile(
    r"\bip\s+address\s+(?:\d{1,3}\.){3}\d{1,3}\s+"
    r"(?:\d{1,3}\.){3}\d{1,3}\b",
    re.IGNORECASE,
)
HOSTNAME_PATTERN = re.compile(
    r"(?<![A-Za-z0-9-])(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![A-Za-z0-9-])"
)
DHCP_RELAY_INTENT_PATTERN = re.compile(
    r"(?:DHCP.{0,80}(?:relay|リレー|中継|転送)"
    r"|(?:relay|リレー|中継|転送).{0,80}DHCP)",
    re.IGNORECASE,
)
DHCP_VRF_CONTEXT_PATTERN = re.compile(
    r"\b(?:tenant|different|cross|inter)[ -]?VRF\b"
    r"|テナント.{0,12}VRF|異なる.{0,12}VRF|VRF間",
    re.IGNORECASE,
)
VLAN_CREATION_PATTERN = re.compile(
    r"(?:\bVLAN\s*\d+.{0,24}(?:作成|新設|追加|create|add)"
    r"|(?:作成|新設|追加|create|add).{0,24}\bVLAN\s*\d+)",
    re.IGNORECASE | re.DOTALL,
)
VLAN_ID_PATTERN = re.compile(r"\bVLAN\s*(\d+)", re.IGNORECASE)
DHCP_VRF_HELPER_PATTERN = re.compile(
    r"^\s*ip\s+helper-address\s+(?:global\b|vrf\s+\S+)",
    re.IGNORECASE,
)
NEGATION_INTENT_PATTERN = re.compile(
    r"削除|解除|無効(?:化|に)|\b(?:remove|delete|disable)\b",
    re.IGNORECASE,
)
NO_AUTHENTICATION_PATTERN = re.compile(
    r"認証(?:を|は)?(?:使用|利用|使)(?:せず|しません?|しない|わない)|認証なし|without authentication",
    re.IGNORECASE,
)
NON_NEGATABLE_CLI_PREFIXES = (
    "show ",
    "clear ",
    "debug ",
    "ping ",
    "traceroute ",
)
MINIMAL_CONFIGURATION_PATTERN = re.compile(
    r"できるだけ短|最短|最小(?:限)?|\b(?:minimal|shortest)\b",
    re.IGNORECASE,
)
VLAN_NAME_INTENT_PATTERN = re.compile(
    r"(?:\bVLAN\b.*(?:名前|名称|名を|name)|(?:名前|名称|name).*\bVLAN\b)",
    re.IGNORECASE,
)
EXAMPLE_REQUEST_PATTERN = re.compile(
    r"設定例|コンフィグ例|構成例|(?:configuration|config)\s+example",
    re.IGNORECASE,
)
UNGROUNDED_WARNING_PATTERN = re.compile(
    r"(?:根拠|取得(?:結果|情報)).*"
    r"(?:ない|ありません|不足|確認でき(?:ない|ません)|記載されていません)"
    r"|一般的な.*(?:構文|知識).*(?:前提|補完)"
    r"|事前知識.*(?:使用|補完)",
    re.IGNORECASE,
)
INTERFACE_RANGE_DEFINITION_PATTERN = re.compile(
    r"^define\s+interface-range\s+(?P<name>\S+)\s+(?P<range>.+)$",
    re.IGNORECASE,
)
INTERFACE_RANGE_MACRO_PATTERN = re.compile(
    r"^interface\s+range\s+macro\s+(?P<name>\S+)$",
    re.IGNORECASE,
)
INTERFACE_NAME_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]+Ethernet|Port-channel)"
    r"\s*\d+(?:/\d+)*(?:\.\d+)?(?![A-Za-z0-9./-])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Section:
    citation_id: str
    document_id: str
    title: str
    version: str
    product: str
    release: str
    heading_path: tuple[str, ...]
    text: str
    source_url: str = ""


@dataclass(frozen=True)
class SearchResult:
    citation_id: str
    score: float
    document_id: str
    title: str
    version: str
    product: str
    release: str
    heading_path: tuple[str, ...]
    text: str
    source_url: str = ""


@dataclass(frozen=True)
class SearchFeature:
    queries: tuple[str, ...]
    cli_terms: tuple[str, ...]
    excluded_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class _LexicalCorpus:
    points: tuple[Any, ...]
    documents: tuple[tuple[str, ...], ...]
    document_frequencies: dict[str, int]
    average_length: float


class Embedder(Protocol):
    dimension: int

    def encode_passages(self, texts: list[str]) -> list[list[float]]: ...

    def encode_queries(self, texts: list[str]) -> list[list[float]]: ...


def clean_heading(value: str) -> str:
    value = re.sub(r"<span\s+id=[\"'][^\"']+[\"']\s*></span>", "", value)
    return re.sub(r"[*_`]", "", value).strip()


def split_markdown_sections(
    markdown: str,
    document_id: str,
    title: str,
    version: str,
    max_characters: int = 4_000,
    product: str = "",
    release: str = "",
    source_url: str = "",
) -> list[Section]:
    if max_characters < 500:
        raise RAGError("RAG section limit must be at least 500 characters")
    markdown = FRONTMATTER_PATTERN.sub("", markdown, count=1)
    heading_stack: list[str] = []
    current_path: tuple[str, ...] = ()
    lines: list[str] = []
    raw_sections: list[tuple[tuple[str, ...], str]] = []
    procedure_context: dict[int, tuple[str, ...]] = {}
    in_fence = False
    fence_marker = ""

    def save() -> None:
        text = "\n".join(lines).strip()
        if text:
            raw_sections.append((current_path, text))

    for line in markdown.splitlines():
        fence = FENCE_PATTERN.match(line)
        if fence:
            marker = fence.group(1)[0]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
        heading = None if in_fence else HEADING_PATTERN.match(line)
        if heading is None:
            lines.append(line)
            continue
        save()
        level = len(heading.group("level"))
        heading_value = clean_heading(heading.group("title"))
        generic_procedure_heading = heading_value.lower() in {
            "before you begin", "procedure",
        }
        if generic_procedure_heading:
            if (
                level not in procedure_context
                and len(heading_stack) >= level
                and re.search(
                    r"\b(?:creating|configuring|assigning|enabling|disabling|"
                    r"modifying|setting|installing|deleting|removing|specifying|"
                    r"managing)\b",
                    heading_stack[level - 1],
                    re.IGNORECASE,
                )
            ):
                procedure_context[level] = tuple(heading_stack[:level])
        else:
            procedure_context = {
                key: value for key, value in procedure_context.items() if key < level
            }
        heading_stack[level - 1 :] = [heading_value]
        owner = procedure_context.get(level) if generic_procedure_heading else None
        current_path = (*owner, heading_value) if owner else tuple(heading_stack)
        lines = [line]
    save()
    if in_fence:
        raise RAGError("Packaged Markdown has an unclosed code fence")

    sections: list[Section] = []
    for path, text in raw_sections:
        normalized_path = tuple(clean_heading(item) for item in path)
        if (
            normalized_path == (clean_heading(title),)
            or any(item.upper() == "CONTENTS" for item in normalized_path)
        ):
            continue
        searchable_path = (
            path[1:]
            if path and clean_heading(title).lower() == path[0].lower()
            else path
        )
        prefix = " > ".join(searchable_path)
        available = max_characters - len(prefix) - 2
        if available < 1:
            raise RAGError("Heading path exceeds the RAG section limit")
        blocks = [block for block in re.split(r"\n{2,}", text) if block.strip()]
        pieces: list[str] = []
        current: list[str] = []
        for block in blocks:
            block_parts = [
                block[index : index + available]
                for index in range(0, len(block), available)
            ]
            for part in block_parts:
                candidate = "\n\n".join([*current, part])
                if current and len(candidate) > available:
                    pieces.append("\n\n".join(current))
                    current = []
                current.append(part)
        if current:
            pieces.append("\n\n".join(current))
        for piece in pieces:
            number = len(sections) + 1
            content = f"{prefix}\n\n{piece}" if prefix else piece
            sections.append(
                Section(
                    citation_id=f"{document_id}:section-{number:04d}",
                    document_id=document_id,
                    title=title,
                    version=version,
                    product=product,
                    release=release,
                    heading_path=path,
                    text=content,
                    source_url=source_url,
                )
            )
    return sections


class E5Embedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, batch_size: int = 16) -> None:
        if batch_size < 1:
            raise RAGError("Embedding batch size must be positive")
        self.model_name = model_name
        self.batch_size = batch_size
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
            self.model = AutoModel.from_pretrained(model_name)
        except (OSError, ValueError) as error:
            raise RAGError(f"Could not load embedding model: {model_name}") from error
        self.model.eval()
        self.dimension = int(self.model.config.hidden_size)

    def _encode(self, texts: list[str], prefix: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = [prefix + text for text in texts[start : start + self.batch_size]]
            tokens = self.tokenizer(
                batch,
                max_length=512,
                padding=True,
                truncation=True,
                return_tensors="pt",
            )
            with torch.inference_mode():
                output = self.model(**tokens).last_hidden_state
                mask = tokens["attention_mask"].unsqueeze(-1)
                pooled = (output * mask).sum(1) / mask.sum(1).clamp(min=1)
                pooled = functional.normalize(pooled, p=2, dim=1)
            vectors.extend(pooled.cpu().tolist())
        return vectors

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, "passage: ")

    def encode_queries(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, "query: ")


def load_package_sections(package_dir: Path) -> list[Section]:
    descriptor_path = package_dir / "datapackage.json"
    report = Package(str(descriptor_path)).validate()
    if not report.valid:
        raise RAGError(
            f"Invalid data package {package_dir}: {report.flatten(['code', 'message'])}"
        )
    try:
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        markdown = (package_dir / "manual.md").read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as error:
        raise RAGError(f"Could not read data package: {package_dir}") from error
    document_id = descriptor.get("name")
    title = descriptor.get("title")
    version = descriptor.get("version", "")
    configbrain = descriptor.get("configbrain")
    if not isinstance(document_id, str) or not isinstance(title, str):
        raise RAGError(f"Data package has invalid identity metadata: {package_dir}")
    if not isinstance(configbrain, dict):
        raise RAGError(f"Data package has invalid configbrain metadata: {package_dir}")
    product = configbrain.get("catalogProduct")
    release = configbrain.get("release", version)
    source_url = descriptor.get("homepage", "")
    if (
        not isinstance(product, str)
        or not product.strip()
        or not isinstance(release, str)
        or not release.strip()
    ):
        raise RAGError(
            f"Data package has invalid normalized product metadata: {package_dir}"
        )
    if not isinstance(source_url, str):
        source_url = ""
    return split_markdown_sections(
        markdown,
        document_id,
        title,
        str(version),
        product=product.strip(),
        release=release.strip(),
        source_url=source_url.strip(),
    )


def package_descriptor_sha256(package_dir: Path) -> str:
    descriptor_path = package_dir / "datapackage.json"
    try:
        return hashlib.sha256(descriptor_path.read_bytes()).hexdigest()
    except OSError as error:
        raise RAGError(f"Could not read package descriptor: {descriptor_path}") from error


def package_product(package_dir: Path) -> str:
    descriptor_path = package_dir / "datapackage.json"
    try:
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RAGError(f"Could not read package descriptor: {descriptor_path}") from error
    configbrain = descriptor.get("configbrain")
    if not isinstance(configbrain, dict):
        raise RAGError(f"Data package has invalid configbrain metadata: {package_dir}")
    product = configbrain.get("catalogProduct")
    if not isinstance(product, str) or not product.strip():
        raise RAGError(
            f"Data package has no normalized catalog product metadata: {package_dir}"
        )
    return product.strip()


def section_point_id(section: Section) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, section.citation_id))


def _point(section: Section, vector: list[float]) -> models.PointStruct:
    return models.PointStruct(
        id=section_point_id(section),
        vector=vector,
        payload={
            "citation_id": section.citation_id,
            "document_id": section.document_id,
            "title": section.title,
            "version": section.version,
            "product": section.product,
            "release": section.release,
            "heading_path": list(section.heading_path),
            "text": section.text,
            "source_url": section.source_url,
        },
    )


def _upsert_sections(
    client: QdrantClient,
    collection: str,
    sections: list[Section],
    embedder: Embedder,
) -> None:
    for start in range(0, len(sections), 16):
        batch = sections[start : start + 16]
        vectors = embedder.encode_passages([section.text for section in batch])
        client.upsert(
            collection_name=collection,
            points=[
                _point(section, vector)
                for section, vector in zip(batch, vectors, strict=True)
            ],
            wait=True,
        )


def product_collection_name(
    product: str,
    collection_prefix: str = DEFAULT_COLLECTION,
) -> str:
    if not PRODUCT_ID_PATTERN.fullmatch(product):
        raise RAGError(f"Invalid catalog product identifier: {product}")
    return f"{collection_prefix}_{product}"


def _package_dirs_by_product(package_dirs: list[Path]) -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {}
    for package_dir in package_dirs:
        grouped.setdefault(package_product(package_dir), []).append(package_dir)
    return grouped


def _manifest_is_compatible(
    manifest: Any,
    collection_prefix: str,
    embedder: Embedder,
) -> bool:
    return (
        isinstance(manifest, dict)
        and manifest.get("schema_version") == 2
        and manifest.get("collection_prefix") == collection_prefix
        and manifest.get("embedding_model")
        == getattr(embedder, "model_name", "custom")
        and manifest.get("vector_size") == embedder.dimension
        and isinstance(manifest.get("source_sha256"), dict)
        and isinstance(manifest.get("source_products"), dict)
        and isinstance(manifest.get("collections"), dict)
    )


def _collection_manifest(
    client: QdrantClient,
    collection_prefix: str,
    source_products: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], int]:
    collections: dict[str, dict[str, Any]] = {}
    total_sections = 0
    for product in sorted(set(source_products.values())):
        name = product_collection_name(product, collection_prefix)
        if not client.collection_exists(name):
            raise RAGError(f"Qdrant product collection is missing: {name}")
        sections = client.count(collection_name=name, exact=True).count
        documents = sum(value == product for value in source_products.values())
        collections[product] = {
            "name": name,
            "documents": documents,
            "sections": sections,
        }
        total_sections += sections
    return collections, total_sections


def _write_index_manifest(qdrant_path: Path, manifest: dict[str, Any]) -> None:
    manifest_path = qdrant_path / "index-manifest.json"
    temporary_path = manifest_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(manifest_path)


def build_index(
    packages_dir: Path,
    qdrant_path: Path,
    embedder: Embedder,
    collection: str = DEFAULT_COLLECTION,
) -> dict[str, Any]:
    package_dirs = sorted(
        path.parent for path in packages_dir.glob("*/datapackage.json")
    )
    if not package_dirs:
        raise RAGError(f"No data packages found under: {packages_dir}")
    grouped = _package_dirs_by_product(package_dirs)
    qdrant_path.mkdir(parents=True, exist_ok=True)
    manifest_path = qdrant_path / "index-manifest.json"
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        previous = {}
    except (OSError, json.JSONDecodeError) as error:
        raise RAGError(f"Could not read index manifest: {manifest_path}") from error
    if previous.get("schema_version") == 1:
        raise RAGError(
            "Legacy shared collection must be migrated with the "
            "'migrate-collections' command before indexing"
        )
    if previous and not _manifest_is_compatible(previous, collection, embedder):
        raise RAGError(f"Incompatible index manifest: {manifest_path}")
    previous_versions = previous.get("section_versions", {})
    if not isinstance(previous_versions, dict):
        raise RAGError(f"Invalid section versions in index manifest: {manifest_path}")

    previous_sources: dict[str, str] = previous.get("source_sha256", {})
    previous_products: dict[str, str] = previous.get("source_products", {})
    rebuilt_products = set(grouped)
    combined_sources = {
        document_id: checksum
        for document_id, checksum in previous_sources.items()
        if previous_products.get(document_id) not in rebuilt_products
    }
    combined_products = {
        document_id: product
        for document_id, product in previous_products.items()
        if product not in rebuilt_products
    }
    combined_sources.update(
        {
            path.name: package_descriptor_sha256(path)
            for path in package_dirs
        }
    )
    combined_products.update(
        {path.name: package_product(path) for path in package_dirs}
    )

    client = QdrantClient(path=str(qdrant_path))
    try:
        for product, product_packages in grouped.items():
            product_collection = product_collection_name(product, collection)
            if client.collection_exists(product_collection):
                client.delete_collection(product_collection)
            client.create_collection(
                collection_name=product_collection,
                vectors_config=models.VectorParams(
                    size=embedder.dimension,
                    distance=models.Distance.COSINE,
                ),
            )
            sections = [
                section
                for package_dir in product_packages
                for section in load_package_sections(package_dir)
            ]
            _upsert_sections(client, product_collection, sections, embedder)
        collections, section_count = _collection_manifest(
            client,
            collection,
            combined_products,
        )
    finally:
        client.close()
    manifest = {
        "schema_version": 2,
        "collection_prefix": collection,
        "collections": collections,
        "embedding_model": getattr(embedder, "model_name", "custom"),
        "vector_size": embedder.dimension,
        "documents": len(combined_sources),
        "sections": section_count,
        "source_sha256": combined_sources,
        "source_products": combined_products,
        "section_versions": {
            **previous_versions,
            **{product: SECTION_INDEX_VERSION for product in rebuilt_products},
        },
    }
    _write_index_manifest(qdrant_path, manifest)
    return manifest


def update_index_incrementally(
    packages_dir: Path,
    qdrant_path: Path,
    embedder: Embedder,
    collection: str = DEFAULT_COLLECTION,
) -> dict[str, Any]:
    package_dirs = sorted(path.parent for path in packages_dir.glob("*/datapackage.json"))
    if not package_dirs:
        raise RAGError(f"No data packages found under: {packages_dir}")
    manifest_path = qdrant_path / "index-manifest.json"
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return build_index(packages_dir, qdrant_path, embedder, collection)
    except (OSError, json.JSONDecodeError) as error:
        raise RAGError(f"Could not read index manifest: {manifest_path}") from error
    if isinstance(previous, dict) and previous.get("schema_version") == 1:
        raise RAGError(
            "Legacy shared collection must be migrated with the "
            "'migrate-collections' command before incremental indexing"
        )
    if not _manifest_is_compatible(previous, collection, embedder):
        raise RAGError(f"Incompatible index manifest: {manifest_path}")

    current = {path.name: package_descriptor_sha256(path) for path in package_dirs}
    current_products = {path.name: package_product(path) for path in package_dirs}
    grouped = _package_dirs_by_product(package_dirs)
    updated_products = set(current_products.values())
    previous_sources: dict[str, str] = previous["source_sha256"]
    previous_products: dict[str, str] = previous["source_products"]
    checksum_changes = {
        document_id
        for document_id, checksum in current.items()
        if previous_sources.get(document_id) != checksum
    }
    previous_versions = previous.get("section_versions", {})
    if not isinstance(previous_versions, dict):
        raise RAGError(f"Invalid section versions in index manifest: {manifest_path}")
    checksum_changes.update(
        document_id
        for document_id, product in current_products.items()
        if previous_versions.get(product) != SECTION_INDEX_VERSION
    )

    client = QdrantClient(path=str(qdrant_path))
    try:
        metadata_changes = {
            document_id
            for document_id, product in current_products.items()
            if previous_sources.get(document_id) is not None
            and previous_products.get(document_id) != product
        }
        changed = sorted(checksum_changes | metadata_changes)
        removed = sorted(
            document_id
            for document_id in set(previous_sources) - set(current)
            if previous_products.get(document_id) in updated_products
        )
        for product in updated_products:
            product_collection = product_collection_name(product, collection)
            if not client.collection_exists(product_collection):
                client.create_collection(
                    collection_name=product_collection,
                    vectors_config=models.VectorParams(
                        size=embedder.dimension,
                        distance=models.Distance.COSINE,
                    ),
                )
        for document_id in [*changed, *removed]:
            products = {
                value
                for value in (
                    previous_products.get(document_id),
                    current_products.get(document_id),
                )
                if value
            }
            for product in products:
                product_collection = product_collection_name(product, collection)
                if client.collection_exists(product_collection):
                    client.delete(
                        collection_name=product_collection,
                        points_selector=models.FilterSelector(
                            filter=models.Filter(
                                must=[
                                    models.FieldCondition(
                                        key="document_id",
                                        match=models.MatchValue(value=document_id),
                                    )
                                ]
                            )
                        ),
                        wait=True,
                    )
        for product_packages in grouped.values():
            for package_dir in product_packages:
                if package_dir.name in changed:
                    product = current_products[package_dir.name]
                    _upsert_sections(
                        client,
                        product_collection_name(product, collection),
                        load_package_sections(package_dir),
                        embedder,
                    )

        combined_sources = {**previous_sources, **current}
        combined_products = {**previous_products, **current_products}
        for document_id in removed:
            combined_sources.pop(document_id, None)
            combined_products.pop(document_id, None)
        collections, sections = _collection_manifest(
            client,
            collection,
            combined_products,
        )
    finally:
        client.close()

    manifest = {
        "schema_version": 2,
        "collection_prefix": collection,
        "collections": collections,
        "embedding_model": getattr(embedder, "model_name", "custom"),
        "vector_size": embedder.dimension,
        "documents": len(combined_sources),
        "sections": sections,
        "source_sha256": combined_sources,
        "source_products": combined_products,
        "section_versions": {
            **previous_versions,
            **{product: SECTION_INDEX_VERSION for product in updated_products},
        },
        "incremental": {"updated": changed, "removed": removed},
    }
    _write_index_manifest(qdrant_path, manifest)
    return manifest


def migrate_product_collections(
    qdrant_path: Path,
    collection: str = DEFAULT_COLLECTION,
) -> dict[str, Any]:
    manifest_path = qdrant_path / "index-manifest.json"
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RAGError(f"Could not read index manifest: {manifest_path}") from error
    if isinstance(previous, dict) and previous.get("schema_version") == 2:
        return previous
    if (
        not isinstance(previous, dict)
        or previous.get("schema_version") != 1
        or previous.get("collection") != collection
        or not isinstance(previous.get("source_sha256"), dict)
        or not isinstance(previous.get("source_products"), dict)
    ):
        raise RAGError(f"Index manifest cannot be migrated: {manifest_path}")

    source_products: dict[str, str] = previous["source_products"]
    products = sorted(set(source_products.values()))
    if not products:
        raise RAGError("Index manifest has no products to migrate")

    client = QdrantClient(path=str(qdrant_path))
    created: list[str] = []
    source_deleted = False
    try:
        if not client.collection_exists(collection):
            raise RAGError(f"Legacy shared Qdrant collection is missing: {collection}")
        source_info = client.get_collection(collection)
        vectors_config = source_info.config.params.vectors
        for product in products:
            target = product_collection_name(product, collection)
            if client.collection_exists(target):
                raise RAGError(f"Target product collection already exists: {target}")
            client.create_collection(
                collection_name=target,
                vectors_config=vectors_config,
            )
            created.append(target)

        expected_counts = Counter()
        offset: Any = None
        while True:
            points, offset = client.scroll(
                collection_name=collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            grouped_points: dict[str, list[models.PointStruct]] = {}
            for point in points:
                payload = point.payload or {}
                product = payload.get("product")
                if not isinstance(product, str) or product not in products:
                    raise RAGError(
                        f"Legacy Qdrant point has invalid product metadata: {point.id}"
                    )
                expected_counts[product] += 1
                grouped_points.setdefault(product, []).append(
                    models.PointStruct(
                        id=point.id,
                        vector=point.vector,
                        payload=payload,
                    )
                )
            for product, product_points in grouped_points.items():
                client.upsert(
                    collection_name=product_collection_name(product, collection),
                    points=product_points,
                    wait=True,
                )
            if offset is None:
                break

        for product, expected in expected_counts.items():
            target = product_collection_name(product, collection)
            actual = client.count(collection_name=target, exact=True).count
            if actual != expected:
                raise RAGError(
                    f"Migrated collection count mismatch for {product}: "
                    f"expected {expected}, got {actual}"
                )
        collections, sections = _collection_manifest(
            client,
            collection,
            source_products,
        )
        source_sections = client.count(collection_name=collection, exact=True).count
        if sections != source_sections:
            raise RAGError(
                f"Migrated section count mismatch: expected {source_sections}, "
                f"got {sections}"
            )
        client.delete_collection(collection)
        source_deleted = True
    except Exception:
        if not source_deleted:
            for target in created:
                if client.collection_exists(target):
                    client.delete_collection(target)
        raise
    finally:
        client.close()

    manifest = {
        "schema_version": 2,
        "collection_prefix": collection,
        "collections": collections,
        "embedding_model": previous.get("embedding_model"),
        "vector_size": previous.get("vector_size"),
        "documents": len(previous["source_sha256"]),
        "sections": sections,
        "source_sha256": previous["source_sha256"],
        "source_products": source_products,
        "migration": {
            "from_collection": collection,
            "products": products,
        },
    }
    _write_index_manifest(qdrant_path, manifest)
    return manifest


def search_index(
    query: str,
    qdrant_path: Path,
    embedder: Embedder,
    collection: str = DEFAULT_COLLECTION,
    limit: int = 6,
    product: str | None = None,
    release: str | None = None,
) -> list[SearchResult]:
    if not query.strip() or limit < 1:
        raise RAGError("Search query and a positive result limit are required")
    if not product:
        raise RAGError("Product is required to select a Qdrant collection")
    product_collection = product_collection_name(product, collection)
    client = QdrantClient(path=str(qdrant_path))
    try:
        if not client.collection_exists(product_collection):
            raise RAGError(f"Qdrant product collection is missing: {product_collection}")
        response = client.query_points(
            collection_name=product_collection,
            query=embedder.encode_queries([query])[0],
            limit=limit,
            with_payload=True,
            query_filter=_metadata_filter(product, release),
        )
    finally:
        client.close()
    results: list[SearchResult] = []
    for point in response.points:
        payload = point.payload or {}
        try:
            results.append(
                SearchResult(
                    citation_id=str(payload["citation_id"]),
                    score=float(point.score),
                    document_id=str(payload["document_id"]),
                    title=str(payload["title"]),
                    version=str(payload["version"]),
                    product=str(payload["product"]),
                    release=str(payload["release"]),
                    heading_path=tuple(payload["heading_path"]),
                    text=str(payload["text"]),
                    source_url=str(payload.get("source_url", "")),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RAGError("Qdrant returned an invalid section payload") from error
    return results


def _result_from_point(point: Any, score: float) -> SearchResult:
    payload = point.payload or {}
    try:
        return SearchResult(
            citation_id=str(payload["citation_id"]),
            score=score,
            document_id=str(payload["document_id"]),
            title=str(payload["title"]),
            version=str(payload["version"]),
            product=str(payload["product"]),
            release=str(payload["release"]),
            heading_path=tuple(payload["heading_path"]),
            text=str(payload["text"]),
            source_url=str(payload.get("source_url", "")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RAGError("Qdrant returned an invalid section payload") from error


def _load_lexical_corpus(
    qdrant_path: Path,
    collection: str = DEFAULT_COLLECTION,
    product: str | None = None,
    release: str | None = None,
) -> _LexicalCorpus:
    if not product:
        raise RAGError("Product is required to select a Qdrant collection")
    product_collection = product_collection_name(product, collection)
    client = QdrantClient(path=str(qdrant_path))
    points: list[Any] = []
    offset = None
    try:
        if not client.collection_exists(product_collection):
            raise RAGError(f"Qdrant product collection is missing: {product_collection}")
        while True:
            batch, offset = client.scroll(
                collection_name=product_collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
                scroll_filter=_metadata_filter(product, release),
            )
            points.extend(batch)
            if offset is None:
                break
    finally:
        client.close()
    documents = tuple(
        tuple(
            SEARCH_TOKEN_PATTERN.findall(
                str((point.payload or {}).get("text", "")).lower()
            )
        )
        for point in points
    )
    document_frequencies = Counter(
        token for tokens in documents for token in set(tokens)
    )
    average_length = sum(map(len, documents)) / max(len(documents), 1)
    return _LexicalCorpus(
        points=tuple(points),
        documents=documents,
        document_frequencies=dict(document_frequencies),
        average_length=average_length,
    )


def _search_lexical_corpus(
    query: str,
    corpus: _LexicalCorpus,
    limit: int,
    require_terms_in_order: bool = False,
) -> list[SearchResult]:
    query_tokens = SEARCH_TOKEN_PATTERN.findall(query.lower())
    if not query_tokens:
        return []
    scored: list[tuple[Any, float]] = []
    for point, tokens in zip(corpus.points, corpus.documents, strict=True):
        if require_terms_in_order and not _tokens_in_order(tokens, query_tokens):
            continue
        frequencies = Counter(tokens)
        score = 0.0
        for token in set(query_tokens):
            frequency = frequencies[token]
            if not frequency:
                continue
            document_frequency = corpus.document_frequencies[token]
            inverse_frequency = math.log(
                1 + (len(corpus.documents) - document_frequency + 0.5)
                / (document_frequency + 0.5)
            )
            denominator = frequency + 1.2 * (
                1 - 0.75
                + 0.75 * len(tokens) / max(corpus.average_length, 1)
            )
            score += inverse_frequency * frequency * 2.2 / denominator
        document_text = " ".join(tokens)
        for size in (2, 3, 4):
            phrases = {
                " ".join(query_tokens[index : index + size])
                for index in range(len(query_tokens) - size + 1)
            }
            score += sum(
                size * 4.0 for phrase in phrases if phrase in document_text
            )
        if (
            2 <= len(query_tokens) <= 6
            and _tokens_in_order(tokens, query_tokens)
        ):
            score += len(query_tokens) * 3.0
        if score:
            scored.append((point, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return [_result_from_point(point, score) for point, score in scored[:limit]]


def search_lexical(
    query: str,
    qdrant_path: Path,
    collection: str = DEFAULT_COLLECTION,
    limit: int = 18,
    product: str | None = None,
    release: str | None = None,
) -> list[SearchResult]:
    corpus = _load_lexical_corpus(
        qdrant_path,
        collection=collection,
        product=product,
        release=release,
    )
    return _search_lexical_corpus(query, corpus, limit)


def search_manuals(
    query: str,
    qdrant_path: Path,
    embedder: Embedder,
    collection: str = DEFAULT_COLLECTION,
    limit: int = 10,
    product: str | None = None,
    release: str | None = None,
    document: str | None = None,
) -> list[SearchResult]:
    """Search all indexed Cisco manuals with hybrid semantic/keyword ranking."""
    if not query.strip() or limit < 1:
        raise RAGError("Search query and a positive result limit are required")
    if document and not document.strip():
        raise RAGError("Document filter must not be empty")
    manifest_path = qdrant_path / "index-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RAGError(f"Could not read index manifest: {manifest_path}") from error
    collections = manifest.get("collections")
    if not isinstance(collections, dict):
        raise RAGError(f"Index manifest has no product collections: {manifest_path}")
    products = [product] if product else sorted(collections)
    if not products:
        raise RAGError("Index manifest contains no searchable products")

    ranked: dict[str, tuple[SearchResult, float]] = {}
    per_product_limit = max(limit * 3, 20)
    for selected_product in products:
        if not isinstance(selected_product, str):
            raise RAGError("Index manifest contains an invalid product identifier")
        for ranking, weight in (
            (
                search_index(
                    query,
                    qdrant_path,
                    embedder,
                    collection=collection,
                    limit=per_product_limit,
                    product=selected_product,
                    release=release,
                ),
                1.0,
            ),
            (
                search_lexical(
                    query,
                    qdrant_path,
                    collection=collection,
                    limit=per_product_limit,
                    product=selected_product,
                    release=release,
                ),
                1.5,
            ),
        ):
            for rank, result in enumerate(ranking, start=1):
                if document and result.document_id != document:
                    continue
                previous = ranked.get(result.citation_id)
                combined_score = (previous[1] if previous else 0.0) + weight / (
                    60 + rank
                )
                ranked[result.citation_id] = (result, combined_score)
    ordered = sorted(ranked.values(), key=lambda item: item[1], reverse=True)
    return [
        SearchResult(
            citation_id=result.citation_id,
            score=score,
            document_id=result.document_id,
            title=result.title,
            version=result.version,
            product=result.product,
            release=result.release,
            heading_path=result.heading_path,
            text=result.text,
            source_url=result.source_url,
        )
        for result, score in ordered[:limit]
    ]


def _chat_json(messages: list[dict[str, str]], config: LLMConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    if config.model:
        payload["model"] = config.model
    credential = config.api_key
    if config.api_key_header.lower() == "authorization":
        credential = "Bearer " + credential
    response = None
    last_error: requests.RequestException | None = None
    for attempt in range(3):
        try:
            response = requests.post(
                config.api_url,
                headers={
                    config.api_key_header: credential,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=config.timeout,
            )
        except requests.RequestException as error:
            last_error = error
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            raise RAGError(f"GPT request failed after 3 attempts: {error}") from error
        if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
            break
        time.sleep(2**attempt)
    if response is None:
        raise RAGError(f"GPT request failed: {last_error}")
    if not response.ok:
        raise RAGError(f"GPT request returned HTTP {response.status_code}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
        result = json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError, requests.JSONDecodeError) as error:
        raise RAGError("GPT request returned invalid JSON") from error
    if not isinstance(result, dict):
        raise RAGError("GPT response must be a JSON object")
    return result


def _metadata_filter(
    product: str | None, release: str | None, document: str | None = None
) -> models.Filter | None:
    conditions = []
    if product:
        conditions.append(
            models.FieldCondition(
                key="product",
                match=models.MatchValue(value=product),
            )
        )
    if release:
        conditions.append(
            models.FieldCondition(
                key="release",
                match=models.MatchValue(value=release),
            )
        )
    if document:
        conditions.append(
            models.FieldCondition(
                key="document_id",
                match=models.MatchValue(value=document),
            )
        )
    return models.Filter(must=conditions) if conditions else None


def _normalize_cli_search_term(term: str) -> str:
    term = " ".join(term.split())
    replacements = (
        (r"^vlan\s+\d+$", "vlan vlan-id"),
        (r"^interface\s+vlan\s*\d+$", "interface vlan vlan-id"),
        (r"^interface\s+gigabitethernet\d+(?:/\d+)+$", "interface interface-id"),
        (r"^switchport\s+access\s+vlan\s+\d+$", "switchport access vlan vlan-id"),
        (r"^ip\s+helper-address\s+(?:\d{1,3}\.){3}\d{1,3}$", "ip helper-address address"),
        (
            r"^ip\s+address\s+(?:\d{1,3}\.){3}\d{1,3}"
            r"(?:\s+(?:\d{1,3}\.){3}\d{1,3})?$",
            "ip address ip-address subnet-mask",
        ),
    )
    for pattern, normalized in replacements:
        if re.fullmatch(pattern, term, re.IGNORECASE):
            return normalized
    return term


def expand_search_plan(
    requirement: str,
    config: LLMConfig,
    product: str = "catalyst9300",
    release: str = "26.x",
) -> tuple[list[str], list[str], list[SearchFeature]]:
    result = _chat_json(
        [
            {
                "role": "system",
                "content": (
                    f"Cisco {product} / IOS XE {release}の英語マニュアル検索クエリを作成します。"
                    "日本語要件を独立して設定・検証できる機能へ分解してください。"
                    "各機能について、機能名、設定手順名、使用が予想されるCisco CLI構文を含む"
                    "英語検索クエリを1～3件作成してください。コンフィグ自体は生成しません。"
                    "JSONでfeatures配列を返し、各要素はqueries配列と、検索すべきCLIコマンド句"
                    "だけを格納したcli_terms配列、要件で明示的に除外された概念を英語の"
                    "短い語句で格納したexcluded_terms配列を持たせてください。featuresは1～4件、"
                    "全cli_termsは24件以内にしてください。値、VRF名、インターフェース名は"
                    "cli_termsへ含めません。複数機能を1つの検索クエリへ混在させないでください。"
                    "削除、解除、無効化の要件では、検索クエリにremoveまたはdisableを含め、"
                    "想定されるno形式のCLI句もcli_termsへ含めてください。"
                ),
            },
            {"role": "user", "content": requirement},
        ],
        config,
    )
    raw_features = result.get("features")
    if (
        not isinstance(raw_features, list)
        or not 1 <= len(raw_features) <= 4
        or not all(isinstance(feature, dict) for feature in raw_features)
    ):
        raise RAGError("Query expansion returned an invalid features array")
    features: list[SearchFeature] = []
    for raw_feature in raw_features:
        queries = raw_feature.get("queries")
        cli_terms = raw_feature.get("cli_terms")
        excluded_terms = raw_feature.get("excluded_terms", [])
        if (
            not isinstance(queries, list)
            or not 1 <= len(queries) <= 3
            or not all(isinstance(query, str) and query.strip() for query in queries)
            or not isinstance(cli_terms, list)
            or not all(isinstance(term, str) and term.strip() for term in cli_terms)
            or not isinstance(excluded_terms, list)
            or not all(
                isinstance(term, str) and term.strip()
                for term in excluded_terms
            )
        ):
            raise RAGError("Query expansion returned an invalid feature")
        normalized_excluded_terms = tuple(
            dict.fromkeys(term.strip().lower() for term in excluded_terms)
        )
        if NO_AUTHENTICATION_PATTERN.search(requirement):
            normalized_excluded_terms = tuple(dict.fromkeys([
                *normalized_excluded_terms,
                "auth",
                "key",
                "trusted-key",
            ]))
        normalized_cli_terms = tuple(
            _normalize_cli_search_term(term)
            for term in cli_terms
            if not any(
                _contains_search_concept(term, excluded)
                for excluded in normalized_excluded_terms
            )
            and not term.strip().lower().startswith(NON_NEGATABLE_CLI_PREFIXES)
        )
        features.append(
            SearchFeature(
                queries=tuple(query.strip() for query in queries),
                cli_terms=normalized_cli_terms,
                excluded_terms=normalized_excluded_terms,
            )
        )
    if VLAN_CREATION_PATTERN.search(requirement):
        target_index = next(
            (
                index for index, feature in enumerate(features)
                if "vlan" in " ".join((*feature.queries, *feature.cli_terms)).lower()
            ),
            0,
        )
        target = features[target_index]
        features[target_index] = SearchFeature(
            queries=tuple(dict.fromkeys((
                *target.queries, "creating or modifying an ethernet vlan vlan vlan-id",
            ))),
            cli_terms=tuple(dict.fromkeys(("vlan vlan-id", *target.cli_terms))),
            excluded_terms=target.excluded_terms,
        )
    if re.search(r"\bSVI\b", requirement, re.IGNORECASE):
        requests_svi_address = bool(re.search(
            r"\bSVI\b.{0,30}(?:IPv4|IP|アドレス).{0,30}"
            r"(?:\d{1,3}\.){3}\d{1,3}",
            requirement, re.IGNORECASE,
        ))
        target_index = next(
            (
                index for index, feature in enumerate(features)
                if re.search(
                    r"\bsvi\b|interface\s+vlan",
                    " ".join((*feature.queries, *feature.cli_terms)),
                    re.IGNORECASE,
                )
            ),
            0,
        )
        target = features[target_index]
        features[target_index] = SearchFeature(
            queries=tuple(dict.fromkeys((
                *target.queries,
                "manually assigning ip information to multiple svis interface vlan vlan-id",
            ))),
            cli_terms=tuple(dict.fromkeys((
                "interface vlan vlan-id",
                *(("ip address ip-address subnet-mask",) if requests_svi_address else ()),
                *(("no shutdown",) if requests_svi_address and "有効化" in requirement else ()),
                *target.cli_terms,
            ))),
            excluded_terms=target.excluded_terms,
        )
    if re.search(r"アクセスポート|\baccess\s+port\b", requirement, re.IGNORECASE):
        target_index = next(
            (
                index for index, feature in enumerate(features)
                if "access" in " ".join((*feature.queries, *feature.cli_terms)).lower()
            ),
            0,
        )
        target = features[target_index]
        features[target_index] = SearchFeature(
            queries=tuple(dict.fromkeys((
                *target.queries, "assigning static-access ports to a vlan",
            ))),
            cli_terms=tuple(dict.fromkeys((
                "interface interface-id", "switchport mode access",
                "switchport access vlan vlan-id", *target.cli_terms,
            ))),
            excluded_terms=target.excluded_terms,
        )
    if VLAN_NAME_INTENT_PATTERN.search(requirement):
        target_index = next(
            (
                index
                for index, feature in enumerate(features)
                if "vlan" in " ".join(
                    (*feature.queries, *feature.cli_terms)
                ).lower()
                and "name" in " ".join(
                    (*feature.queries, *feature.cli_terms)
                ).lower()
            ),
            0,
        )
        target = features[target_index]
        features[target_index] = SearchFeature(
            queries=target.queries,
            cli_terms=tuple(
                dict.fromkeys(("name vlan-name", *target.cli_terms))
            ),
            excluded_terms=target.excluded_terms,
        )
    if (
        DHCP_RELAY_INTENT_PATTERN.search(requirement)
        and not _has_explicit_dhcp_vrf_context(requirement)
    ):
        target_index = next(
            (
                index
                for index, feature in enumerate(features)
                if _is_dhcp_relay_feature(feature)
            ),
            None,
        )
        if target_index is None:
            target_index = len(features)
            features.append(SearchFeature((), ()))
        target = features[target_index]
        excluded_terms = tuple(
            dict.fromkeys(
                (
                    *target.excluded_terms,
                    "EVPN",
                    "VXLAN",
                    "tenant VRF",
                    "ip helper-address global",
                    "ip helper-address vrf",
                )
            )
        )
        features[target_index] = SearchFeature(
            queries=tuple(
                dict.fromkeys(
                    (
                        *target.queries,
                        "DHCP relay agent specifying the packet forwarding "
                        "address ip helper-address address",
                    )
                )
            ),
            cli_terms=tuple(
                term
                for term in dict.fromkeys(
                    (
                        *target.cli_terms,
                        "ip helper-address address",
                        "ip helper-address",
                    )
                )
                if not any(
                    _contains_search_concept(term, excluded)
                    for excluded in excluded_terms
                )
            ),
            excluded_terms=excluded_terms,
        )
    if re.search(r"\bexit\b", requirement, re.IGNORECASE):
        target = features[0]
        features[0] = SearchFeature(
            queries=target.queries,
            cli_terms=tuple(dict.fromkeys((*target.cli_terms, "exit"))),
            excluded_terms=target.excluded_terms,
        )
    all_queries = list(dict.fromkeys(
        [requirement, *(query for feature in features for query in feature.queries)]
    ))
    if NEGATION_INTENT_PATTERN.search(requirement):
        features = [
            SearchFeature(
                queries=feature.queries,
                cli_terms=tuple(dict.fromkeys([
                    *(
                        f"no {term}"
                        for term in feature.cli_terms
                        if not term.lower().startswith(("no ", *NON_NEGATABLE_CLI_PREFIXES))
                    ),
                    *feature.cli_terms,
                ])),
                excluded_terms=feature.excluded_terms,
            )
            for feature in features
        ]
    features, all_cli_terms = _prioritize_cli_terms(features, requirement)
    return all_queries, all_cli_terms, features


def _prioritize_cli_terms(
    features: list[SearchFeature],
    requirement: str,
    max_terms: int = 24,
) -> tuple[list[SearchFeature], list[str]]:
    if max_terms < len(features):
        raise RAGError("CLI term limit must cover every search feature")
    preferred_terms: list[str] = []
    is_negation = bool(NEGATION_INTENT_PATTERN.search(requirement))
    has_target = _has_server_target(requirement)
    if re.search(r"\bntp\b|NTP", requirement, re.IGNORECASE):
        preferred_terms.append("no ntp server" if is_negation else "ntp server")
        if has_target:
            preferred_terms.append("ntp server ip-address")
    if re.search(r"syslog|logging", requirement, re.IGNORECASE):
        preferred_terms.append("logging host")
        if has_target:
            preferred_terms.append("logging host ip-address")
    if (
        DHCP_RELAY_INTENT_PATTERN.search(requirement)
        and not _has_explicit_dhcp_vrf_context(requirement)
    ):
        preferred_terms.extend(
            ("ip helper-address address", "ip helper-address")
        )
    if re.search(r"\bprefer\b|優先", requirement, re.IGNORECASE):
        preferred_terms.append("prefer")
    if VLAN_NAME_INTENT_PATTERN.search(requirement):
        preferred_terms.append("name vlan-name")
    ordered_terms: list[str] = []
    for term in preferred_terms:
        if term not in ordered_terms:
            ordered_terms.append(term)
    for feature in features:
        if feature.cli_terms:
            for term in feature.cli_terms:
                if term not in ordered_terms:
                    ordered_terms.append(term)
                    break
    for feature in features:
        for term in feature.cli_terms:
            if term not in ordered_terms:
                ordered_terms.append(term)
    selected_terms = ordered_terms[:max_terms]
    selected_set = set(selected_terms)
    prioritized_features = [
        SearchFeature(
            queries=feature.queries,
            cli_terms=tuple(
                term for term in feature.cli_terms if term in selected_set
            ),
            excluded_terms=feature.excluded_terms,
        )
        for feature in features
    ]
    return prioritized_features, selected_terms


def _contains_search_phrase(text: str, phrase: str) -> bool:
    text_tokens = SEARCH_TOKEN_PATTERN.findall(text.lower())
    phrase_tokens = SEARCH_TOKEN_PATTERN.findall(phrase.lower())
    if not phrase_tokens:
        return False
    phrase_length = len(phrase_tokens)
    return any(
        text_tokens[index : index + phrase_length] == phrase_tokens
        for index in range(len(text_tokens) - phrase_length + 1)
    )


def _supports_configuration_term(text: str, term: str) -> bool:
    term_tokens = SEARCH_TOKEN_PATTERN.findall(term.lower())
    if not term_tokens:
        return False
    operational_words = {
        prefix.strip() for prefix in NON_NEGATABLE_CLI_PREFIXES
    }
    for line in text.splitlines():
        line_tokens = SEARCH_TOKEN_PATTERN.findall(line.lower())
        term_index = 0
        first_match: int | None = None
        for index, token in enumerate(line_tokens):
            if token != term_tokens[term_index]:
                continue
            if first_match is None:
                first_match = index
            term_index += 1
            if term_index == len(term_tokens):
                break
        if term_index != len(term_tokens) or first_match is None:
            continue
        if not operational_words.intersection(line_tokens[:first_match]):
            return True
    return False


def _tokens_in_order(
    text_tokens: list[str] | tuple[str, ...],
    phrase_tokens: list[str],
    max_gap: int = 16,
) -> bool:
    if not phrase_tokens:
        return False
    for first_match, token in enumerate(text_tokens):
        if token != phrase_tokens[0]:
            continue
        phrase_index = 1
        if phrase_index == len(phrase_tokens):
            return True
        last_possible_match = min(
            len(text_tokens),
            first_match + len(phrase_tokens) + max_gap,
        )
        for index in range(first_match + 1, last_possible_match):
            if text_tokens[index] != phrase_tokens[phrase_index]:
                continue
            phrase_index += 1
            if phrase_index == len(phrase_tokens):
                return True
    return False


def _contains_search_terms_in_order(text: str, phrase: str) -> bool:
    return _tokens_in_order(
        SEARCH_TOKEN_PATTERN.findall(text.lower()),
        SEARCH_TOKEN_PATTERN.findall(phrase.lower()),
    )


def _contains_search_concept(text: str, concept: str) -> bool:
    text_tokens = SEARCH_TOKEN_PATTERN.findall(text.lower())
    concept_tokens = SEARCH_TOKEN_PATTERN.findall(concept.lower())
    if not concept_tokens:
        return False
    if len(concept_tokens) == 1:
        return any(token.startswith(concept_tokens[0]) for token in text_tokens)
    return _contains_search_phrase(text, concept)


def _is_dhcp_relay_feature(feature: SearchFeature) -> bool:
    feature_text = " ".join((*feature.queries, *feature.cli_terms))
    return bool(
        re.search(r"\bDHCP\b|ip\s+helper-address", feature_text, re.IGNORECASE)
    )


def _has_explicit_dhcp_vrf_context(requirement: str) -> bool:
    if DHCP_VRF_CONTEXT_PATTERN.search(requirement):
        return True
    vrf_names = {
        name.lower()
        for name in re.findall(
            r"\bVRF\s+([A-Za-z][A-Za-z0-9_.-]*)\b",
            requirement,
            re.IGNORECASE,
        )
        if name.lower() not in {"name", "unknown", "unspecified"}
    }
    return len(vrf_names) > 1


def _feature_evidence_multiplier(
    feature: SearchFeature,
    result: SearchResult,
) -> float:
    context = " ".join((result.title, *result.heading_path))
    if any(
        _contains_search_concept(context, excluded)
        for excluded in feature.excluded_terms
    ):
        return 0.05
    feature_text = " ".join((*feature.queries, *feature.cli_terms)).lower()
    if "creating or modifying an ethernet vlan" in feature_text:
        context_lower = context.lower()
        if any(term in context_lower for term in ("evpn", "vxlan", "rspan")):
            return 0.1
        if (
            "creating or modifying an ethernet vlan" in context_lower
            or (
                "normal-range vlan" in context_lower
                and (
                    (
                        "create a vlan" in result.text.lower()
                        and _supports_configuration_term(result.text, "vlan vlan-id")
                    )
                    or _supports_configuration_term(result.text, "exit")
                )
            )
        ):
            return 3.0
    if (
        "manually assigning ip information to multiple svis" in feature_text
        and (
            "manually assigning ip information to multiple svis" in context.lower()
            or (
                "example: configuring a device to download configurations from a dhcp server"
                in context.lower()
                and re.search(r"\binterface\s+vlan", result.text, re.IGNORECASE)
            )
        )
    ):
        return 3.0
    if (
        "assigning static-access ports to a vlan" in feature_text
        and "assigning static-access ports to a vlan" in context.lower()
    ):
        return 3.0
    if (
        "evpn" in {term.lower() for term in feature.excluded_terms}
        and "ip helper-address address" in feature.cli_terms
        and (
            "ip addressing services" in result.title.lower()
            or _contains_search_concept(
                " ".join(result.heading_path),
                "specifying the packet forwarding address",
            )
        )
    ):
        return 3.0
    return 1.0


def _detect_requirement_conflicts(requirement: str) -> list[str]:
    has_access = re.search(r"\baccess\b|アクセス(?:ポート)?", requirement, re.I)
    has_trunk = re.search(r"\btrunk\b|トランク(?:ポート)?", requirement, re.I)
    has_simultaneous_intent = re.search(
        r"同時|かつ|同一(?:ポート|インターフェース)|\bboth\b",
        requirement,
        re.I,
    )
    interfaces = {
        re.sub(r"\s+", "", match.group(0)).lower()
        for match in INTERFACE_NAME_PATTERN.finditer(requirement)
    }
    if (
        has_access
        and has_trunk
        and has_simultaneous_intent
        and (
            len(interfaces) <= 1
            or re.search(r"同一(?:ポート|インターフェース)", requirement)
        )
    ):
        target = next(iter(interfaces), "同一インターフェース")
        return [f"{target}へのaccessモードとtrunkモードの同時指定"]
    return []


def _has_server_target(requirement: str) -> bool:
    return bool(IPV4_PATTERN.search(requirement) or HOSTNAME_PATTERN.search(requirement))


def _has_svi_ipv4_address(requirement: str) -> bool:
    address_matches = [
        *IPV4_PREFIX_PATTERN.finditer(requirement),
        *IPV4_ADDRESS_MASK_PATTERN.finditer(requirement),
    ]
    for match in address_matches:
        context = requirement[
            max(0, match.start() - 60) : min(len(requirement), match.end() + 40)
        ]
        if (
            re.search(
                r"\bSVI\b|interface\s+Vlan\s*\d*|\bVLAN\s*\d+",
                context,
                re.IGNORECASE,
            )
            and re.search(r"\bIP\b|address|アドレス", context, re.IGNORECASE)
        ):
            return True
    return False


def _preflight_requirement(
    requirement: str,
    results: list[SearchResult],
) -> tuple[list[str], list[str]]:
    missing_required_values: list[str] = []
    unsupported_requirements: list[str] = []
    requirement_lower = requirement.lower()
    has_target = _has_server_target(requirement)
    if (
        ("syslog" in requirement_lower or "logging" in requirement_lower)
        and not has_target
    ):
        missing_required_values.append(
            "SyslogサーバーのIPアドレスまたはホスト名"
        )
    if (
        NEGATION_INTENT_PATTERN.search(requirement)
        and re.search(r"\bntp\b|NTP", requirement, re.IGNORECASE)
        and not has_target
    ):
        missing_required_values.append(
            "削除対象のNTPサーバーIPアドレスまたはホスト名"
        )
    if (
        DHCP_RELAY_INTENT_PATTERN.search(requirement)
        and VLAN_CREATION_PATTERN.search(requirement)
        and not _has_svi_ipv4_address(requirement)
    ):
        vlan_match = VLAN_ID_PATTERN.search(requirement)
        svi_name = f"Vlan{vlan_match.group(1)}の" if vlan_match else "SVIの"
        missing_required_values.append(
            f"{svi_name}IPv4アドレスとプレフィックス"
        )
    if NO_AUTHENTICATION_PATTERN.search(requirement):
        has_ntp_syntax = any(
            _contains_search_terms_in_order(item.text, "ntp server")
            for item in results
        )
        if not has_ntp_syntax:
            unsupported_requirements.append(
                "認証なしNTPサーバー指定の根拠"
            )
    return missing_required_values, unsupported_requirements


def _unrequested_dhcp_vrf_options(
    requirement: str,
    configuration: str,
) -> list[str]:
    if (
        not DHCP_RELAY_INTENT_PATTERN.search(requirement)
        or _has_explicit_dhcp_vrf_context(requirement)
    ):
        return []
    return [
        line.strip()
        for line in configuration.splitlines()
        if DHCP_VRF_HELPER_PATTERN.match(line)
    ]


def _remove_overconfiguration(
    requirement: str,
    configuration: str,
) -> tuple[str, list[str]]:
    requirement_lower = requirement.lower()
    session_commands = {"enable", "configure terminal", "conf t", "end"}
    minimal_syslog = (
        ("syslog" in requirement_lower or "logging" in requirement_lower)
        and ("最小限" in requirement or re.search(r"\bminimal\b", requirement, re.I))
    )
    no_authentication = bool(NO_AUTHENTICATION_PATTERN.search(requirement))
    allow_prefer = bool(re.search(r"\bprefer\b|優先", requirement, re.I))
    requires_ntp_server = bool(
        re.search(r"NTPサーバー|\bntp server\b", requirement, re.IGNORECASE)
    )
    kept_lines: list[str] = []
    removed_lines: list[str] = []
    for line in configuration.splitlines():
        command = line.strip()
        command_lower = command.lower()
        remove_session_command = (
            command_lower in session_commands
            and command_lower not in requirement_lower
        )
        remove_optional_logging = (
            minimal_syslog
            and (
                not command_lower.startswith("logging host ")
                and (
                    command_lower.startswith("logging ")
                    or command_lower.startswith("service timestamps ")
                )
            )
        )
        remove_ntp_authentication = (
            no_authentication
            and (
                re.match(
                    r"^(?:no\s+)?ntp\s+(?:authenticate|authentication-key|trusted-key)\b",
                    command_lower,
                )
                is not None
                or (
                    command_lower.startswith("ntp server ")
                    and any(
                        re.search(rf"\b{term}\b", command_lower)
                        for term in ("vrf", "key", "source", "version")
                    )
                )
            )
        )
        remove_unrequested_prefer = (
            no_authentication
            and command_lower.startswith("ntp server ")
            and re.search(r"\bprefer\b", command_lower)
            and not allow_prefer
        )
        remove_delete_placeholder = (
            NEGATION_INTENT_PATTERN.search(requirement)
            and command_lower.startswith("no ntp server ")
            and bool(
                re.search(
                    r"\b(?:ip-address|vrf-name|host-name|server-address)\b|<[^>]+>",
                    command_lower,
                )
            )
        )
        if no_authentication and command_lower.startswith("ntp server "):
            tokens = command.split()
            normalized_tokens = tokens[:3]
            if allow_prefer and "prefer" in {
                token.lower() for token in tokens[3:]
            }:
                normalized_tokens.append("prefer")
            normalized_command = " ".join(normalized_tokens)
            if normalized_command != command:
                removed_lines.append(command)
            kept_lines.append(normalized_command)
            continue
        if requires_ntp_server and command_lower.startswith("ntp peer "):
            normalized_command = re.sub(
                r"^ntp peer\b",
                "ntp server",
                command,
                count=1,
                flags=re.IGNORECASE,
            )
            removed_lines.append(command)
            kept_lines.append(normalized_command)
            continue
        if (
            remove_session_command
            or remove_optional_logging
            or remove_ntp_authentication
            or remove_unrequested_prefer
            or remove_delete_placeholder
        ):
            removed_lines.append(command)
        else:
            kept_lines.append(line)
    return "\n".join(kept_lines), removed_lines


def _remove_operational_commands(
    configuration: str,
) -> tuple[str, list[str]]:
    kept_lines: list[str] = []
    removed_lines: list[str] = []
    for line in configuration.splitlines():
        command = line.strip()
        if command.lower().startswith(NON_NEGATABLE_CLI_PREFIXES):
            removed_lines.append(command)
        else:
            kept_lines.append(line)
    return "\n".join(kept_lines), removed_lines


def _normalize_minimal_interface_range_macro(
    requirement: str,
    configuration: str,
) -> tuple[str, list[tuple[tuple[str, str], str]]]:
    if (
        not MINIMAL_CONFIGURATION_PATTERN.search(requirement)
        or re.search(r"マクロ|再利用|\bmacro\b|\breuse\b", requirement, re.I)
    ):
        return configuration, []
    lines = configuration.splitlines()
    definitions: dict[str, tuple[int, str, str]] = {}
    macro_uses: dict[str, tuple[int, str]] = {}
    for index, line in enumerate(lines):
        command = line.strip()
        definition = INTERFACE_RANGE_DEFINITION_PATTERN.fullmatch(command)
        if definition:
            definitions[definition.group("name").lower()] = (
                index,
                command,
                definition.group("range"),
            )
        macro_use = INTERFACE_RANGE_MACRO_PATTERN.fullmatch(command)
        if macro_use:
            macro_uses[macro_use.group("name").lower()] = (index, command)
    replacements: list[tuple[tuple[str, str], str]] = []
    removed_indexes: set[int] = set()
    for name in sorted(definitions.keys() & macro_uses.keys()):
        definition_index, definition_command, interface_range = definitions[name]
        macro_index, macro_command = macro_uses[name]
        normalized_command = f"interface range {interface_range}"
        lines[definition_index] = normalized_command
        removed_indexes.add(macro_index)
        replacements.append(
            (
                (definition_command, macro_command),
                normalized_command,
            )
        )
    return (
        "\n".join(
            line for index, line in enumerate(lines) if index not in removed_indexes
        ),
        replacements,
    )


def _claims_ungrounded_commands(warnings: list[str]) -> bool:
    return any(UNGROUNDED_WARNING_PATTERN.search(warning) for warning in warnings)


def _select_feature_balanced(
    ranked: dict[str, tuple[SearchResult, float]],
    feature_rankings: list[dict[str, tuple[SearchResult, float]]],
    limit: int,
) -> list[tuple[SearchResult, float]]:
    ordered_features = [
        sorted(feature.values(), key=lambda item: item[1], reverse=True)
        for feature in feature_rankings
    ]
    selected: list[tuple[SearchResult, float]] = []
    selected_ids: set[str] = set()
    feature_quota = min(2, max(1, limit // max(len(ordered_features), 1)))
    for rank in range(feature_quota):
        for feature in ordered_features:
            for candidate in feature[rank:]:
                result, _ = candidate
                if result.citation_id in selected_ids:
                    continue
                selected.append(ranked[result.citation_id])
                selected_ids.add(result.citation_id)
                break
            if len(selected) == limit:
                return selected
    ordered = sorted(ranked.values(), key=lambda item: item[1], reverse=True)
    selected.extend(
        candidate
        for candidate in ordered
        if candidate[0].citation_id not in selected_ids
    )
    return selected[:limit]


def search_expanded(
    queries: list[str],
    qdrant_path: Path,
    embedder: Embedder,
    collection: str = DEFAULT_COLLECTION,
    limit: int = 6,
    cli_terms: list[str] | None = None,
    features: list[SearchFeature] | None = None,
    product: str | None = None,
    release: str | None = None,
) -> list[SearchResult]:
    ranked: dict[str, tuple[SearchResult, float]] = {}
    feature_rankings: list[dict[str, tuple[SearchResult, float]]] = [
        {} for _ in features or []
    ]
    per_query_limit = max(limit * 3, 12)
    lexical_corpus = _load_lexical_corpus(
        qdrant_path,
        collection=collection,
        product=product,
        release=release,
    )
    for query in queries:
        query_features = [
            feature for feature in features or [] if query in feature.queries
        ]
        rankings = (
            (search_index(
                query,
                qdrant_path,
                embedder,
                collection=collection,
                limit=per_query_limit,
                product=product,
                release=release,
            ), 1.0),
            (_search_lexical_corpus(
                query,
                lexical_corpus,
                limit=per_query_limit,
            ), 3.0),
        )
        for ranking, weight in rankings:
            for rank, result in enumerate(ranking, start=1):
                previous = ranked.get(result.citation_id)
                rank_score = weight / (60 + rank)
                context_multiplier = max(
                    (
                        _feature_evidence_multiplier(feature, result)
                        for feature in query_features
                    ),
                    default=1.0,
                )
                score = (
                    (previous[1] if previous else 0.0)
                    + rank_score * context_multiplier
                )
                ranked[result.citation_id] = (result, score)
                for feature, feature_ranked in zip(
                    features or [], feature_rankings, strict=True
                ):
                    if query not in feature.queries:
                        continue
                    feature_previous = feature_ranked.get(result.citation_id)
                    feature_rank_score = rank_score * _feature_evidence_multiplier(
                        feature,
                        result,
                    )
                    feature_score = (
                        (feature_previous[1] if feature_previous else 0.0)
                        + feature_rank_score
                    )
                    feature_ranked[result.citation_id] = (result, feature_score)
    for term in cli_terms or []:
        term_features = [
            feature for feature in features or [] if term in feature.cli_terms
        ]
        term_results = _search_lexical_corpus(
            term,
            lexical_corpus,
            limit=len(lexical_corpus.points),
            require_terms_in_order=True,
        )
        term_results.sort(
            key=lambda result: (
                _supports_configuration_term(result.text, term),
                max(
                    (
                        _feature_evidence_multiplier(feature, result)
                        for feature in term_features
                    ),
                    default=1.0,
                ),
                result.score,
            ),
            reverse=True,
        )
        term_results = term_results[:per_query_limit]
        for rank, result in enumerate(term_results, start=1):
            previous = ranked.get(result.citation_id)
            rank_score = 1.0 / rank
            context_multiplier = max(
                (
                    _feature_evidence_multiplier(feature, result)
                    for feature in term_features
                ),
                default=1.0,
            )
            score = (
                (previous[1] if previous else 0.0)
                + rank_score * context_multiplier
            )
            ranked[result.citation_id] = (result, score)
            for feature, feature_ranked in zip(
                features or [], feature_rankings, strict=True
            ):
                if term not in feature.cli_terms:
                    continue
                feature_previous = feature_ranked.get(result.citation_id)
                feature_rank_score = (
                    rank_score
                    * _feature_evidence_multiplier(feature, result)
                )
                feature_score = (
                    (feature_previous[1] if feature_previous else 0.0)
                    + feature_rank_score
                )
                feature_ranked[result.citation_id] = (result, feature_score)
    ordered = (
        _select_feature_balanced(ranked, feature_rankings, limit)
        if feature_rankings
        else sorted(ranked.values(), key=lambda item: item[1], reverse=True)[:limit]
    )
    return [
        SearchResult(
            citation_id=result.citation_id,
            score=score,
            document_id=result.document_id,
            title=result.title,
            version=result.version,
            product=result.product,
            release=result.release,
            heading_path=result.heading_path,
            text=result.text,
            source_url=result.source_url,
        )
        for result, score in ordered
    ]


def generate_configuration(
    requirement: str,
    results: list[SearchResult],
    config: LLMConfig,
    knowledge_policy: str = DEFAULT_KNOWLEDGE_POLICY,
    product: str | None = None,
    release: str | None = None,
) -> dict[str, Any]:
    if not results:
        raise RAGError("Configuration generation requires retrieved evidence")
    if knowledge_policy not in KNOWLEDGE_POLICIES:
        raise RAGError(f"Unknown knowledge policy: {knowledge_policy}")
    products = {item.product for item in results}
    releases = {item.release for item in results}
    target_product = product or (next(iter(products)) if len(products) == 1 else None)
    target_release = release or (next(iter(releases)) if len(releases) == 1 else None)
    if not target_product or products != {target_product}:
        raise RAGError("Retrieved evidence contains a different product")
    if target_release and releases != {target_release}:
        raise RAGError("Retrieved evidence contains a different release")
    preflight_missing, preflight_unsupported = _preflight_requirement(
        requirement,
        results,
    )
    is_example_request = bool(EXAMPLE_REQUEST_PATTERN.search(requirement))
    policy_instructions = {
        "disallow": (
            "LLMの事前知識によるCLI補完は禁止です。根拠で構文を確認できないCLIは生成せず、"
            "warningsへ不足を記載してください。inferred_commandsは必ず空配列にしてください。"
        ),
        "limited": (
            "根拠に対象機能と必要な操作が明記されている場合に限り、根拠で不足する一般的な"
            "IOS XE CLI構文を事前知識で補完できます。根拠にない機能、要件にない設定、"
            "破壊的な変更は追加しないでください。補完したCLI行をinferred_commandsへ"
            "configurationと完全に同じ文字列で列挙してください。"
        ),
        "allow": (
            "要件を満たすためにIOS XEの事前知識を使用できます。根拠から直接導出できない"
            "すべてのCLI行をinferred_commandsへconfigurationと完全に同じ文字列で列挙し、"
            "理由とリスクをwarningsへ記載してください。"
        ),
    }
    evidence_references = {
        f"E{index}": item.citation_id
        for index, item in enumerate(results, start=1)
    }
    if (preflight_missing or preflight_unsupported) and not is_example_request:
        reasons = [*preflight_missing, *preflight_unsupported]
        return {
            "requirement": requirement,
            "knowledge_policy": knowledge_policy,
            "configuration": "",
            "explanation": (
                "要件の前処理で、設定生成に必要な値または根拠が不足しているため、"
                "設定を生成しませんでした。"
            ),
            "citations": [item.citation_id for item in results[:1]],
            "warnings": [
                "生成前の要件検証により部分設定を抑止しました: "
                + ", ".join(reasons)
            ],
            "inferred_commands": [],
            "unsupported_requirements": preflight_unsupported,
            "missing_required_values": preflight_missing,
            "retrieval": [
                {
                    "citation_id": item.citation_id,
                    "score": item.score,
                    "document_id": item.document_id,
                    "product": item.product,
                    "release": item.release,
                    "heading_path": list(item.heading_path),
                    "source_url": item.source_url,
                }
                for item in results
            ],
        }
    evidence = "\n\n".join(
        f"[E{index}] {item.title} / {' > '.join(item.heading_path)}\n{item.text}"
        for index, item in enumerate(results, start=1)
    )
    messages = [
        {
            "role": "system",
            "content": (
                f"あなたはCisco {target_product}、Cisco IOS XE {target_release or '指定リリース'}"
                "のコンフィグ生成者です。"
                "提供された根拠だけを使用し、根拠にないコマンドを推測しないでください。"
                "根拠の構文で[ no ] commandと記載されている場合、no commandはその設定を"
                "削除または無効化する根拠として使用できます。削除・解除の要件では肯定形の"
                "commandを生成せず、根拠で確認できるno形式を使用してください。"
                "要件にないVRF名、認証キー、インターフェース、例示値を根拠の例から"
                "コピーしないでください。必要な値が要件にない場合は、その設定を省略して"
                "warningsへ不足を記載してください。"
                "不足情報があればwarningsへ明記してください。JSONオブジェクトだけを返し、"
                "configurationはCLIのみ、explanationは日本語、citationsは使用した根拠の"
                "E1、E2形式の識別子を正確にコピーした配列、"
                "warnings、inferred_commands、unsupported_requirements、"
                "missing_required_valuesは文字列配列に"
                "してください。主要機能を実現するCLIの根拠が不足する場合は、その要件を"
                "unsupported_requirementsへ記載し、configurationを完全に空にしてください。"
                "要求された操作に不可欠なIPアドレス、インターフェース、識別子などが"
                "要件にない場合はmissing_required_valuesへ記載し、任意パラメータは含めず、"
                "configurationを完全に空にしてください。"
                "インターフェース選択など、単独では要件を実現しない部分設定も生成しないでください。"
                "configurationにはグローバル設定モード、インターフェース設定モードなどで"
                "投入する設定コマンドだけを含めてください。show、clear、debug、ping、"
                "tracerouteなどの運用・確認コマンドはconfigurationへ含めず、必要なら"
                "explanationまたはwarningsへ記載してください。"
                "要件に「最短」「最小限」「できるだけ短い」とある一回限りの"
                "インターフェース範囲設定では、define interface-rangeによるマクロ定義より"
                "直接のinterface rangeを優先してください。"
                "同一インターフェースをaccessモードとtrunkモードへ同時指定するなど、"
                "相互に矛盾する要件はunsupported_requirementsへ記載し、"
                "正常な別要件を含めてconfigurationを完全に空にしてください。"
                "要件が設定例、コンフィグ例、configuration exampleの提示だけを求める場合、"
                "実投入用のconfigurationは空にしてください。根拠に実際の設定例があれば、"
                "そのCLIを改変せずexplanationへ「参考設定例（そのまま投入不可）」として"
                "記載し、対応する根拠をcitationsへ含めてください。根拠に設定例がなければ"
                "例を推測せず、その旨をexplanationとwarningsへ記載してください。"
                f"知識利用ポリシーは{knowledge_policy}です。{policy_instructions[knowledge_policy]}"
                "設定モードへ入るconfigure terminalや"
                "保存コマンドは、要件または根拠に必要性が示されない限り追加しないでください。"
            ),
        },
        {
            "role": "user",
            "content": f"要件:\n{requirement}\n\n根拠:\n{evidence}",
        },
    ]
    result = _chat_json(messages, config)
    configuration = result.get("configuration")
    explanation = result.get("explanation")
    citations = result.get("citations")
    warnings = result.get("warnings")
    inferred_commands = result.get("inferred_commands")
    unsupported_requirements = result.get("unsupported_requirements")
    missing_required_values = result.get("missing_required_values")
    if isinstance(configuration, list) and all(
        isinstance(line, str) for line in configuration
    ):
        configuration = "\n".join(configuration)
    if isinstance(explanation, list) and all(
        isinstance(paragraph, str) for paragraph in explanation
    ):
        explanation = "\n\n".join(explanation)
    if (
        not isinstance(configuration, str)
        or not isinstance(explanation, str)
        or not isinstance(citations, list)
        or not all(isinstance(item, str) for item in citations)
        or not isinstance(warnings, list)
        or not all(isinstance(item, str) for item in warnings)
        or not isinstance(inferred_commands, list)
        or not all(isinstance(item, str) for item in inferred_commands)
        or not isinstance(unsupported_requirements, list)
        or not all(isinstance(item, str) for item in unsupported_requirements)
        or not isinstance(missing_required_values, list)
        or not all(isinstance(item, str) for item in missing_required_values)
    ):
        raise RAGError(
            "Configuration generation response has an invalid structure: "
            f"configuration={type(configuration).__name__}, "
            f"explanation={type(explanation).__name__}, "
            f"citations={type(citations).__name__}"
            f"[{','.join(type(item).__name__ for item in citations) if isinstance(citations, list) else ''}], "
            f"warnings={type(warnings).__name__}"
            f"[{','.join(type(item).__name__ for item in warnings) if isinstance(warnings, list) else ''}], "
            f"inferred_commands={type(inferred_commands).__name__}"
            f"[{','.join(type(item).__name__ for item in inferred_commands) if isinstance(inferred_commands, list) else ''}], "
            f"unsupported_requirements={type(unsupported_requirements).__name__}"
            f"[{','.join(type(item).__name__ for item in unsupported_requirements) if isinstance(unsupported_requirements, list) else ''}], "
            f"missing_required_values={type(missing_required_values).__name__}"
            f"[{','.join(type(item).__name__ for item in missing_required_values) if isinstance(missing_required_values, list) else ''}]"
        )
    if (
        not configuration.strip()
        and not warnings
        and not is_example_request
    ):
        raise RAGError(
            "Configuration generation returned no configuration and no warning"
        )
    if "```" in configuration:
        raise RAGError("Generated configuration must not contain Markdown fences")
    allowed = set(evidence_references.values())
    normalized_citations = [
        evidence_references.get(citation, citation)
        for citation in citations
    ]
    if not normalized_citations or not set(normalized_citations).issubset(allowed):
        raise RAGError("Generated citations are empty or not in retrieved evidence")
    for reference, citation_id in evidence_references.items():
        explanation = explanation.replace(f"[{reference}]", f"[{citation_id}]")
    warnings = [
        re.sub(
            r"(?<![A-Za-z0-9_:])E\d+(?![A-Za-z0-9_])",
            lambda match: evidence_references.get(match.group(), match.group()),
            warning,
        )
        for warning in warnings
    ]
    configuration_lines = {
        line.strip() for line in configuration.splitlines() if line.strip()
    }
    if any(command.strip() not in configuration_lines for command in inferred_commands):
        raise RAGError("Inferred commands must exactly match generated configuration lines")
    if knowledge_policy == "disallow" and inferred_commands:
        raise RAGError("Generated configuration uses disallowed prior knowledge")
    if is_example_request:
        reference_example = configuration.strip()
        if reference_example and reference_example not in explanation:
            explanation = (
                explanation.rstrip()
                + "\n\n参考設定例（そのまま投入不可）:\n"
                + reference_example
            )
        configuration = ""
        inferred_commands = []
        example_warning = (
            "設定例の提示要求のため、参考例は説明に記載し、"
            "実投入用コンフィグは生成していません。"
        )
        if example_warning not in warnings:
            warnings.append(example_warning)
    if (
        NO_AUTHENTICATION_PATTERN.search(requirement)
        and not configuration.strip()
        and not missing_required_values
    ):
        ntp_targets = IPV4_PATTERN.findall(requirement)
        if ntp_targets and any(
            _contains_search_terms_in_order(item.text, "ntp server")
            for item in results
        ):
            preferred_target = next(
                (
                    target
                    for target in ntp_targets
                    if re.search(
                        rf"{re.escape(target)}[^\n。]*\bprefer\b",
                        requirement,
                        re.IGNORECASE,
                    )
                ),
                None,
            )
            configuration = "\n".join(
                f"ntp server {target}"
                + (" prefer" if target == preferred_target else "")
                for target in ntp_targets
            )
            unsupported_requirements = []
            warnings.append(
                "認証なしNTPの明示IPと根拠が確認できたため、最小CLIを生成しました。"
            )
    configuration, operational_commands = _remove_operational_commands(
        configuration
    )
    if operational_commands:
        operational_set = set(operational_commands)
        inferred_commands = [
            command
            for command in inferred_commands
            if command.strip() not in operational_set
        ]
        warnings.append(
            "設定コンフィグから運用・確認コマンドを除外しました: "
            + ", ".join(operational_commands)
        )
    configuration, macro_replacements = (
        _normalize_minimal_interface_range_macro(
            requirement,
            configuration,
        )
    )
    for replaced_commands, normalized_command in macro_replacements:
        replaced_set = set(replaced_commands)
        used_inference = any(
            command.strip() in replaced_set
            for command in inferred_commands
        )
        inferred_commands = [
            command
            for command in inferred_commands
            if command.strip() not in replaced_set
        ]
        if used_inference and normalized_command not in inferred_commands:
            inferred_commands.append(normalized_command)
        warnings.append(
            "一回限りの最短設定としてinterface rangeへ正規化しました: "
            + ", ".join(replaced_commands)
        )
    configuration, removed_commands = _remove_overconfiguration(
        requirement,
        configuration,
    )
    if removed_commands:
        removed_set = set(removed_commands)
        inferred_commands = [
            command
            for command in inferred_commands
            if command.strip() not in removed_set
        ]
        warnings.append(
            "要件にない操作・任意設定を除外しました: "
            + ", ".join(removed_commands)
        )
    unrequested_dhcp_vrf_options = _unrequested_dhcp_vrf_options(
        requirement,
        configuration,
    )
    if unrequested_dhcp_vrf_options:
        unsupported_reason = "DHCPリレーのVRF専用キーワードが要件にありません"
        if unsupported_reason not in unsupported_requirements:
            unsupported_requirements.append(unsupported_reason)
        warnings.append(
            "VRF間の経路要件がないため、DHCPリレーのVRF専用構文を拒否しました: "
            + ", ".join(unrequested_dhcp_vrf_options)
        )
    for conflict in _detect_requirement_conflicts(requirement):
        if conflict not in unsupported_requirements:
            unsupported_requirements.append(conflict)
    if configuration.strip() and _claims_ungrounded_commands(warnings):
        if knowledge_policy == "disallow":
            unsupported_reason = "生成CLIの根拠不足"
        elif not inferred_commands:
            unsupported_reason = "根拠外CLIの申告不整合"
        else:
            unsupported_reason = ""
        if (
            unsupported_reason
            and unsupported_reason not in unsupported_requirements
        ):
            unsupported_requirements.append(unsupported_reason)
    if unsupported_requirements or missing_required_values:
        discarded_commands = [
            line.strip() for line in configuration.splitlines() if line.strip()
        ]
        configuration = ""
        inferred_commands = []
        if unsupported_requirements:
            warnings.append(
                "主要要件を安全に実現できないため、部分設定を破棄しました: "
                + ", ".join(unsupported_requirements)
            )
        if missing_required_values:
            warnings.append(
                "主要機能に必要な値が不足しているため、部分設定を破棄しました: "
                + ", ".join(missing_required_values)
            )
        if discarded_commands:
            warnings.append(
                "破棄した部分設定: " + ", ".join(discarded_commands)
            )
    return {
        "requirement": requirement,
        "knowledge_policy": knowledge_policy,
        "configuration": (
            configuration.strip() + "\n" if configuration.strip() else ""
        ),
        "explanation": explanation.strip(),
        "citations": normalized_citations,
        "warnings": warnings,
        "inferred_commands": inferred_commands,
        "unsupported_requirements": unsupported_requirements,
        "missing_required_values": missing_required_values,
        "retrieval": [
            {
                "citation_id": item.citation_id,
                "score": item.score,
                "document_id": item.document_id,
                "product": item.product,
                "release": item.release,
                "heading_path": list(item.heading_path),
                "source_url": item.source_url,
            }
            for item in results
        ],
    }


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", r"\|").replace("\n", "<br>")


def format_markdown_report(
    result: dict[str, Any],
    report_path: Path | None = None,
    packages_dir: Path = Path("data/products/catalyst9300/packages"),
) -> str:
    warnings = result.get("warnings", [])
    inferred_commands = result.get("inferred_commands", [])
    missing_required_values = result.get("missing_required_values", [])
    unsupported_requirements = result.get("unsupported_requirements", [])
    citations = result.get("citations", [])
    retrieval = result.get("retrieval", [])
    configuration = str(result.get("configuration", "")).rstrip()
    retrieval_by_citation = {
        item.get("citation_id"): item
        for item in retrieval
        if isinstance(item, dict)
    }

    def source_link(document_id: str) -> str | None:
        if report_path is None or not document_id:
            return None
        source = (packages_dir / document_id / "manual.md").resolve()
        relative = os.path.relpath(source, report_path.parent.resolve())
        return Path(relative).as_posix()

    lines = [
        "# Cisco RAG生成レポート",
        "",
        "## 質問",
        "",
        str(result.get("requirement", "")),
        "",
        "## 生成コンフィグ",
        "",
        "```text",
        configuration,
        "```",
        "",
        "## 説明",
        "",
        str(result.get("explanation", "")),
        "",
        "## 注意事項",
        "",
    ]
    lines.extend(
        [f"- {warning}" for warning in warnings] if warnings else ["なし"]
    )
    component_results = result.get("component_results")
    if isinstance(component_results, list) and component_results:
        lines.extend([
            "", "## 要件ごとの結果", "",
            "候補は個別確認用であり、全体検証に失敗した場合は投入できません。", "",
            "| 要件 | 状態 | 注意事項 |",
            "|---|---|---|",
        ])
        for part in component_results:
            lines.append(
                f"| {_markdown_cell(part['name'])} "
                f"| {'確認候補' if part['status'] == 'verified' else '未確認'} "
                f"| {_markdown_cell('; '.join(part['warnings']) or 'なし')} |"
            )
        if any(part["status"] != "verified" for part in component_results):
            for part in component_results:
                candidate = part.get("candidate_configuration", "").strip()
                if candidate:
                    lines.extend([
                        "", f"### {_markdown_cell(part['name'])}の個別候補（投入不可）", "",
                        "```text", candidate, "```",
                    ])
    for heading, items in (
        ("不足値", missing_required_values),
        ("未対応要件", unsupported_requirements),
    ):
        lines.extend(["", f"## {heading}", ""])
        lines.extend([f"- {item}" for item in items] if items else ["なし"])
    lines.extend(
        [
            "",
            "## 参照根拠",
            "",
        ]
    )
    if citations:
        for citation in citations:
            item = retrieval_by_citation.get(citation, {})
            document_id = str(item.get("document_id", citation.split(":", 1)[0]))
            link = source_link(document_id)
            lines.append(
                f"- [`{citation}`]({link})" if link else f"- `{citation}`"
            )
    else:
        lines.append("なし")
    lines.extend(
        [
            "",
            "## 根拠外の推測",
            "",
            f"- 知識利用ポリシー: `{result.get('knowledge_policy', '')}`",
            f"- 推測コマンド数: {len(inferred_commands)}",
        ]
    )
    if inferred_commands:
        lines.extend(["", "```text", *inferred_commands, "```"])
    citation_checks = result.get("citation_checks")
    if isinstance(citation_checks, list) and citation_checks:
        lines.extend([
            "", "## CLIごとの引用確認", "",
            "| CLI | 確認結果 | 一致した引用 | 確認できなかった引用 |",
            "|---|---|---|---|",
        ])
        for check in citation_checks:
            lines.append(
                f"| `{_markdown_cell(check['command'])}` "
                f"| {'確認候補' if check['verified'] else '未確認'} "
                f"| {_markdown_cell(', '.join(check['citations']) or 'なし')} "
                f"| {_markdown_cell(', '.join(check['rejected_citations']) or 'なし')} |"
            )
    if retrieval:
        lines.extend(
            [
                "",
                "## 検索結果",
                "",
                "| 順位 | スコア | 文書 | 見出し | 引用ID |",
                "|---:|---:|---|---|---|",
            ]
        )
        for rank, item in enumerate(retrieval, start=1):
            heading = " > ".join(item.get("heading_path", []))
            document_id = str(item.get("document_id", ""))
            link = source_link(document_id)
            document = (
                f"[{_markdown_cell(document_id)}]({link})"
                if link
                else _markdown_cell(document_id)
            )
            lines.append(
                f"| {rank} | {float(item.get('score', 0)):.4f} "
                f"| {document} "
                f"| {_markdown_cell(heading)} "
                f"| `{_markdown_cell(item.get('citation_id', ''))}` |"
            )
    return "\n".join(lines).rstrip() + "\n"


def search_result_dict(result: SearchResult) -> dict[str, Any]:
    return {
        "score": result.score,
        "citation_id": result.citation_id,
        "document_id": result.document_id,
        "title": result.title,
        "version": result.version,
        "product": result.product,
        "release": result.release,
        "heading_path": list(result.heading_path),
        "text": result.text,
        "source_url": result.source_url,
    }


def format_search_results(results: list[SearchResult]) -> str:
    lines: list[str] = []
    for rank, result in enumerate(results, start=1):
        lines.extend(
            [
                f"[{rank}] score={result.score:.4f}",
                f"文書: {result.title} ({result.document_id})",
                f"製品: {result.product} / IOS XE: {result.release}",
                f"章・節: {' > '.join(result.heading_path) or '(指定なし)'}",
                f"出典: {result.source_url or '(URLなし)'}",
                "本文:",
                result.text,
                "",
            ]
        )
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or query the Cisco RAG index.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    index = subparsers.add_parser("index")
    index.add_argument(
        "--packages-dir",
        type=Path,
        default=Path("data/products/catalyst9300/packages"),
    )
    index.add_argument("--qdrant-path", type=Path, default=Path("data/qdrant"))
    index.add_argument("--model", default=DEFAULT_MODEL)
    index.add_argument(
        "--incremental",
        action="store_true",
        help="Update only package documents changed since the saved index manifest.",
    )
    migrate = subparsers.add_parser(
        "migrate-collections",
        help="Split the legacy shared collection into product collections.",
    )
    migrate.add_argument("--qdrant-path", type=Path, default=Path("data/qdrant"))
    search = subparsers.add_parser(
        "search",
        help="Search indexed Cisco manuals without generating an answer.",
    )
    search.add_argument("query")
    search.add_argument("--qdrant-path", type=Path, default=Path("data/qdrant"))
    search.add_argument("--model", default=DEFAULT_MODEL)
    search.add_argument("--limit", type=int, default=10)
    search.add_argument(
        "--product",
        help="Exact catalog product identifier. Omit to search all indexed products.",
    )
    search.add_argument(
        "--release",
        help="Exact catalog release identifier, for example 26.x.",
    )
    search.add_argument(
        "--document",
        help="Exact manual document identifier.",
    )
    search.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    search.add_argument("--output", type=Path, help="Write results to a file.")
    query = subparsers.add_parser("generate")
    query.add_argument("requirement")
    query.add_argument(
        "--packages-dir",
        type=Path,
        default=Path("data/products/catalyst9300/packages"),
    )
    query.add_argument("--qdrant-path", type=Path, default=Path("data/qdrant"))
    query.add_argument("--model", default=DEFAULT_MODEL)
    query.add_argument("--top-k", type=int, default=6)
    query.add_argument(
        "--product",
        required=True,
        help="Exact catalog product identifier, for example catalyst9300.",
    )
    query.add_argument(
        "--release",
        help="Exact catalog release identifier, for example 26.x.",
    )
    query.add_argument(
        "--knowledge-policy",
        choices=KNOWLEDGE_POLICIES,
        default=DEFAULT_KNOWLEDGE_POLICY,
        help="Control use of LLM prior knowledge (default: disallow)",
    )
    query.add_argument(
        "--output",
        type=Path,
        help="Write JSON by default, or a Markdown report when the path ends in .md.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "migrate-collections":
            result = migrate_product_collections(args.qdrant_path)
        elif args.command in {"index", "search"}:
            embedder = E5Embedder(args.model)
        if args.command == "index":
            result = (
                update_index_incrementally(
                    args.packages_dir, args.qdrant_path, embedder
                )
                if args.incremental
                else build_index(args.packages_dir, args.qdrant_path, embedder)
            )
        elif args.command == "search":
            retrieved = search_manuals(
                args.query,
                args.qdrant_path,
                embedder,
                limit=args.limit,
                product=args.product,
                release=args.release,
                document=args.document,
            )
            result = [search_result_dict(item) for item in retrieved]
            output = (
                json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                if args.format == "json"
                else format_search_results(retrieved)
            )
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(output, encoding="utf-8")
        elif args.command == "generate":
            service = ConfigurationGenerationService(
                qdrant_path=args.qdrant_path,
                embedder=E5Embedder(args.model),
                llm_config=load_config(),
                search_planner=expand_search_plan,
                evidence_searcher=search_expanded,
                configuration_generator=generate_configuration,
            )
            outcome = service.generate(
                ConfigurationGenerationRequest(
                    requirement=args.requirement,
                    product=args.product,
                    release=args.release,
                    knowledge_policy=args.knowledge_policy,
                    top_k=args.top_k,
                )
            )
            result = outcome.result
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                output = (
                    format_markdown_report(result, args.output, args.packages_dir)
                    if args.output.suffix.lower() in {".md", ".markdown"}
                    else json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                )
                args.output.write_text(output, encoding="utf-8")
    except (RAGError, ConfigurationGenerationError) as error:
        print(f"RAG failed: {error}", file=sys.stderr)
        return 1
    if args.command == "search":
        if args.output:
            print(args.output.read_text(encoding="utf-8"), end="")
        elif args.format == "text":
            print(format_search_results(retrieved), end="")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    elif (
        args.command == "generate"
        and args.output
        and args.output.suffix.lower() in {".md", ".markdown"}
    ):
        print(
            format_markdown_report(result, args.output, args.packages_dir),
            end="",
        )
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
