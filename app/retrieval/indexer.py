from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from collections import Counter
from typing import Protocol, Sequence
from uuid import UUID, uuid5

from qdrant_client import QdrantClient, models

from app.ingestion.chunker import Chunk


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbeddingProvider:
    def __init__(self, api_key: str, model: str = "text-embedding-3-large") -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(input=list(texts), model=self.model)
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]


@dataclass(frozen=True)
class DocumentMetadata:
    vendor: str = "Cisco"
    product: str = "catalyst9300"
    os: str = "IOS XE"
    os_version: str = "26.x"
    document_title: str = ""
    document_version: str = "26.x"
    source_url: str = ""


class QdrantIndexer:
    def __init__(
        self,
        client: QdrantClient,
        collection_name: str,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self.client = client
        self.collection_name = collection_name
        self.embedding_provider = embedding_provider

    def index_chunks(
        self,
        chunks: Sequence[Chunk],
        metadata: DocumentMetadata,
        *,
        batch_size: int = 64,
    ) -> int:
        if not chunks:
            return 0
        if batch_size < 1:
            raise ValueError("batch_size must be positive")

        point_ids = _chunk_ids(chunks)
        first_vector = self.embedding_provider.embed([chunks[0].text])[0]
        self._ensure_collection(len(first_vector))
        indexed = 0
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = [first_vector] if start == 0 else self.embedding_provider.embed(
                [chunk.text for chunk in batch]
            )
            if start == 0 and len(batch) > 1:
                vectors.extend(self.embedding_provider.embed([chunk.text for chunk in batch[1:]]))
            if len(vectors) != len(batch):
                raise ValueError("embedding provider returned an unexpected vector count")
            points = [
                models.PointStruct(
                    id=point_ids[start + offset],
                    vector=vector,
                    payload=_payload(chunk, metadata),
                )
                for offset, (chunk, vector) in enumerate(zip(batch, vectors, strict=True))
            ]
            self.client.upsert(collection_name=self.collection_name, points=points, wait=True)
            indexed += len(points)
        return indexed

    def _ensure_collection(self, vector_size: int) -> None:
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
            )


def _chunk_ids(chunks: Sequence[Chunk]) -> list[str]:
    occurrences: Counter[tuple[str, int | None, str, str, str, str]] = Counter()
    point_ids: list[str] = []
    for chunk in chunks:
        digest = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        key = (
            chunk.document_id,
            chunk.page_number,
            chunk.section_title or "",
            chunk.content_type,
            digest,
            chunk.section_url or "",
        )
        occurrence = occurrences[key]
        occurrences[key] += 1
        point_ids.append(_chunk_id(chunk, digest, occurrence))
    return point_ids


def _chunk_id(chunk: Chunk, digest: str, occurrence: int) -> str:
    return str(
        uuid5(
            UUID("7f5c6a91-2eb1-4ebc-a4c4-6a47d4f6c2b5"),
            f"{chunk.document_id}:{chunk.page_number}:{chunk.section_title or ''}:{chunk.content_type}:{chunk.section_url or ''}:{occurrence}:{digest}",
        )
    )


def _payload(chunk: Chunk, metadata: DocumentMetadata) -> dict[str, str | int | None]:
    return {
        "document_id": chunk.document_id,
        "vendor": metadata.vendor,
        "product": metadata.product,
        "os": metadata.os,
        "os_version": metadata.os_version,
        "document_title": metadata.document_title,
        "document_version": metadata.document_version,
        "section_title": chunk.section_title or "",
        "page_number": chunk.page_number,
        "section_url": chunk.section_url or "",
        "source_url": metadata.source_url,
        "content_type": chunk.content_type,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "text": chunk.text,
    }