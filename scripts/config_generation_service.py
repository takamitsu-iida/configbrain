"""Application service for grounded configuration generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

if __package__:
    from .transform_markdown import LLMConfig
else:
    from transform_markdown import LLMConfig


DEFAULT_KNOWLEDGE_POLICY = "disallow"
KNOWLEDGE_POLICIES = ("disallow", "limited", "allow")


class ConfigurationGenerationError(RuntimeError):
    """Raised when a configuration generation request is invalid."""


class Embedder(Protocol):
    dimension: int

    def encode_passages(self, texts: list[str]) -> list[list[float]]: ...

    def encode_queries(self, texts: list[str]) -> list[list[float]]: ...


class SearchResult(Protocol):
    citation_id: str
    title: str
    heading_path: tuple[str, ...]
    text: str


class SearchPlanner(Protocol):
    def __call__(
        self,
        requirement: str,
        config: LLMConfig,
        product: str,
        release: str,
    ) -> tuple[list[str], list[str], list[Any]]: ...


class EvidenceSearcher(Protocol):
    def __call__(
        self,
        queries: list[str],
        qdrant_path: Path,
        embedder: Embedder,
        *,
        limit: int,
        cli_terms: list[str],
        features: list[Any],
        product: str,
        release: str | None,
    ) -> list[SearchResult]: ...


class ConfigurationGenerator(Protocol):
    def __call__(
        self,
        requirement: str,
        results: list[SearchResult],
        config: LLMConfig,
        knowledge_policy: str,
        product: str,
        release: str | None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ConfigurationGenerationRequest:
    requirement: str
    product: str
    release: str | None = None
    knowledge_policy: str = DEFAULT_KNOWLEDGE_POLICY
    top_k: int = 6


@dataclass(frozen=True)
class ConfigurationGenerationOutcome:
    result: dict[str, Any]
    evidence: tuple[SearchResult, ...]


class ConfigurationGenerationService:
    def __init__(
        self,
        qdrant_path: Path,
        embedder: Embedder,
        llm_config: LLMConfig,
        search_planner: SearchPlanner,
        evidence_searcher: EvidenceSearcher,
        configuration_generator: ConfigurationGenerator,
    ) -> None:
        self._qdrant_path = qdrant_path
        self._embedder = embedder
        self._llm_config = llm_config
        self._search_planner = search_planner
        self._evidence_searcher = evidence_searcher
        self._configuration_generator = configuration_generator

    def generate(
        self,
        request: ConfigurationGenerationRequest,
    ) -> ConfigurationGenerationOutcome:
        _validate_request(request)
        queries, cli_terms, features = self._search_planner(
            request.requirement,
            self._llm_config,
            request.product,
            request.release or "target release",
        )
        retrieved = self._evidence_searcher(
            queries,
            self._qdrant_path,
            self._embedder,
            limit=request.top_k,
            cli_terms=cli_terms,
            features=features,
            product=request.product,
            release=request.release,
        )
        result = self._configuration_generator(
            request.requirement,
            retrieved,
            self._llm_config,
            knowledge_policy=request.knowledge_policy,
            product=request.product,
            release=request.release,
        )
        result["search_queries"] = queries
        result["cli_terms"] = cli_terms
        return ConfigurationGenerationOutcome(
            result=result,
            evidence=tuple(retrieved),
        )


def _validate_request(request: ConfigurationGenerationRequest) -> None:
    if not request.requirement.strip():
        raise ConfigurationGenerationError(
            "Configuration requirement must not be empty"
        )
    if not request.product.strip():
        raise ConfigurationGenerationError("Configuration product must not be empty")
    if request.release is not None and not request.release.strip():
        raise ConfigurationGenerationError("Configuration release must not be empty")
    if request.knowledge_policy not in KNOWLEDGE_POLICIES:
        raise ConfigurationGenerationError(
            f"Unknown knowledge policy: {request.knowledge_policy}"
        )
    if request.top_k < 1:
        raise ConfigurationGenerationError(
            "Configuration retrieval limit must be at least 1"
        )
