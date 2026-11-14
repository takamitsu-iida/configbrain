import asyncio
from contextlib import asynccontextmanager
import csv
import json
import logging
from pathlib import Path
from threading import Event
import time
from typing import Any, AsyncIterator
import uuid

import httpx
import pytest

from scripts.api import (
    create_app as create_api_app,
    load_configuration_targets,
)
from scripts.config_generation_service import ConfigurationGenerationService
from scripts.rag import RAGError, SearchResult
from scripts.transform_markdown import LLMConfig


TEST_CONFIGURATION_TARGETS = {
    "catalyst9200": ("26.x",),
    "catalyst9300": ("26.x",),
    "catalyst9500": ("26.x",),
}


def create_app(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault(
        "configuration_targets",
        TEST_CONFIGURATION_TARGETS,
    )
    job_database_path = kwargs.get("job_database_path")
    if isinstance(job_database_path, Path):
        kwargs.setdefault(
            "pilot_feedback_path",
            job_database_path.with_name("feedback.csv"),
        )
    return create_api_app(*args, **kwargs)


class FakeEmbedder:
    dimension = 2

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def encode_queries(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def successful_generation(
    requirement: str,
    results: list[SearchResult],
    config: LLMConfig,
    **kwargs: Any,
) -> dict[str, Any]:
    evidence = results[0]
    return {
        "requirement": requirement,
        "knowledge_policy": kwargs["knowledge_policy"],
        "configuration": "ntp server 192.0.2.1\n",
        "explanation": "公式マニュアルの構文を使用しました。",
        "citations": ["guide:section-0001"],
        "warnings": [],
        "inferred_commands": [],
        "unsupported_requirements": [],
        "missing_required_values": [],
        "retrieval": [
            {
                "citation_id": evidence.citation_id,
                "score": evidence.score,
                "document_id": evidence.document_id,
                "product": evidence.product,
                "release": evidence.release,
                "heading_path": list(evidence.heading_path),
            }
        ],
    }


def make_service(
    configuration_generator: Any | None = None,
) -> ConfigurationGenerationService:
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

    return ConfigurationGenerationService(
        qdrant_path=Path("data/qdrant"),
        embedder=FakeEmbedder(),
        llm_config=LLMConfig(
            api_url="https://example.invalid",
            api_key="test",
            model="test",
            api_key_header="Authorization",
        ),
        search_planner=lambda *args: (
            ["NTP server configuration"],
            ["ntp server"],
            [],
        ),
        evidence_searcher=lambda *args, **kwargs: [evidence],
        configuration_generator=configuration_generator or successful_generation,
    )


@asynccontextmanager
async def api_client(
    app: Any,
    api_key: str | None = "test-key",
) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        headers = {"X-API-Key": api_key} if api_key is not None else {}
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=headers,
        ) as client:
            yield client


async def wait_for_terminal_job(
    client: httpx.AsyncClient,
    job_id: str,
) -> dict[str, Any]:
    for _ in range(100):
        response = await client.get(f"/v1/config-generations/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"completed", "failed"}:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError(f"Job did not reach a terminal state: {job_id}")


def test_post_returns_before_generation_completes_and_get_returns_result(
    tmp_path: Path,
) -> None:
    release_generation = Event()

    def delayed_generation(
        requirement: str,
        results: list[SearchResult],
        config: LLMConfig,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not release_generation.wait(timeout=5):
            raise AssertionError("Generation was not released")
        return successful_generation(
            requirement,
            results,
            config,
            **kwargs,
        )

    app = create_app(
        make_service(delayed_generation),
        job_database_path=tmp_path / "jobs.sqlite3",
        api_key="test-key",
    )

    async def scenario() -> None:
        async with api_client(app) as client:
            response = await client.post(
                "/v1/config-generations",
                json={
                    "requirement": "  NTPサーバー192.0.2.1を設定  ",
                    "product": " catalyst9300 ",
                    "release": " 26.x ",
                    "knowledge_policy": "disallow",
                    "top_k": 8,
                },
                headers={"X-Request-ID": "request-success-001"},
            )
            assert response.status_code == 202
            assert response.headers["X-Request-ID"] == "request-success-001"
            accepted = response.json()
            assert accepted["request_id"] == "request-success-001"
            assert accepted["status"] == "queued"
            assert accepted["status_url"].endswith(
                f"/v1/config-generations/{accepted['job_id']}"
            )

            release_generation.set()
            job = await wait_for_terminal_job(client, accepted["job_id"])

        assert job["status"] == "completed"
        assert job["request_id"] == "request-success-001"
        assert job["request"] == {
            "requirement": "NTPサーバー192.0.2.1を設定",
            "product": "catalyst9300",
            "release": "26.x",
            "knowledge_policy": "disallow",
            "top_k": 8,
        }
        assert job["result"]["configuration"] == "ntp server 192.0.2.1\n"
        assert job["result"]["search_queries"] == ["NTP server configuration"]
        assert job["error"] is None
        assert job["error_code"] is None
        assert job["duration_ms"] is not None
        assert job["started_at"] is not None
        assert job["completed_at"] is not None

    asyncio.run(scenario())


def test_idempotency_key_reuses_job_and_rejects_different_request(
    tmp_path: Path,
) -> None:
    app = create_app(
        make_service(),
        job_database_path=tmp_path / "jobs.sqlite3",
        api_key="test-key",
    )

    async def scenario() -> None:
        headers = {"Idempotency-Key": "request-123"}
        payload = {
            "requirement": "NTPサーバー192.0.2.1を設定",
            "product": "catalyst9300",
        }
        async with api_client(app) as client:
            first = await client.post(
                "/v1/config-generations",
                json=payload,
                headers=headers,
            )
            second = await client.post(
                "/v1/config-generations",
                json=payload,
                headers=headers,
            )
            conflict = await client.post(
                "/v1/config-generations",
                json={
                    **payload,
                    "requirement": "別のNTPサーバーを設定",
                },
                headers=headers,
            )

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]
        assert conflict.status_code == 409
        assert "different request" in conflict.json()["detail"]

    asyncio.run(scenario())


def test_failed_generation_is_persisted_as_failed_job(tmp_path: Path) -> None:
    def fail_generation(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RAGError("Generation backend is unavailable")

    app = create_app(
        make_service(fail_generation),
        job_database_path=tmp_path / "jobs.sqlite3",
        api_key="test-key",
    )

    async def scenario() -> None:
        async with api_client(app) as client:
            response = await client.post(
                "/v1/config-generations",
                json={
                    "requirement": "NTPサーバー192.0.2.1を設定",
                    "product": "catalyst9300",
                },
            )
            assert response.status_code == 202
            job = await wait_for_terminal_job(
                client,
                response.json()["job_id"],
            )

        assert job["status"] == "failed"
        assert job["result"] is None
        assert job["error_code"] == "generation_failed"
        assert job["error"] == "Configuration generation failed"
        assert job["duration_ms"] is not None

    asyncio.run(scenario())


def test_completed_job_remains_available_after_app_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "jobs.sqlite3"
    first_app = create_app(
        make_service(),
        job_database_path=database_path,
        api_key="test-key",
    )

    async def create_job() -> str:
        async with api_client(first_app) as client:
            response = await client.post(
                "/v1/config-generations",
                json={
                    "requirement": "NTPサーバー192.0.2.1を設定",
                    "product": "catalyst9300",
                },
            )
            job_id = response.json()["job_id"]
            job = await wait_for_terminal_job(client, job_id)
            assert job["status"] == "completed"
            return job_id

    job_id = asyncio.run(create_job())
    restarted_app = create_app(
        make_service(),
        job_database_path=database_path,
        api_key="test-key",
    )

    async def read_job() -> None:
        async with api_client(restarted_app) as client:
            response = await client.get(
                f"/v1/config-generations/{job_id}"
            )
        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        assert response.json()["result"]["citations"] == [
            "guide:section-0001"
        ]

    asyncio.run(read_job())


def test_api_rejects_invalid_input_and_reports_missing_job(
    tmp_path: Path,
) -> None:
    app = create_app(
        make_service(),
        job_database_path=tmp_path / "jobs.sqlite3",
        api_key="test-key",
    )

    async def scenario() -> None:
        async with api_client(app) as client:
            invalid = await client.post(
                "/v1/config-generations",
                json={
                    "requirement": " ",
                    "product": "catalyst9300",
                    "unexpected": True,
                },
            )
            missing = await client.get(
                f"/v1/config-generations/{uuid.uuid4()}"
            )
            unsupported_product = await client.post(
                "/v1/config-generations",
                json={
                    "requirement": "NTPを設定",
                    "product": "catalyst9999",
                    "release": "26.x",
                },
            )
            unsupported_release = await client.post(
                "/v1/config-generations",
                json={
                    "requirement": "NTPを設定",
                    "product": "catalyst9300",
                    "release": "17.9",
                },
            )

        assert invalid.status_code == 422
        errors = invalid.json()["detail"]
        assert {error["loc"][-1] for error in errors} == {
            "requirement",
            "unexpected",
        }
        assert missing.status_code == 404
        assert missing.json() == {"detail": "Configuration job not found"}
        assert unsupported_product.status_code == 400
        assert unsupported_product.json() == {
            "detail": "Unsupported product: catalyst9999"
        }
        assert unsupported_release.status_code == 400
        assert unsupported_release.json() == {
            "detail": "Unsupported release for catalyst9300: 17.9"
        }

    asyncio.run(scenario())


def test_api_requires_valid_api_key(tmp_path: Path) -> None:
    app = create_app(
        make_service(),
        job_database_path=tmp_path / "jobs.sqlite3",
        api_key="test-key",
    )

    async def scenario() -> None:
        payload = {
            "requirement": "NTPを設定",
            "product": "catalyst9300",
        }
        async with api_client(app, api_key=None) as client:
            missing = await client.post(
                "/v1/config-generations",
                json=payload,
            )
            invalid = await client.post(
                "/v1/config-generations",
                json=payload,
                headers={"X-API-Key": "wrong-key"},
            )
            valid = await client.post(
                "/v1/config-generations",
                json=payload,
                headers={"X-API-Key": "test-key"},
            )

        assert missing.status_code == 401
        assert invalid.status_code == 401
        assert valid.status_code == 202

    asyncio.run(scenario())


def test_web_ui_and_assets_are_available_without_exposing_api_key(
    tmp_path: Path,
) -> None:
    app = create_app(
        make_service(),
        job_database_path=tmp_path / "jobs.sqlite3",
        api_key="SUPER_SECRET_API_KEY",
    )

    async def scenario() -> None:
        async with api_client(app, api_key=None) as client:
            page = await client.get("/")
            script = await client.get("/ui/assets/app.js")
            stylesheet = await client.get("/ui/assets/app.css")
            targets = await client.get("/v1/configuration-targets")
            templates_without_key = await client.get(
                "/v1/requirement-templates"
            )
            templates = await client.get(
                "/v1/requirement-templates",
                headers={"X-API-Key": "SUPER_SECRET_API_KEY"},
            )

        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert page.headers["cache-control"] == "no-store"
        assert page.headers["content-security-policy"].startswith(
            "default-src 'self'"
        )
        assert page.headers["referrer-policy"] == "no-referrer"
        assert page.headers["x-content-type-options"] == "nosniff"
        assert "根拠付きConfig作成" in page.text
        assert "/ui/assets/app.js?v=20261114-3" in page.text
        assert "/ui/assets/app.css?v=20261114-3" in page.text
        assert "SUPER_SECRET_API_KEY" not in page.text

        assert script.status_code == 200
        assert script.headers["cache-control"] == "no-store"
        assert script.headers["content-type"].split(";")[0] in {
            "application/javascript",
            "text/javascript",
        }
        assert '"/v1/config-generations"' in script.text
        assert "localStorage" not in script.text
        assert "sessionStorage" not in script.text
        assert "SUPER_SECRET_API_KEY" not in script.text

        assert stylesheet.status_code == 200
        assert stylesheet.headers["cache-control"] == "no-store"
        assert stylesheet.headers["content-type"].startswith("text/css")
        assert 'button[aria-busy="true"]' in stylesheet.text
        assert "cursor: not-allowed" in stylesheet.text
        assert 'setAttribute("aria-busy", "true")' in script.text
        assert 'removeAttribute("aria-busy")' in script.text
        assert "Cisco公式ドキュメントを開く" in script.text
        assert 'sourceLink.rel = "noopener noreferrer"' in script.text
        assert targets.status_code == 200
        assert targets.json() == {
            "targets": [
                {
                    "product": "catalyst9200",
                    "releases": ["26.x"],
                },
                {
                    "product": "catalyst9300",
                    "releases": ["26.x"],
                },
                {
                    "product": "catalyst9500",
                    "releases": ["26.x"],
                },
            ]
        }
        assert templates_without_key.status_code == 401
        assert templates.status_code == 200
        assert len(templates.json()["templates"]) >= 10
        assert templates.json()["templates"][0] == {
            "label": "DHCPリレー",
            "requirement": (
                "Vlan100のDHCP要求を192.0.2.50へ転送してください"
            ),
        }

    asyncio.run(scenario())


def test_completed_job_feedback_is_saved_and_duplicate_is_rejected(
    tmp_path: Path,
) -> None:
    feedback_path = tmp_path / "pilot" / "feedback.csv"
    app = create_app(
        make_service(),
        job_database_path=tmp_path / "jobs.sqlite3",
        pilot_feedback_path=feedback_path,
        api_key="test-key",
    )
    feedback_id = str(uuid.uuid4())
    payload = {
        "feedback_id": feedback_id,
        "reviewer": "reviewer-01",
        "requirement_anonymized": True,
        "verdict": "minor_changes",
        "expected_configuration": "ntp server 192.0.2.1\n",
        "corrected_configuration": "",
        "citations_useful": "yes",
        "requirement_classification_appropriate": "yes",
        "dangerous_or_destructive_error": False,
        "issue_categories": ["generation", "retrieval"],
        "comments": "匿名化済みコメント",
    }

    async def scenario() -> None:
        async with api_client(app) as client:
            accepted = await client.post(
                "/v1/config-generations",
                json={
                    "requirement": "NTPを設定",
                    "product": "catalyst9300",
                    "release": "26.x",
                },
            )
            job = await wait_for_terminal_job(
                client,
                accepted.json()["job_id"],
            )
            missing_category = await client.post(
                f"/v1/config-generations/{job['job_id']}/feedback",
                json={
                    **payload,
                    "feedback_id": str(uuid.uuid4()),
                    "verdict": "unusable",
                    "issue_categories": [],
                },
            )
            dangerous_without_comment = await client.post(
                f"/v1/config-generations/{job['job_id']}/feedback",
                json={
                    **payload,
                    "feedback_id": str(uuid.uuid4()),
                    "dangerous_or_destructive_error": True,
                    "comments": "",
                },
            )
            saved = await client.post(
                f"/v1/config-generations/{job['job_id']}/feedback",
                json=payload,
            )
            duplicate = await client.post(
                f"/v1/config-generations/{job['job_id']}/feedback",
                json=payload,
            )
            updated = await client.put(
                f"/v1/config-generations/{job['job_id']}/feedback/"
                f"{feedback_id}",
                json={
                    **payload,
                    "verdict": "ready",
                    "issue_categories": [],
                    "comments": "訂正済みコメント",
                },
            )

        assert missing_category.status_code == 422
        assert dangerous_without_comment.status_code == 422
        assert saved.status_code == 201
        assert saved.json()["feedback_id"] == feedback_id
        assert saved.json()["job_id"] == job["job_id"]
        assert duplicate.status_code == 409
        assert "already exists" in duplicate.json()["detail"]
        assert updated.status_code == 200
        assert updated.json()["feedback_id"] == feedback_id

    asyncio.run(scenario())

    with feedback_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["feedback_id"] == feedback_id
    assert rows[0]["reviewer"] == "reviewer-01"
    assert rows[0]["requirement_anonymized"] == "yes"
    assert rows[0]["dangerous_or_destructive_error"] == "no"
    assert rows[0]["verdict"] == "ready"
    assert rows[0]["issue_categories"] == ""
    assert rows[0]["comments"] == "訂正済みコメント"


def test_requirement_templates_reload_after_file_replacement(
    tmp_path: Path,
) -> None:
    template_path = tmp_path / "requirements.tsv"
    template_path.write_text(
        "最初の要件\tNTPを設定してください\n",
        encoding="utf-8",
    )
    app = create_app(
        make_service(),
        job_database_path=tmp_path / "jobs.sqlite3",
        requirement_templates_path=template_path,
        api_key="test-key",
    )

    async def scenario() -> None:
        async with api_client(app) as client:
            first = await client.get("/v1/requirement-templates")
            template_path.write_text(
                "差し替え後\tSyslogを設定してください\n",
                encoding="utf-8",
            )
            second = await client.get("/v1/requirement-templates")

        assert first.status_code == 200
        assert first.json()["templates"] == [
            {
                "label": "最初の要件",
                "requirement": "NTPを設定してください",
            }
        ]
        assert second.status_code == 200
        assert second.json()["templates"] == [
            {
                "label": "差し替え後",
                "requirement": "Syslogを設定してください",
            }
        ]

    asyncio.run(scenario())


def test_configuration_targets_include_only_indexed_catalog_documents(
    tmp_path: Path,
) -> None:
    qdrant_path = tmp_path / "qdrant"
    catalog_directory = tmp_path / "catalog"
    qdrant_path.mkdir()
    catalog_directory.mkdir()
    (qdrant_path / "index-manifest.json").write_text(
        json.dumps(
            {
                "collections": {
                    "catalyst9200": {},
                    "catalyst9300": {},
                },
                "source_sha256": {
                    "indexed-9200": "sha256",
                    "indexed-9300": "sha256",
                },
            }
        ),
        encoding="utf-8",
    )
    (catalog_directory / "c9200-26.json").write_text(
        json.dumps(
            {
                "product": "catalyst9200",
                "release": "26.x",
                "documents": [{"document_id": "indexed-9200"}],
            }
        ),
        encoding="utf-8",
    )
    (catalog_directory / "c9200-17.json").write_text(
        json.dumps(
            {
                "product": "catalyst9200",
                "release": "17.9",
                "documents": [{"document_id": "not-indexed"}],
            }
        ),
        encoding="utf-8",
    )
    (catalog_directory / "c9300-26.json").write_text(
        json.dumps(
            {
                "product": "catalyst9300",
                "release": "26.x",
                "documents": [{"document_id": "indexed-9300"}],
            }
        ),
        encoding="utf-8",
    )

    assert load_configuration_targets(
        qdrant_path,
        catalog_directory,
    ) == {
        "catalyst9200": ("26.x",),
        "catalyst9300": ("26.x",),
    }


def test_api_rejects_oversized_request_body(tmp_path: Path) -> None:
    app = create_app(
        make_service(),
        job_database_path=tmp_path / "jobs.sqlite3",
        api_key="test-key",
        max_request_body_bytes=64,
    )

    async def scenario() -> None:
        async with api_client(app) as client:
            response = await client.post(
                "/v1/config-generations",
                json={
                    "requirement": "x" * 100,
                    "product": "catalyst9300",
                },
                headers={"Content-Length": "10"},
            )

        assert response.status_code == 413
        assert response.json() == {"detail": "Request body is too large"}
        assert response.headers["X-Request-ID"]

    asyncio.run(scenario())


def test_app_startup_requires_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONFIGBRAIN_API_KEY", raising=False)
    app = create_app(
        make_service(),
        job_database_path=tmp_path / "jobs.sqlite3",
        env_file=tmp_path / "missing.env",
    )

    async def scenario() -> None:
        with pytest.raises(
            RuntimeError,
            match="CONFIGBRAIN_API_KEY must be configured",
        ):
            async with app.router.lifespan_context(app):
                pass

    asyncio.run(scenario())


def test_api_rejects_job_when_capacity_is_reached(tmp_path: Path) -> None:
    release_generation = Event()

    def delayed_generation(
        requirement: str,
        results: list[SearchResult],
        config: LLMConfig,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not release_generation.wait(timeout=5):
            raise AssertionError("Generation was not released")
        return successful_generation(requirement, results, config, **kwargs)

    app = create_app(
        make_service(delayed_generation),
        job_database_path=tmp_path / "jobs.sqlite3",
        api_key="test-key",
        max_unfinished_jobs=1,
    )

    async def scenario() -> None:
        async with api_client(app) as client:
            first = await client.post(
                "/v1/config-generations",
                json={
                    "requirement": "NTPサーバー1を設定",
                    "product": "catalyst9300",
                },
            )
            second = await client.post(
                "/v1/config-generations",
                json={
                    "requirement": "NTPサーバー2を設定",
                    "product": "catalyst9300",
                },
            )
            assert first.status_code == 202
            assert second.status_code == 429
            assert second.headers["Retry-After"] == "5"
            release_generation.set()
            await wait_for_terminal_job(client, first.json()["job_id"])

    asyncio.run(scenario())


def test_job_timeout_is_reported_without_running_next_job_concurrently(
    tmp_path: Path,
) -> None:
    def slow_generation(
        requirement: str,
        results: list[SearchResult],
        config: LLMConfig,
        **kwargs: Any,
    ) -> dict[str, Any]:
        time.sleep(0.05)
        return successful_generation(requirement, results, config, **kwargs)

    app = create_app(
        make_service(slow_generation),
        job_database_path=tmp_path / "jobs.sqlite3",
        api_key="test-key",
        job_timeout_seconds=0.01,
    )

    async def scenario() -> None:
        async with api_client(app) as client:
            response = await client.post(
                "/v1/config-generations",
                json={
                    "requirement": "NTPを設定",
                    "product": "catalyst9300",
                },
            )
            job = await wait_for_terminal_job(
                client,
                response.json()["job_id"],
            )

        assert job["status"] == "failed"
        assert job["error_code"] == "timeout"
        assert job["error"] == "Configuration generation timed out"
        assert job["duration_ms"] is not None

    asyncio.run(scenario())


def test_audit_logs_exclude_api_key_and_requirement(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(
        make_service(),
        job_database_path=tmp_path / "jobs.sqlite3",
        api_key="SUPER_SECRET_API_KEY",
    )

    async def scenario() -> None:
        async with api_client(
            app,
            api_key="SUPER_SECRET_API_KEY",
        ) as client:
            response = await client.post(
                "/v1/config-generations",
                json={
                    "requirement": "SUPER_SECRET_REQUIREMENT",
                    "product": "catalyst9300",
                    "release": "26.x",
                },
            )
            await wait_for_terminal_job(client, response.json()["job_id"])

    with caplog.at_level(logging.INFO):
        asyncio.run(scenario())

    assert "SUPER_SECRET_API_KEY" not in caplog.text
    assert "SUPER_SECRET_REQUIREMENT" not in caplog.text
    assert "product=catalyst9300" in caplog.text
    assert "status=completed" in caplog.text
