import copy

import pytest

from configbrain.generate_config import _fulfillment_status, _normalize_semantic_replacement
from configbrain.report import ReportError, render_report
from configbrain.select_manuals import CloudConfig, SelectionError
from configbrain.semantic_review import (
    REPAIR_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT, SemanticReviewError,
    explanation_targets, review_explanations, review_metrics, review_units,
    validate_reviews, validate_saved_reviews,
)
from configbrain.verify_pdf import _citations


EID = "evidence-v1-" + "a" * 64
OTHER = "evidence-v1-" + "b" * 64


def review_response(units: list[dict], atoms: dict, **overrides) -> dict:
    return {"reviews": [
        {"unit_id": unit["unit_id"], "claims": [{
            "text": unit["text"], "target": "VLAN", "operation": "Add to a VLAN group",
            "support": "supported", "applicability": "confirmed", "conditions": [],
            "reason": "The cited span describes this operation.",
            "evidence": [{"evidence_id": EID, "quote": atoms[EID]["text"]}],
            **overrides,
        }]}
        for unit in units
    ]}


@pytest.fixture
def review_case(report_draft: dict) -> dict:
    draft = copy.deepcopy(report_draft)
    item = draft["items"][0]
    citation = {
        "evidence_id": EID, "source_id": "source-1",
        "quote": "A VLAN should be present in the device to be able to add it to the VLAN group.",
        "locator": {"kind": "paragraph", "block_index": 1},
    }
    for entry in [*item["constraints"], *item["withheld_steps"]]:
        entry["citations"] = [copy.deepcopy(citation)]
    atom = {
        "evidence_id": EID, "source_id": "source-1",
        "text": citation["quote"], "locator": citation["locator"],
    }
    other = {**atom, "evidence_id": OTHER, "text": "Uncited counterevidence."}
    sources = copy.deepcopy(draft["sources"])
    sources["source-1"]["text"] = (
        "Full parent context, including exceptions and prerequisites. " + atom["text"]
    )
    evidence = {
        "items": [{
            "item_id": "item-1", "source_ids": ["source-1"], "reference_source_ids": [],
            "evidence_ids": [EID, OTHER], "reference_evidence_ids": [],
        }],
        "sources": sources, "evidence_atoms": {EID: atom, OTHER: other},
    }
    return {"draft": draft, "evidence": evidence, "verified": []}


def run_review(case: dict, rounds: int = 1) -> dict:
    draft, evidence = case["draft"], case["evidence"]
    review_explanations(
        draft, evidence, CloudConfig("https://unused.test", "not-a-real-key", None), rounds,
        lambda target, value, original: _normalize_semantic_replacement(
            target, value, original, evidence,
        ),
        lambda candidate: case["verified"].append(copy.deepcopy(candidate)),
    )
    draft["generation_status"] = (
        "partial" if any(item.get("semantic_review_error") for item in draft["items"])
        else "complete"
    )
    draft["fulfillment_status"] = _fulfillment_status(draft["items"], [])
    validate_saved_reviews(draft)
    return draft


@pytest.mark.parametrize("defect", [
    "missing_unit", "duplicate_unit", "unknown_unit", "missing_clause", "rewritten_clause",
    "foreign_evidence", "fabricated_span", "uncited_support", "no_proof", "no_conditions",
    "unknown_status", "extra_cli",
])
def test_review_requires_complete_claim_coverage_and_real_scoped_proofs(
    review_case: dict, defect: str,
) -> None:
    item = review_case["draft"]["items"][0]
    item["constraints"][0]["explanation"] = "One clause and another clause."
    units = review_units(explanation_targets(item))
    atoms = review_case["evidence"]["evidence_atoms"]
    value = review_response(units, atoms)
    claim = value["reviews"][0]["claims"][0]
    if defect == "missing_unit":
        value["reviews"].pop()
    elif defect == "duplicate_unit":
        value["reviews"].append(copy.deepcopy(value["reviews"][0]))
    elif defect == "unknown_unit":
        value["reviews"][0]["unit_id"] = "u999"
    elif defect == "missing_clause":
        claim["text"] = claim["text"][:-1]
    elif defect == "rewritten_clause":
        claim["text"] += " A different claim."
    elif defect == "foreign_evidence":
        claim["evidence"][0]["evidence_id"] = "evidence-from-another-item"
    elif defect == "fabricated_span":
        claim["evidence"][0]["quote"] = "A made-up quotation."
    elif defect == "uncited_support":
        claim["evidence"] = [{"evidence_id": OTHER, "quote": atoms[OTHER]["text"]}]
    elif defect == "no_proof":
        claim["evidence"] = []
    elif defect == "no_conditions":
        claim["applicability"] = "unknown"
    elif defect == "unknown_status":
        claim["support"] = "probably"
    else:
        value["configuration"] = ["no shutdown"]
    with pytest.raises(SemanticReviewError):
        validate_reviews(value, units, atoms, {EID, OTHER})


def test_compound_claims_must_cover_every_clause_and_conditions(review_case: dict) -> None:
    item = review_case["draft"]["items"][0]
    item["constraints"][0].update(
        explanation="A prerequisite。A conclusion。",
        conditions=["Only when adding to a VLAN group."],
    )
    units = review_units(explanation_targets(item))
    assert any(unit["component"] == "condition-1" for unit in units)
    atoms = review_case["evidence"]["evidence_atoms"]
    response = review_response(units, atoms)
    claim = response["reviews"][0]["claims"][0]
    split = len(claim["text"]) // 2
    response["reviews"][0]["claims"] = [
        {**claim, "text": claim["text"][:split]},
        {**claim, "text": claim["text"][split:]},
    ]
    assert validate_reviews(response, units, atoms, {EID, OTHER}) == response["reviews"]


# Human-labeled contrasts from the demo23 investigation. These test storage,
# repair/holding and reporting, not the real model's ability to judge semantics.
LABELED_CASES = [
    (
        "vlan-group-wrong-operation",
        "VLANグループの前提なので、アクセスポート割当てもVLANの事前作成が必須です。",
        "A VLAN should be present in the device to be able to add it to the VLAN group.",
        "insufficient_evidence", "unknown", ["アクセスポート割当ての仕様確認"],
    ),
    (
        "vlan-group-correct-operation",
        "VLANグループへの追加にはVLANがデバイス上に存在する必要があります。",
        "A VLAN should be present in the device to be able to add it to the VLAN group.",
        "supported", "conditional", ["VLANグループへ追加する場合"],
    ),
    (
        "vtp-local-exclusion",
        "ローカルなVLAN作成なのでVTP primaryは関係ありません。",
        "Primary server status is needed only for database updates when the administrator "
        "issues a takeover message in the domain.",
        "insufficient_evidence", "unknown", ["VTPのバージョン・モード・サーバ役割"],
    ),
    (
        "vtp-conditioned",
        "VTP primaryの要否はデータベース更新条件に依存するため、状態の確認が必要です。",
        "Primary server status is needed only for database updates when the administrator "
        "issues a takeover message in the domain.",
        "supported", "unknown", ["VTPのバージョン・モード・サーバ役割"],
    ),
    (
        "no-shutdown-layer3-conflation",
        "no shutdownを実行するとLayer3インターフェイスになります。",
        "Restarts an interface.",
        "insufficient_evidence", "confirmed", [],
    ),
    (
        "no-shutdown-correct-role",
        "no shutdownはインターフェイスを再起動します。",
        "Restarts an interface.",
        "supported", "confirmed", [],
    ),
]


@pytest.mark.parametrize(
    "_case_id,text,quote,support,applicability,conditions", LABELED_CASES,
    ids=[case[0] for case in LABELED_CASES],
)
def test_labeled_verdicts_are_not_mistaken_for_source_or_applicability_approval(
    review_case: dict, monkeypatch: pytest.MonkeyPatch, _case_id: str, text: str,
    quote: str, support: str, applicability: str, conditions: list[str],
) -> None:
    case = review_case
    item = case["draft"]["items"][0]
    item.update(assessment=text, constraints=[], withheld_steps=[])
    case["evidence"]["evidence_atoms"][EID]["text"] = quote
    case["evidence"]["sources"]["source-1"]["text"] = "Full parent context: " + quote
    # Give this summary an actual attached citation, as in a generated CLI item.
    item["configuration"][0]["citations"][0]["quote"] = quote

    def request(system_prompt, prompt, config):
        assert system_prompt == REVIEW_SYSTEM_PROMPT
        assert "Full parent context" in prompt["source_contexts"]["source-1"]["text"]
        assert OTHER in prompt["evidence_atoms"]
        return review_response(
            prompt["units"], prompt["evidence_atoms"], support=support,
            applicability=applicability, conditions=conditions,
        ), "review-model"

    monkeypatch.setattr("configbrain.semantic_review.request_json", request)
    draft = run_review(case)
    result = draft["semantic_reviews"]["item-1"]
    report = render_report(draft)
    if support != "supported":
        assert result["status"] == "issues_found"
        assert draft["generation_status"] == "partial"
        assert draft["fulfillment_status"] == "undetermined"
        assert "この説明を根拠として採用していません" in report
        assert f"> {text}" in report
    else:
        assert result["status"] == "complete"
        assert "semantic_review_error" not in item
        assert "この説明を根拠として採用していません" not in report
        if applicability != "confirmed":
            assert item["semantic_review_pending"] is True
            assert "支持・適用条件未確認" in report
            assert conditions[0] in report
        else:
            assert "支持・要件上適用可（LLM判定）" in report


def test_explanation_repair_is_reverified_reviewed_and_cannot_change_cli(
    review_case: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = review_case
    before = copy.deepcopy(case["draft"]["items"][0])
    calls = []

    def request(system_prompt, prompt, config):
        calls.append(system_prompt)
        if system_prompt == REPAIR_SYSTEM_PROMPT:
            assert prompt["requested_target_ids"] == ["t2"]
            return {"replacements": [{
                "target_id": "t2", "replacement": {
                    "explanation": "The rule applies to adding a VLAN to a VLAN group.",
                    "evidence_ids": [EID],
                },
            }], "unresolved_target_ids": []}, "repair-model"
        response = review_response(prompt["units"], prompt["evidence_atoms"])
        if len(calls) == 1:
            response["reviews"][1]["claims"][0]["support"] = "insufficient_evidence"
        return response, "review-model"

    monkeypatch.setattr("configbrain.semantic_review.request_json", request)
    draft = run_review(case, rounds=2)
    item = draft["items"][0]
    assert calls == [REVIEW_SYSTEM_PROMPT, REPAIR_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT]
    assert len(case["verified"]) == 1
    assert item["constraints"][0]["explanation"].startswith("The rule applies")
    for field in ("configuration", "templates", "verification", "verification_templates",
                  "withheld_steps", "depends_on", "status"):
        assert item.get(field) == before.get(field)
    assert draft["semantic_reviews"]["item-1"]["status"] == "complete"
    assert len(draft["semantic_review_requests"]) == 3


@pytest.mark.parametrize("failure", ["request", "coverage", "cli_edit", "foreign_repair"])
def test_review_and_repair_fail_closed_and_raw_history_is_not_a_citation(
    review_case: dict, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    case = review_case
    before = copy.deepcopy(case["draft"]["items"])
    calls = []

    def request(system_prompt, prompt, config):
        calls.append(system_prompt)
        if failure == "request":
            raise SelectionError("Cloud LLM request failed.")
        if failure == "coverage":
            return {"reviews": [], "configuration": [{
                "source_id": "unaccepted", "evidence_id": "unaccepted", "quote": "unaccepted",
            }]}, None
        if system_prompt == REVIEW_SYSTEM_PROMPT:
            return review_response(
                prompt["units"], prompt["evidence_atoms"], support="insufficient_evidence",
            ), None
        replacement = {"explanation": "A repair.", "evidence_ids": [EID]}
        if failure == "foreign_repair":
            replacement["evidence_ids"] = ["another-item"]
        else:
            replacement["configuration"] = ["no shutdown"]
        return {
            "replacements": [{"target_id": "t2", "replacement": replacement}],
            "unresolved_target_ids": ["t1", "t3"],
        }, None

    monkeypatch.setattr("configbrain.semantic_review.request_json", request)
    draft = run_review(case, rounds=2)
    item = draft["items"][0]
    assert len(calls) <= 3
    assert item["semantic_review_error"]
    assert draft["generation_status"] == "partial"
    assert case["verified"] == []
    for field in ("assessment", "constraints", "configuration", "templates", "withheld_steps"):
        assert item[field] == before[0][field]
    assert all(citation["source_id"] != "unaccepted" for citation in _citations(draft["items"]))
    assert "この説明を根拠として採用していません" in render_report(draft)


def test_invalid_review_retries_include_diagnostic_and_previous_response(
    review_case: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def request(system_prompt, prompt, config):
        calls.append(prompt)
        if len(calls) == 1:
            return {"reviews": []}, "review-model"
        assert prompt["validation_feedback"]["previous_response"] == {"reviews": []}
        assert "Missing review units" in prompt["validation_feedback"]["error"]
        return review_response(prompt["units"], prompt["evidence_atoms"]), "review-model"

    monkeypatch.setattr("configbrain.semantic_review.request_json", request)
    result = run_review(review_case, rounds=2)
    assert len(calls) == 2
    assert result["semantic_reviews"]["item-1"]["status"] == "complete"


def test_validation_feedback_survives_a_transient_request_failure(
    review_case: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def request(system_prompt, prompt, config):
        calls.append(prompt)
        if len(calls) == 1:
            return {"reviews": []}, None
        if len(calls) == 2:
            raise SelectionError("Cloud LLM request failed.")
        assert prompt["validation_feedback"]["previous_response"] == {"reviews": []}
        assert "Missing review units" in prompt["validation_feedback"]["error"]
        return review_response(prompt["units"], prompt["evidence_atoms"]), None

    monkeypatch.setattr("configbrain.semantic_review.request_json", request)
    result = run_review(review_case, rounds=3)
    assert len(calls) == 3
    assert result["semantic_reviews"]["item-1"]["status"] == "complete"


@pytest.mark.parametrize("unresolved", [False, True])
def test_review_and_repair_stop_at_the_budget_or_explicit_no_progress(
    review_case: dict, monkeypatch: pytest.MonkeyPatch, unresolved: bool,
) -> None:
    calls = []

    def request(system_prompt, prompt, config):
        calls.append(system_prompt)
        if system_prompt == REPAIR_SYSTEM_PROMPT:
            if unresolved:
                return {
                    "replacements": [], "unresolved_target_ids": prompt["requested_target_ids"],
                }, None
            return {
                "replacements": [{
                    "target_id": "t2", "replacement": {
                        "explanation": f"Unsupported revision {len(calls)}.", "evidence_ids": [EID],
                    },
                }], "unresolved_target_ids": [],
            }, None
        response = review_response(prompt["units"], prompt["evidence_atoms"])
        response["reviews"][1]["claims"][0]["support"] = "insufficient_evidence"
        return response, None

    monkeypatch.setattr("configbrain.semantic_review.request_json", request)
    result = run_review(review_case, rounds=3)
    assert len(calls) == (2 if unresolved else 5)
    assert len(review_case["verified"]) == (0 if unresolved else 2)
    assert result["semantic_reviews"]["item-1"]["status"] == "issues_found"
    if unresolved:
        assert result["semantic_review_requests"][-1]["outcome"] == "no_progress"


def test_a_new_review_does_not_keep_stale_failure_or_pending_flags(
    review_case: dict, mock_semantic_reviewer: list,
) -> None:
    item = review_case["draft"]["items"][0]
    item.update(semantic_review_error="Previous failure.", semantic_review_pending=True)
    run_review(review_case)
    assert "semantic_review_error" not in item
    assert "semantic_review_pending" not in item


def test_counterevidence_can_contradict_even_when_not_previously_cited(review_case: dict) -> None:
    units = review_units(explanation_targets(review_case["draft"]["items"][0]))
    atoms = review_case["evidence"]["evidence_atoms"]
    response = review_response(
        units, atoms, support="contradicted",
        evidence=[{"evidence_id": OTHER, "quote": atoms[OTHER]["text"]}],
    )
    assert validate_reviews(response, units, atoms, {EID, OTHER}) == response["reviews"]


@pytest.mark.parametrize("tamper", [
    "text", "support", "missing_review", "pending_flag", "quote", "source",
    "requirement", "item_requirement", "assumptions", "conditions",
])
def test_report_rejects_stale_or_incomplete_semantic_metadata(
    review_case: dict, mock_semantic_reviewer: list, tamper: str,
) -> None:
    draft = run_review(review_case)
    if tamper == "text":
        draft["items"][0]["constraints"][0]["explanation"] += " New unreviewed claim."
    elif tamper == "support":
        draft["semantic_reviews"]["item-1"]["reviews"][0]["claims"][0]["support"] = "contradicted"
    elif tamper == "missing_review":
        draft["semantic_reviews"]["item-1"]["reviews"].pop()
    elif tamper == "pending_flag":
        draft["items"][0]["semantic_review_pending"] = True
    elif tamper == "quote":
        draft["items"][0]["constraints"][0]["citations"][0]["quote"] = "A different source span."
    elif tamper == "source":
        draft["sources"]["source-1"]["manual_sha256"] = "e" * 64
    elif tamper == "requirement":
        draft["requirement"] = "A new requirement."
    elif tamper == "item_requirement":
        draft["items"][0]["requirement"] = "A changed item requirement."
    elif tamper == "assumptions":
        draft["items"][0]["assumptions"].append("A new assumption.")
    else:
        draft["items"][0]["constraints"][0]["conditions"] = ["A new condition."]
    with pytest.raises(ReportError):
        render_report(draft)


@pytest.mark.parametrize("predictions", ["labeled", "approve_all", "abstain_all"])
def test_human_label_metrics_gate_false_approvals_without_rewarding_blanket_abstention(
    predictions: str,
) -> None:
    labels = {
        case[0]: {"support": case[3], "applicability": case[4]} for case in LABELED_CASES
    }
    reviews = [{
        "unit_id": uid, "claims": [dict(
            expected if predictions == "labeled" else
            {"support": "supported", "applicability": "confirmed"}
            if predictions == "approve_all" else
            {"support": "insufficient_evidence", "applicability": "unknown"},
        )],
    } for uid, expected in labels.items()]
    metrics = review_metrics(labels, reviews)
    assert metrics["total"] == 6
    assert metrics["passes_gate"] is (predictions == "labeled")
    if predictions == "labeled":
        assert metrics["correct"] == 6
        assert metrics["false_approvals"] == 0
    elif predictions == "approve_all":
        assert metrics["source_false_approvals"] == 3
        assert metrics["applicability_false_confirmations"] == 4
        assert metrics["false_approvals"] == 5
    else:
        assert metrics["false_approvals"] == 0
        assert metrics["correct"] < 6
    with pytest.raises(SemanticReviewError, match="coverage"):
        review_metrics(labels, reviews[:-1])
