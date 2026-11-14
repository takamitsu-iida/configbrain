"""Evaluate HTTP API results against reproducible CLI baselines."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Protocol
from urllib.parse import urljoin

from dotenv import dotenv_values
import requests

if __package__:
    from .rag import (
        DEFAULT_KNOWLEDGE_POLICY,
        KNOWLEDGE_POLICIES,
        _contains_search_terms_in_order,
    )
else:
    from rag import (
        DEFAULT_KNOWLEDGE_POLICY,
        KNOWLEDGE_POLICIES,
        _contains_search_terms_in_order,
    )


class APIEvaluationError(RuntimeError):
    """Raised when API equivalence cannot be evaluated safely."""


EVALUATOR_SCHEMA_VERSION = 2


class HTTPClient(Protocol):
    def post(self, url: str, **kwargs: Any) -> Any: ...

    def get(self, url: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class BaselineCase:
    directory_name: str
    case_id: str
    product: str
    release: str | None
    requirement: str
    retrieval_terms: tuple[str, ...]
    required_patterns: tuple[str, ...]
    forbidden_patterns: tuple[str, ...]
    expect_no_configuration: bool
    configurations: frozenset[str]
    decision_signatures: frozenset[tuple[bool, bool]]
    baseline_sha256: str


def load_baseline_cases(baseline_dir: Path) -> list[BaselineCase]:
    if not baseline_dir.is_dir():
        raise APIEvaluationError(
            f"CLI baseline directory does not exist: {baseline_dir}"
        )
    case_dirs = sorted(path for path in baseline_dir.iterdir() if path.is_dir())
    if not case_dirs:
        raise APIEvaluationError(
            f"CLI baseline directory has no case directories: {baseline_dir}"
        )
    return [_load_baseline_case(path) for path in case_dirs]


def _load_baseline_case(case_dir: Path) -> BaselineCase:
    paths = sorted(case_dir.glob("run-*.json"))
    if not paths:
        raise APIEvaluationError(f"CLI baseline case has no runs: {case_dir}")
    reports = [_load_json(path, "CLI baseline") for path in paths]
    results: list[dict[str, Any]] = []
    for path, report in zip(paths, reports, strict=True):
        report_results = report.get("results")
        if not isinstance(report_results, list) or len(report_results) != 1:
            raise APIEvaluationError(
                f"CLI baseline must contain exactly one result: {path}"
            )
        result = report_results[0]
        if not isinstance(result, dict):
            raise APIEvaluationError(f"CLI baseline result is invalid: {path}")
        if not all(
            result.get(field) is True
            for field in (
                "retrieval_pass",
                "configuration_pass",
                "citation_pass",
                "product_isolation_pass",
            )
        ):
            raise APIEvaluationError(f"CLI baseline run is not passing: {path}")
        results.append(result)
    first = results[0]
    generated = first.get("generated")
    if not isinstance(generated, dict):
        raise APIEvaluationError(f"CLI baseline has no generated result: {case_dir}")
    retrieval = generated.get("retrieval")
    if not isinstance(retrieval, list) or not retrieval:
        raise APIEvaluationError(f"CLI baseline has no retrieval evidence: {case_dir}")
    first_retrieval = retrieval[0]
    if not isinstance(first_retrieval, dict):
        raise APIEvaluationError(f"CLI baseline retrieval is invalid: {case_dir}")
    case_id = first.get("id")
    product = first_retrieval.get("product")
    requirement = generated.get("requirement")
    if not all(isinstance(value, str) and value for value in (case_id, product, requirement)):
        raise APIEvaluationError(f"CLI baseline identifiers are invalid: {case_dir}")
    release = first_retrieval.get("release")
    if release is not None and not isinstance(release, str):
        raise APIEvaluationError(f"CLI baseline release is invalid: {case_dir}")
    for result in results[1:]:
        current_generated = result.get("generated", {})
        if (
            result.get("id") != case_id
            or current_generated.get("requirement") != requirement
        ):
            raise APIEvaluationError(
                f"CLI baseline runs describe different cases: {case_dir}"
            )
    configurations = frozenset(
        _normalize_configuration(str(result["generated"]["configuration"]))
        for result in results
    )
    if len(configurations) != 1:
        raise APIEvaluationError(
            f"CLI baseline configurations are not reproducible: {case_dir}"
        )
    decision_signatures = frozenset(
        _decision_signature(result["generated"])
        for result in results
    )
    baseline_sha256 = hashlib.sha256(
        b"".join(path.read_bytes() for path in paths)
    ).hexdigest()
    return BaselineCase(
        directory_name=case_dir.name,
        case_id=case_id,
        product=product,
        release=release,
        requirement=requirement,
        retrieval_terms=tuple(first.get("retrieval_checks", {}).keys()),
        required_patterns=tuple(first.get("required_checks", {}).keys()),
        forbidden_patterns=tuple(first.get("forbidden_checks", {}).keys()),
        expect_no_configuration=bool(first.get("expect_no_configuration")),
        configurations=configurations,
        decision_signatures=decision_signatures,
        baseline_sha256=baseline_sha256,
    )


def submit_and_wait(
    client: HTTPClient,
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
    idempotency_key: str,
    *,
    poll_interval: float,
    timeout: float,
) -> dict[str, Any]:
    headers = {
        "X-API-Key": api_key,
        "Idempotency-Key": idempotency_key,
    }
    endpoint = urljoin(base_url.rstrip("/") + "/", "v1/config-generations")
    try:
        response = client.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as error:
        raise APIEvaluationError("Could not submit API evaluation job") from error
    if response.status_code != 202:
        raise APIEvaluationError(
            f"API job submission returned HTTP {response.status_code}"
        )
    accepted = _response_json(response, "API job submission")
    job_id = accepted.get("job_id")
    status_url = accepted.get("status_url")
    if not isinstance(job_id, str) or not job_id:
        raise APIEvaluationError("API job submission returned no job_id")
    if not isinstance(status_url, str) or not status_url:
        status_url = f"{endpoint}/{job_id}"
    deadline = time.monotonic() + timeout
    while True:
        try:
            response = client.get(
                status_url,
                headers={"X-API-Key": api_key},
                timeout=30,
            )
        except requests.RequestException as error:
            raise APIEvaluationError("Could not poll API evaluation job") from error
        if response.status_code != 200:
            raise APIEvaluationError(
                f"API job polling returned HTTP {response.status_code}"
            )
        job = _response_json(response, "API job polling")
        status_value = job.get("status")
        if status_value == "completed":
            result = job.get("result")
            if not isinstance(result, dict):
                raise APIEvaluationError("Completed API job has no result")
            return {
                "job_id": job_id,
                "request_id": job.get("request_id"),
                "duration_ms": job.get("duration_ms"),
                "result": result,
            }
        if status_value == "failed":
            error_code = job.get("error_code") or "unknown"
            raise APIEvaluationError(f"API evaluation job failed: {error_code}")
        if status_value not in {"queued", "running"}:
            raise APIEvaluationError(
                f"API job returned an unknown status: {status_value}"
            )
        if time.monotonic() >= deadline:
            raise APIEvaluationError(
                f"API evaluation job polling timed out: {job_id}"
            )
        time.sleep(poll_interval)


def evaluate_api_result(
    baseline: BaselineCase,
    generated: dict[str, Any],
) -> dict[str, Any]:
    configuration = _normalize_configuration(
        str(generated.get("configuration", ""))
    )
    search_plan_text = "\n".join(
        [
            *(
                str(item)
                for item in generated.get("search_queries", [])
                if isinstance(item, str)
            ),
            *(
                str(item)
                for item in generated.get("cli_terms", [])
                if isinstance(item, str)
            ),
        ]
    )
    retrieval_checks = {
        term: _contains_search_terms_in_order(search_plan_text, term)
        for term in baseline.retrieval_terms
    }
    required_checks = {
        pattern: re.search(pattern, configuration) is not None
        for pattern in baseline.required_patterns
    }
    forbidden_checks = {
        pattern: re.search(pattern, configuration) is None
        for pattern in baseline.forbidden_patterns
    }
    retrieval = generated.get("retrieval")
    retrieval_items = retrieval if isinstance(retrieval, list) else []
    citation_ids = {
        item.get("citation_id")
        for item in retrieval_items
        if isinstance(item, dict)
    }
    citations = generated.get("citations")
    citation_values = citations if isinstance(citations, list) else []
    citation_pass = bool(citation_values) and set(citation_values).issubset(
        citation_ids
    )
    product_isolation_pass = bool(retrieval_items) and all(
        isinstance(item, dict)
        and item.get("product") == baseline.product
        and (
            baseline.release is None
            or item.get("release") == baseline.release
        )
        for item in retrieval_items
    )
    expect_no_configuration_pass = (
        not configuration
        if baseline.expect_no_configuration
        else bool(configuration)
    )
    inferred_commands = generated.get("inferred_commands")
    inference_pass = isinstance(inferred_commands, list) and not inferred_commands
    configuration_pass = (
        all(required_checks.values())
        and all(forbidden_checks.values())
        and expect_no_configuration_pass
        and inference_pass
    )
    configuration_equivalent = configuration in baseline.configurations
    decision_equivalent = (
        _decision_signature(generated) in baseline.decision_signatures
    )
    checks = {
        "retrieval_plan_pass": all(retrieval_checks.values()),
        "configuration_pass": configuration_pass,
        "citation_pass": citation_pass,
        "product_isolation_pass": product_isolation_pass,
        "configuration_equivalent": configuration_equivalent,
        "decision_equivalent": decision_equivalent,
        "inference_pass": inference_pass,
    }
    return {
        **checks,
        "all_pass": all(checks.values()),
        "retrieval_checks": retrieval_checks,
        "required_checks": required_checks,
        "forbidden_checks": forbidden_checks,
        "normalized_configuration": configuration,
    }


def run_api_evaluation(
    baseline_dir: Path,
    output_dir: Path,
    base_url: str,
    api_key: str,
    *,
    runs: int,
    top_k: int,
    knowledge_policy: str,
    poll_interval: float,
    job_timeout: float,
    client: HTTPClient | None = None,
) -> dict[str, Any]:
    if runs < 1:
        raise APIEvaluationError("Evaluation runs must be at least 1")
    baselines = load_baseline_cases(baseline_dir)
    http_client = client or requests.Session()
    evaluations: list[dict[str, Any]] = []
    for baseline in baselines:
        evaluation_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "baseline_sha256": baseline.baseline_sha256,
                    "base_url": base_url.rstrip("/"),
                    "top_k": top_k,
                    "knowledge_policy": knowledge_policy,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        for run_number in range(1, runs + 1):
            output_path = (
                output_dir
                / baseline.directory_name
                / f"run-{run_number:02d}.json"
            )
            if output_path.is_file():
                saved = _load_json(output_path, "API evaluation")
                if (
                    saved.get("evaluation_fingerprint")
                    != evaluation_fingerprint
                ):
                    raise APIEvaluationError(
                        f"Saved API evaluation settings do not match: {output_path}"
                    )
                generated = saved.get("generated")
                if not isinstance(generated, dict):
                    raise APIEvaluationError(
                        f"Saved API evaluation has no generated result: {output_path}"
                    )
                saved["evaluator_schema_version"] = EVALUATOR_SCHEMA_VERSION
                saved["evaluation"] = evaluate_api_result(
                    baseline,
                    generated,
                )
                _write_json(output_path, saved)
                evaluations.append(saved)
                continue
            idempotency_key = (
                f"api-equivalence:{evaluation_fingerprint[:16]}:"
                f"{run_number:02d}"
            )
            completed = submit_and_wait(
                http_client,
                base_url,
                api_key,
                {
                    "requirement": baseline.requirement,
                    "product": baseline.product,
                    "release": baseline.release,
                    "knowledge_policy": knowledge_policy,
                    "top_k": top_k,
                },
                idempotency_key,
                poll_interval=poll_interval,
                timeout=job_timeout,
            )
            generated = completed["result"]
            evaluation = evaluate_api_result(baseline, generated)
            saved = {
                "case_id": baseline.case_id,
                "case_directory": baseline.directory_name,
                "run": run_number,
                "baseline_sha256": baseline.baseline_sha256,
                "evaluation_fingerprint": evaluation_fingerprint,
                "evaluator_schema_version": EVALUATOR_SCHEMA_VERSION,
                "job_id": completed["job_id"],
                "request_id": completed["request_id"],
                "duration_ms": completed["duration_ms"],
                "evaluation": evaluation,
                "generated": generated,
            }
            _write_json(output_path, saved)
            evaluations.append(saved)
    summary = _build_summary(baselines, evaluations, runs)
    report = {
        "summary": summary,
        "baseline_dir": str(baseline_dir),
        "base_url": base_url,
        "runs": evaluations,
    }
    _write_json(output_dir / "summary.json", report)
    return report


def _build_summary(
    baselines: list[BaselineCase],
    evaluations: list[dict[str, Any]],
    runs: int,
) -> dict[str, Any]:
    case_summaries: dict[str, dict[str, Any]] = {}
    for baseline in baselines:
        case_runs = [
            item
            for item in evaluations
            if item.get("case_id") == baseline.case_id
        ]
        configurations = {
            item.get("evaluation", {}).get("normalized_configuration")
            for item in case_runs
        }
        case_summaries[baseline.case_id] = {
            "runs": len(case_runs),
            "all_pass": len(case_runs) == runs
            and all(item.get("evaluation", {}).get("all_pass") for item in case_runs),
            "configuration_reproducible": len(configurations) == 1,
        }
    return {
        "cases": len(baselines),
        "runs": len(evaluations),
        "passed_runs": sum(
            bool(item.get("evaluation", {}).get("all_pass"))
            for item in evaluations
        ),
        "reproducible_cases": sum(
            item["configuration_reproducible"] and item["all_pass"]
            for item in case_summaries.values()
        ),
        "complete": len(evaluations) == len(baselines) * runs,
        "all_pass": all(
            item["all_pass"] and item["configuration_reproducible"]
            for item in case_summaries.values()
        ),
        "case_results": case_summaries,
    }


def _decision_signature(generated: dict[str, Any]) -> tuple[bool, bool]:
    return (
        not bool(str(generated.get("configuration", "")).strip()),
        bool(
            generated.get("unsupported_requirements")
            or generated.get("missing_required_values")
        ),
    )


def _normalize_configuration(configuration: str) -> str:
    return "\n".join(
        line.strip()
        for line in configuration.splitlines()
        if line.strip()
    )


def _response_json(response: Any, context: str) -> dict[str, Any]:
    try:
        value = response.json()
    except (ValueError, json.JSONDecodeError) as error:
        raise APIEvaluationError(f"{context} returned invalid JSON") from error
    if not isinstance(value, dict):
        raise APIEvaluationError(f"{context} returned a non-object JSON value")
    return value


def _load_json(path: Path, context: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise APIEvaluationError(f"Could not read {context}: {path}") from error
    if not isinstance(value, dict):
        raise APIEvaluationError(f"{context} must be a JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_api_key(env_file: Path) -> str:
    values = dotenv_values(env_file) if env_file.is_file() else {}
    api_key = os.environ.get("CONFIGBRAIN_API_KEY") or values.get(
        "CONFIGBRAIN_API_KEY"
    )
    if not isinstance(api_key, str) or not api_key.strip():
        raise APIEvaluationError("CONFIGBRAIN_API_KEY must be configured")
    return api_key.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate API generation against passing CLI baselines."
    )
    parser.add_argument(
        "--baseline-dir",
        type=Path,
        default=Path("data/evaluation/reproducibility-v4"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/evaluation/api-equivalence-v1"),
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument(
        "--knowledge-policy",
        choices=KNOWLEDGE_POLICIES,
        default=DEFAULT_KNOWLEDGE_POLICY,
    )
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--job-timeout", type=float, default=360.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = run_api_evaluation(
            args.baseline_dir,
            args.output_dir,
            args.base_url,
            load_api_key(args.env_file),
            runs=args.runs,
            top_k=args.top_k,
            knowledge_policy=args.knowledge_policy,
            poll_interval=args.poll_interval,
            job_timeout=args.job_timeout,
        )
    except APIEvaluationError as error:
        print(f"API evaluation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
