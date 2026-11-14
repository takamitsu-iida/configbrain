from pathlib import Path
from typing import Any

import pytest

from scripts.config_generation_service import (
    ConfigurationGenerationError,
    ConfigurationGenerationRequest,
    ConfigurationGenerationService,
)
from scripts.rag import SearchFeature, SearchResult
from scripts.transform_markdown import LLMConfig


class FakeEmbedder:
    dimension = 2

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def encode_queries(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def test_service_runs_search_and_generation_as_one_use_case() -> None:
    llm_config = LLMConfig(
        api_url="https://example.invalid",
        api_key="test",
        model="test",
        api_key_header="Authorization",
    )
    evidence = SearchResult(
        citation_id="guide:section-0001",
        score=1.0,
        document_id="guide",
        title="Guide",
        version="26",
        product="catalyst9300",
        release="26.x",
        heading_path=("NTP",),
        text="Use the ntp server ip-address command.",
    )
    calls: dict[str, Any] = {}

    def plan_search(
        requirement: str,
        config: LLMConfig,
        product: str,
        release: str,
    ) -> tuple[list[str], list[str], list[Any]]:
        calls["plan"] = (requirement, config, product, release)
        return (
            ["NTP server configuration"],
            ["ntp server"],
            [SearchFeature(("NTP server configuration",), ("ntp server",))],
        )

    def search_evidence(
        queries: list[str],
        qdrant_path: Path,
        embedder: FakeEmbedder,
        **kwargs: Any,
    ) -> list[SearchResult]:
        calls["search"] = (queries, qdrant_path, embedder, kwargs)
        return [evidence]

    def generate(
        requirement: str,
        results: list[SearchResult],
        config: LLMConfig,
        **kwargs: Any,
    ) -> dict[str, Any]:
        calls["generate"] = (requirement, results, config, kwargs)
        return {
            "requirement": requirement,
            "configuration": "ntp server 192.0.2.1\n",
        }

    embedder = FakeEmbedder()
    service = ConfigurationGenerationService(
        qdrant_path=Path("data/qdrant"),
        embedder=embedder,
        llm_config=llm_config,
        search_planner=plan_search,
        evidence_searcher=search_evidence,
        configuration_generator=generate,
    )

    outcome = service.generate(
        ConfigurationGenerationRequest(
            requirement="NTPサーバー192.0.2.1を設定",
            product="catalyst9300",
            release="26.x",
            knowledge_policy="limited",
            top_k=8,
        )
    )

    assert calls["plan"] == (
        "NTPサーバー192.0.2.1を設定",
        llm_config,
        "catalyst9300",
        "26.x",
    )
    assert calls["search"] == (
        ["NTP server configuration"],
        Path("data/qdrant"),
        embedder,
        {
            "limit": 8,
            "cli_terms": ["ntp server"],
            "features": [
                SearchFeature(("NTP server configuration",), ("ntp server",))
            ],
            "product": "catalyst9300",
            "release": "26.x",
        },
    )
    assert calls["generate"] == (
        "NTPサーバー192.0.2.1を設定",
        [evidence],
        llm_config,
        {
            "knowledge_policy": "limited",
            "product": "catalyst9300",
            "release": "26.x",
        },
    )
    assert outcome.evidence == (evidence,)
    assert outcome.result["search_queries"] == ["NTP server configuration"]
    assert outcome.result["cli_terms"] == ["ntp server"]


@pytest.mark.parametrize(
    ("generation_request", "message"),
    [
        (
            ConfigurationGenerationRequest("", "catalyst9300"),
            "requirement must not be empty",
        ),
        (
            ConfigurationGenerationRequest("NTPを設定", ""),
            "product must not be empty",
        ),
        (
            ConfigurationGenerationRequest("NTPを設定", "catalyst9300", release=""),
            "release must not be empty",
        ),
        (
            ConfigurationGenerationRequest(
                "NTPを設定",
                "catalyst9300",
                knowledge_policy="unknown",
            ),
            "Unknown knowledge policy",
        ),
        (
            ConfigurationGenerationRequest(
                "NTPを設定",
                "catalyst9300",
                top_k=0,
            ),
            "retrieval limit must be at least 1",
        ),
    ],
)
def test_service_rejects_invalid_requests(
    generation_request: ConfigurationGenerationRequest,
    message: str,
) -> None:
    service = ConfigurationGenerationService(
        qdrant_path=Path("data/qdrant"),
        embedder=FakeEmbedder(),
        llm_config=LLMConfig(
            api_url="https://example.invalid",
            api_key="test",
            model="test",
            api_key_header="Authorization",
        ),
        search_planner=lambda *args: ([], [], []),
        evidence_searcher=lambda *args, **kwargs: [],
        configuration_generator=lambda *args, **kwargs: {},
    )

    with pytest.raises(ConfigurationGenerationError, match=message):
        service.generate(generation_request)
