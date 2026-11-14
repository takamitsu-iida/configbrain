"""Run deterministic retrieval and configuration checks for the Cisco RAG."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__:
    from .config_generation_service import (
        ConfigurationGenerationError,
        ConfigurationGenerationRequest,
        ConfigurationGenerationService,
    )
    from .rag import (
        DEFAULT_KNOWLEDGE_POLICY,
        DEFAULT_MODEL,
        E5Embedder,
        KNOWLEDGE_POLICIES,
        RAGError,
        _contains_search_terms_in_order,
        expand_search_plan,
        generate_configuration,
        search_expanded,
    )
    from .transform_markdown import load_config
else:
    from config_generation_service import (
        ConfigurationGenerationError,
        ConfigurationGenerationRequest,
        ConfigurationGenerationService,
    )
    from rag import (
        DEFAULT_KNOWLEDGE_POLICY,
        DEFAULT_MODEL,
        E5Embedder,
        KNOWLEDGE_POLICIES,
        RAGError,
        _contains_search_terms_in_order,
        expand_search_plan,
        generate_configuration,
        search_expanded,
    )
    from transform_markdown import load_config


def load_cases(path: Path) -> list[dict[str, Any]]:
    try:
        cases = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RAGError(f"Could not read evaluation cases: {path}") from error
    if not isinstance(cases, list) or not cases:
        raise RAGError("Evaluation cases must be a non-empty JSON array")
    return cases


def evaluate_case(
    case: dict[str, Any],
    retrieval_text: str,
    generated: dict[str, Any],
) -> dict[str, Any]:
    configuration = "\n".join(
        line.strip() for line in generated["configuration"].splitlines()
    )
    retrieval_checks = {
        term: _contains_search_terms_in_order(retrieval_text, term)
        for term in case["retrieval_terms"]
    }
    required_checks = {
        pattern: re.search(pattern, configuration) is not None
        for pattern in case["required_patterns"]
    }
    forbidden_checks = {
        pattern: re.search(pattern, configuration) is None
        for pattern in case["forbidden_patterns"]
    }
    operational_command_checks = {
        line: not line.lower().startswith(
            ("show ", "clear ", "debug ", "ping ", "traceroute ")
        )
        for line in configuration.splitlines()
        if line
    }
    expect_no_configuration = bool(case.get("expect_no_configuration", False))
    warning_pass = (
        bool(generated.get("warnings"))
        if expect_no_configuration
        else True
    )
    inference_pass = (
        not generated.get("inferred_commands")
        if expect_no_configuration
        else True
    )
    configuration_pass = (
        not configuration.strip() and warning_pass and inference_pass
        if expect_no_configuration
        else (
            all(required_checks.values())
            and all(forbidden_checks.values())
            and all(operational_command_checks.values())
        )
    )
    product_checks = {
        item["citation_id"]: item.get("product") == case["product"]
        and (
            not case.get("release")
            or item.get("release") == case["release"]
        )
        for item in generated["retrieval"]
    }
    return {
        "id": case["id"],
        "retrieval_pass": all(retrieval_checks.values()),
        "configuration_pass": configuration_pass,
        "citation_pass": bool(generated["citations"]),
        "product_isolation_pass": bool(product_checks)
        and all(product_checks.values()),
        "expect_no_configuration": expect_no_configuration,
        "warning_pass": warning_pass,
        "inference_pass": inference_pass,
        "retrieval_checks": retrieval_checks,
        "required_checks": required_checks,
        "forbidden_checks": forbidden_checks,
        "operational_command_checks": operational_command_checks,
        "product_checks": product_checks,
        "generated": generated,
    }


def run_evaluation(
    cases_path: Path,
    qdrant_path: Path,
    output_path: Path,
    model: str,
    top_k: int,
    knowledge_policy: str = DEFAULT_KNOWLEDGE_POLICY,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    cases_sha256 = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    index_manifest_path = qdrant_path / "index-manifest.json"
    if not index_manifest_path.is_file():
        raise RAGError(f"RAG index manifest does not exist: {index_manifest_path}")
    index_sha256 = hashlib.sha256(index_manifest_path.read_bytes()).hexdigest()
    service = ConfigurationGenerationService(
        qdrant_path=qdrant_path,
        embedder=E5Embedder(model),
        llm_config=load_config(),
        search_planner=expand_search_plan,
        evidence_searcher=search_expanded,
        configuration_generator=generate_configuration,
    )
    results: list[dict[str, Any]] = []
    if output_path.is_file():
        try:
            previous = json.loads(output_path.read_text(encoding="utf-8"))
            previous_results = previous.get("results", [])
        except (OSError, json.JSONDecodeError) as error:
            raise RAGError(f"Could not read existing evaluation report: {output_path}") from error
        if not isinstance(previous_results, list):
            raise RAGError("Existing evaluation report has an invalid results array")
        previous_summary = previous.get("summary", {})
        if (
            previous_summary.get("cases_sha256") != cases_sha256
            or previous_summary.get("index_sha256") != index_sha256
            or previous_summary.get("knowledge_policy", DEFAULT_KNOWLEDGE_POLICY)
            != knowledge_policy
        ):
            raise RAGError(
                "Existing evaluation report does not match the cases or RAG index"
            )
        results = previous_results
    completed_ids = {result.get("id") for result in results}
    for case in cases:
        if case["id"] in completed_ids:
            continue
        product = case.get("product")
        release = case.get("release")
        if not isinstance(product, str) or not product.strip():
            raise RAGError(f"Evaluation case {case.get('id')} has no product")
        if release is not None and not isinstance(release, str):
            raise RAGError(f"Evaluation case {case.get('id')} has an invalid release")
        outcome = service.generate(
            ConfigurationGenerationRequest(
                requirement=case["requirement"],
                product=product,
                release=release,
                knowledge_policy=knowledge_policy,
                top_k=top_k,
            )
        )
        generated = outcome.result
        retrieval_text = "\n".join(item.text for item in outcome.evidence)
        result = evaluate_case(case, retrieval_text, generated)
        results.append(result)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                {
                    "summary": {
                        "cases": len(results),
                        "retrieval_pass": sum(
                            item["retrieval_pass"] for item in results
                        ),
                        "configuration_pass": sum(
                            item["configuration_pass"] for item in results
                        ),
                        "citation_pass": sum(
                            item["citation_pass"] for item in results
                        ),
                        "product_isolation_pass": sum(
                            item["product_isolation_pass"] for item in results
                        ),
                        "complete": len(results) == len(cases),
                        "cases_sha256": cases_sha256,
                        "index_sha256": index_sha256,
                        "embedding_model": model,
                        "top_k": top_k,
                        "knowledge_policy": knowledge_policy,
                    },
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return json.loads(output_path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate Cisco RAG generation.")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("data/evaluation/config_cases.json"),
    )
    parser.add_argument("--qdrant-path", type=Path, default=Path("data/qdrant"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/evaluation/results.json"),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument(
        "--knowledge-policy",
        choices=KNOWLEDGE_POLICIES,
        default=DEFAULT_KNOWLEDGE_POLICY,
        help="Control use of LLM prior knowledge (default: disallow)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = run_evaluation(
            args.cases,
            args.qdrant_path,
            args.output,
            args.model,
            args.top_k,
            args.knowledge_policy,
        )
    except (RAGError, ConfigurationGenerationError) as error:
        print(f"RAG evaluation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
