import json
from pathlib import Path
from typing import Any

import pytest

from scripts.evaluate_api import (
    APIEvaluationError,
    BaselineCase,
    evaluate_api_result,
    load_baseline_cases,
    run_api_evaluation,
    submit_and_wait,
)


class FakeResponse:
    def __init__(self, status_code: int, value: dict[str, Any]) -> None:
        self.status_code = status_code
        self._value = value

    def json(self) -> dict[str, Any]:
        return self._value


class FakeClient:
    def __init__(
        self,
        result: dict[str, Any],
        *,
        failed: bool = False,
    ) -> None:
        self._result = result
        self._failed = failed
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.posts.append({"url": url, **kwargs})
        return FakeResponse(
            202,
            {
                "job_id": f"job-{len(self.posts)}",
                "status_url": f"http://api.test/jobs/{len(self.posts)}",
            },
        )

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        if self._failed:
            return FakeResponse(
                200,
                {
                    "status": "failed",
                    "error_code": "generation_failed",
                },
            )
        return FakeResponse(
            200,
            {
                "status": "completed",
                "request_id": "request-001",
                "duration_ms": 10,
                "result": self._result,
            },
        )


def generated_result(configuration: str = "ntp server 192.0.2.1\n") -> dict[str, Any]:
    return {
        "requirement": "NTPサーバー192.0.2.1を設定",
        "knowledge_policy": "disallow",
        "configuration": configuration,
        "explanation": "根拠に基づき生成しました。",
        "citations": ["guide:section-0001"],
        "warnings": [],
        "inferred_commands": [],
        "unsupported_requirements": [],
        "missing_required_values": [],
        "retrieval": [
            {
                "citation_id": "guide:section-0001",
                "score": 1.0,
                "document_id": "guide",
                "product": "catalyst9300",
                "release": "26.x",
                "heading_path": ["NTP"],
            }
        ],
        "search_queries": ["NTP server configuration"],
        "cli_terms": ["ntp server"],
    }


def baseline_case() -> BaselineCase:
    return BaselineCase(
        directory_name="case-01",
        case_id="ntp-server",
        product="catalyst9300",
        release="26.x",
        requirement="NTPサーバー192.0.2.1を設定",
        retrieval_terms=("ntp server",),
        required_patterns=(r"(?im)^ntp server 192\.0\.2\.1$",),
        forbidden_patterns=(r"(?im)^ntp master$",),
        expect_no_configuration=False,
        configurations=frozenset({"ntp server 192.0.2.1"}),
        decision_signatures=frozenset({(False, False)}),
        baseline_sha256="baseline-sha",
    )


def write_baseline(directory: Path) -> None:
    result = {
        "id": "ntp-server",
        "retrieval_pass": True,
        "configuration_pass": True,
        "citation_pass": True,
        "product_isolation_pass": True,
        "expect_no_configuration": False,
        "retrieval_checks": {"ntp server": True},
        "required_checks": {r"(?im)^ntp server 192\.0\.2\.1$": True},
        "forbidden_checks": {r"(?im)^ntp master$": True},
        "generated": generated_result(),
    }
    for run_number in range(1, 4):
        path = directory / "case-01" / f"run-{run_number:02d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"results": [result]}, ensure_ascii=False),
            encoding="utf-8",
        )


def test_submit_and_wait_returns_completed_result() -> None:
    client = FakeClient(generated_result())

    completed = submit_and_wait(
        client,
        "http://api.test",
        "secret",
        {
            "requirement": "NTPサーバー192.0.2.1を設定",
            "product": "catalyst9300",
        },
        "request-001",
        poll_interval=0,
        timeout=1,
    )

    assert completed["job_id"] == "job-1"
    assert completed["result"]["configuration"] == "ntp server 192.0.2.1\n"
    assert client.posts[0]["headers"]["X-API-Key"] == "secret"
    assert client.posts[0]["headers"]["Idempotency-Key"] == "request-001"


def test_submit_and_wait_rejects_failed_job() -> None:
    client = FakeClient(generated_result(), failed=True)

    with pytest.raises(APIEvaluationError, match="generation_failed"):
        submit_and_wait(
            client,
            "http://api.test",
            "secret",
            {"requirement": "NTP", "product": "catalyst9300"},
            "request-001",
            poll_interval=0,
            timeout=1,
        )


def test_evaluate_api_result_detects_equivalence_and_mismatch() -> None:
    passing = evaluate_api_result(baseline_case(), generated_result())
    mismatched = evaluate_api_result(
        baseline_case(),
        generated_result("ntp master 1\n"),
    )

    assert passing["all_pass"]
    assert passing["configuration_equivalent"]
    assert not mismatched["all_pass"]
    assert not mismatched["configuration_pass"]
    assert not mismatched["configuration_equivalent"]


def test_run_api_evaluation_saves_reproducible_summary(
    tmp_path: Path,
) -> None:
    baseline_dir = tmp_path / "baseline"
    output_dir = tmp_path / "output"
    write_baseline(baseline_dir)

    report = run_api_evaluation(
        baseline_dir,
        output_dir,
        "http://api.test",
        "secret",
        runs=3,
        top_k=6,
        knowledge_policy="disallow",
        poll_interval=0,
        job_timeout=1,
        client=FakeClient(generated_result()),
    )

    assert report["summary"]["all_pass"]
    assert report["summary"]["runs"] == 3
    assert report["summary"]["passed_runs"] == 3
    assert report["summary"]["reproducible_cases"] == 1
    assert (output_dir / "summary.json").is_file()
    assert len(load_baseline_cases(baseline_dir)) == 1


def test_run_api_evaluation_rejects_saved_result_for_other_settings(
    tmp_path: Path,
) -> None:
    baseline_dir = tmp_path / "baseline"
    output_dir = tmp_path / "output"
    write_baseline(baseline_dir)
    output_path = output_dir / "case-01" / "run-01.json"
    output_path.parent.mkdir(parents=True)
    output_path.write_text(
        json.dumps(
            {
                "evaluation_fingerprint": "different",
                "baseline_sha256": "different",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(APIEvaluationError, match="settings do not match"):
        run_api_evaluation(
            baseline_dir,
            output_dir,
            "http://api.test",
            "secret",
            runs=1,
            top_k=6,
            knowledge_policy="disallow",
            poll_interval=0,
            job_timeout=1,
            client=FakeClient(generated_result()),
        )
