from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from qdrant_client import QdrantClient, models

from app.retrieval.indexer import EmbeddingProvider


@dataclass(frozen=True)
class SearchResult:
    score: float
    text: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "text": self.text,
            "metadata": self.metadata,
        }


class QdrantSearcher:
    def __init__(
        self,
        client: QdrantClient,
        collection_name: str,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self.client = client
        self.collection_name = collection_name
        self.embedding_provider = embedding_provider

    def search(
        self,
        question: str,
        *,
        limit: int = 5,
        product: str | None = None,
        os_version: str | None = None,
    ) -> list[SearchResult]:
        if not question.strip():
            raise ValueError("question must not be empty")
        if limit < 1:
            raise ValueError("limit must be positive")

        vector = self.embedding_provider.embed([question])[0]
        query_filter = _metadata_filter(product=product, os_version=os_version)
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=query_filter,
            limit=max(limit * 4, 20),
            with_payload=True,
        )
        results = [
            SearchResult(
                score=point.score,
                text=str(point.payload.get("text", "")),
                metadata={key: value for key, value in point.payload.items() if key != "text"},
            )
            for point in response.points
        ]
        return _rerank(question, results)[:limit]


def _metadata_filter(
    *,
    product: str | None,
    os_version: str | None,
) -> models.Filter | None:
    conditions = [
        models.FieldCondition(key="product", match=models.MatchValue(value=product))
        for product in (product,)
        if product
    ]
    if os_version:
        conditions.append(
            models.FieldCondition(key="os_version", match=models.MatchValue(value=os_version))
        )
    return models.Filter(must=conditions) if conditions else None


def search_result_payload(results: list[SearchResult]) -> list[Mapping[str, Any]]:
    return [result.to_dict() for result in results]


def _rerank(question: str, results: list[SearchResult]) -> list[SearchResult]:
    normalized_question = _normalize_for_matching(question)
    question_terms = set(re.findall(r"[a-z0-9]+", normalized_question))
    wants_pvlan = "pvlan" in question_terms or "private vlan" in normalized_question
    wants_svi = "interface vlan" in normalized_question or "svi" in question_terms
    wants_vlan_creation = any(
        marker in normalized_question for marker in ("vlan", "vlanを", "vlanの")
    ) and not wants_svi

    ranked: list[tuple[float, SearchResult]] = []
    for result in results:
        searchable = _normalize_for_matching(
            f"{result.text} {result.metadata.get('section_title', '')}"
        )
        adjusted_score = result.score
        if question_terms:
            overlap = question_terms.intersection(set(re.findall(r"[a-z0-9]+", searchable)))
            adjusted_score += min(len(overlap), 3) * 0.01
        if wants_vlan_creation and not wants_pvlan and "pvlan" in searchable:
            adjusted_score -= 0.25
        if wants_vlan_creation and not wants_svi and "interface vlan" in searchable:
            adjusted_score -= 0.2
        if wants_pvlan and "pvlan" not in searchable:
            adjusted_score -= 0.1
        if wants_svi and "interface vlan" not in searchable and "svi" not in searchable:
            adjusted_score -= 0.1
        ranked.append((adjusted_score, result))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [result for _, result in ranked]


def _normalize_for_matching(text: str) -> str:
    return " ".join(text.casefold().replace("-", " ").split())