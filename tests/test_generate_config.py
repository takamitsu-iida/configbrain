import copy
import json
from pathlib import Path
from typing import Any

import pytest

from configbrain.generate_config import (
    GenerationError, build_evidence, generate_config, main, validate_draft,
)
from configbrain.select_manuals import CloudConfig, SelectionError


@pytest.fixture
def contexts() -> dict[str, Any]:
    markdown = """# Configuring VLANs

## Procedure

<table>
<tr><th>Step</th><th>Command or Action</th><th>Purpose</th></tr>
<tr><td>Step 1</td><td><p>configure terminal</p></td><td>Enter global configuration.</td></tr>
<tr><td>Step 2</td><td><p>vlan <var>vlan-id</var></p></td><td>Create the VLAN.</td></tr>
<tr><td>Step 3</td><td><p>interface <var>interface-id</var></p></td><td>Select a port.</td></tr>
<tr><td>Step 4</td><td><p>end</p></td><td>Return to EXEC.</td></tr>
<tr><td>Step 5</td><td><p>show vlan {name <var>vlan-name</var> | id <var>vlan-id</var>} [brief]</p></td><td>Verify.</td></tr>
</table>
"""
    return {
        "schema_version": 1, "product": "catalyst9300", "release": "26.x",
        "requirement": "VLAN 120を作成し、アクセスポートに割り当てる",
        "selection_sha256": "a" * 64, "search_results_sha256": "b" * 64,
        "catalog_sha256": "c" * 64, "dataset_fingerprint": "d" * 64,
        "evidence_status": "candidates_only", "pdf_verification_status": "not_verified",
        "limitations": ["Original PDF content not verified."],
        "items": [
            {"requirement": "Create VLAN and assign port", "uncertainty": "Port unspecified.",
             "status": "retrieved", "chapters": [{
                 "document_id": "vlan-guide", "document_title": "VLAN guide",
                 "chapter_order": 3, "chapter_title": "Configuring VLANs",
                 "chapter_url": "https://www.cisco.com/vlan.html",
                 "source_pdf_sha256": "e" * 64, "html_content_sha256": "f" * 64,
                 "manual_path": "/local/manual.md", "manual_sha256": "1" * 64,
                 "source_pdf_path": "/local/manual.pdf", "pdf_page_index": 35,
                 "pdf_page_scope": "chapter_start",
                 "contexts": [{
                     "context_id": "lines-1-12", "section_path": ["Configuring VLANs"],
                     "roles": ["task"], "chapter_line_start": 1, "chapter_line_end": 12,
                     "manual_line_start": 100, "manual_line_end": 111,
                     "markdown": markdown,
                 }],
             }]},
            {"requirement": "Unknown feature", "uncertainty": "No sources.",
             "status": "no_candidates", "chapters": []},
        ],
    }


def command_id(evidence: dict[str, Any], prefix: str) -> str:
    return next(
        key for key, command in evidence["commands"].items()
        if command["syntax"].startswith(prefix)
    )


def response(evidence: dict[str, Any]) -> dict[str, Any]:
    def line(prefix: str, cli: str, bindings: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"command_id": command_id(evidence, prefix), "cli": cli, "bindings": bindings or {}}

    return {
        "ready_to_apply": True,
        "items": [
            {
                "item_id": "item-1", "status": "needs_input",
                "assessment": "Port is unspecified; VLAN creation is partially described.",
                "configuration": [
                    line("configure", "configure terminal"),
                    line("vlan ", "vlan 120", {"vlan-id": {"value": "120", "origin": "requirement"}}),
                    line("end", "end"),
                ],
                "templates": [
                    line("interface", "interface <INTERFACE_ID>", {
                        "interface-id": {"value": "<INTERFACE_ID>", "origin": "placeholder"},
                    }),
                ],
                "verification": [
                    line("show", "show vlan id 120", {
                        "vlan-id": {"value": "120", "origin": "requirement"},
                    }),
                ],
                "verification_templates": [],
                "missing_information": ["Target interface."],
                "assumptions": ["Check current VTP configuration."],
                "evidence": [{
                    "explanation": "The procedure describes VLAN creation.",
                    "citations": [{"source_id": "source-1", "quote": "Create the VLAN."}],
                }],
                "constraints": [],
                "withheld_steps": [],
                "inference_candidates": [{
                    "description": "Review whether additional protection is needed.",
                    "assumptions": ["Requires separate evidence and topology review."],
                }],
            },
            {
                "item_id": "item-2", "status": "not_found",
                "assessment": "No selected source.",
                "configuration": [], "templates": [], "verification": [],
                "verification_templates": [], "evidence": [], "constraints": [],
                "missing_information": ["Find applicable documentation."],
                "assumptions": [], "withheld_steps": [], "inference_candidates": [],
            },
        ],
        "next_checks": ["Review the PDF and provide the target interface."],
    }


def test_source_commands_and_typed_parameters_are_extracted(contexts: dict[str, Any]) -> None:
    evidence = build_evidence(contexts)
    vlan = evidence["commands"][command_id(evidence, "vlan ")]
    assert vlan["syntax"] == "vlan vlan-id"
    assert vlan["parameters"] == ["vlan-id"]
    assert vlan["citation"] == {
        "source_id": "source-1", "table_index_in_context": 1,
        "row_index": 3, "step_label": "Step 2",
    }
    assert evidence["sources"]["source-1"]["pdf_page_scope"] == "chapter_start"
    assert evidence["items"][1]["source_ids"] == []


@pytest.mark.parametrize("tag", ["var", "em", "i"])
@pytest.mark.parametrize("parameters", [("ip-address", "subnet-mask"), ("ip_address", "subnet_mask")])
def test_adjacent_parameters_in_one_tag_are_bound_independently(
    contexts: dict[str, Any], tag: str, parameters: tuple[str, str],
) -> None:
    context = contexts["items"][0]["chapters"][0]["contexts"][0]
    context["markdown"] = context["markdown"].replace(
        "</table>",
        f"<tr><td>Step 6</td><td><p>ip address "
        f"<{tag}>{' '.join(parameters)}</{tag}></p></td><td>Assign IPv4.</td></tr></table>",
    )
    evidence = build_evidence(contexts)
    cid = command_id(evidence, "ip address")
    assert evidence["commands"][cid]["parameters"] == list(parameters)
    value = response(evidence)
    line = {
        "command_id": cid, "cli": "ip address <IP_ADDRESS> <SUBNET_MASK>",
        "bindings": {
            parameters[0]: {"value": "<IP_ADDRESS>", "origin": "placeholder"},
            parameters[1]: {"value": "<SUBNET_MASK>", "origin": "placeholder"},
        },
    }
    value["items"][0]["templates"].append(line)
    result = validate_draft(value, evidence)
    assert result["items"][0]["templates"][-1]["unresolved_parameters"] == [
        "IP_ADDRESS", "SUBNET_MASK",
    ]
    line["cli"] = "ip address <SUBNET_MASK> <IP_ADDRESS>"
    with pytest.raises(GenerationError, match=f"syntax for {cid}"):
        validate_draft(value, evidence)
    line["cli"] = "ip address <IP_ADDRESS> <SUBNET_MASK>"
    del line["bindings"][parameters[1]]
    with pytest.raises(GenerationError, match=f"syntax for {cid}"):
        validate_draft(value, evidence)


@pytest.mark.parametrize(
    "requirement,mask,valid",
    [
        ("192.0.2.1/24", "255.255.255.0", True),
        ("192.0.2.1/0", "0.0.0.0", True),
        ("192.0.2.1/32", "255.255.255.255", True),
        ("192.0.2.1/24", "255.255.255.128", False),
        ("192.0.2.1/24 and 198.51.100.1/25", "255.255.255.128", False),
        ("192.0.2.1", "255.255.255.0", False),
    ],
)
def test_cidr_netmask_must_be_derived_from_the_bound_address(
    contexts: dict[str, Any], requirement: str, mask: str, valid: bool,
) -> None:
    contexts["requirement"] += f" IPv4 {requirement}"
    context = contexts["items"][0]["chapters"][0]["contexts"][0]
    context["markdown"] = context["markdown"].replace(
        "</table>",
        "<tr><td>Step 6</td><td><p>ip address <var>ip-address subnet-mask</var>"
        "</p></td><td>Assign IPv4.</td></tr></table>",
    )
    evidence = build_evidence(contexts)
    value = response(evidence)
    cid = command_id(evidence, "ip address")
    value["items"][0]["configuration"].append({
        "command_id": cid, "cli": f"ip address 192.0.2.1 {mask}",
        "bindings": {
            "subnet-mask": {"value": mask, "origin": "requirement"},
            "ip-address": {"value": "192.0.2.1", "origin": "requirement"},
        },
    })
    if not valid:
        with pytest.raises(GenerationError, match=f"{cid}/subnet-mask.*not present"):
            validate_draft(value, evidence)
        return
    result = validate_draft(value, evidence)
    binding = result["items"][0]["configuration"][-1]["bindings"]["subnet-mask"]
    assert binding["value"] == mask
    assert binding["derivation"] == {"kind": "ipv4_cidr_netmask", "source": requirement}
    assert evidence["normalized_ipv4_networks"] == [{
        "cidr": requirement, "address": "192.0.2.1", "netmask": mask,
    }]


@pytest.mark.parametrize("cidr", ["256.0.2.1/24", "192.0.2.1/33"])
def test_invalid_ipv4_cidr_is_rejected_before_cloud(contexts: dict[str, Any], cidr: str) -> None:
    contexts["requirement"] += f" {cidr}"
    with pytest.raises(GenerationError, match="Invalid IPv4 CIDR"):
        build_evidence(contexts)


@pytest.mark.parametrize("binding_value", ["fortygigabitethernet1/0/1", "FortyGigabitEthernet1/0/1"])
def test_interface_case_is_restored_to_the_requirement_and_recorded(
    contexts: dict[str, Any], binding_value: str,
) -> None:
    contexts["requirement"] += " FortyGigabitEthernet1/0/1"
    evidence = build_evidence(contexts)
    value = response(evidence)
    line = value["items"][0]["templates"].pop()
    line.update(cli="interface fortygigabitethernet1/0/1", bindings={
        "interface-id": {"value": binding_value, "origin": "requirement"},
    })
    value["items"][0]["configuration"].append(line)
    validated = validate_draft(value, evidence)["items"][0]["configuration"][-1]
    assert validated["cli"] == "interface FortyGigabitEthernet1/0/1"
    assert validated["bindings"]["interface-id"]["value"] == "FortyGigabitEthernet1/0/1"
    assert validated["normalizations"] == [{
        "kind": "interface_case", "parameter": "interface-id",
        "original_value": binding_value, "value": "FortyGigabitEthernet1/0/1",
        "original_cli": "interface fortygigabitethernet1/0/1",
    }]


@pytest.mark.parametrize("invalid", ["Fo1/0/1", "Ethernet1/0/1", "FortyGigabitEthernet1/0/10"])
def test_interface_normalization_does_not_expand_or_change_ports(
    contexts: dict[str, Any], invalid: str,
) -> None:
    contexts["requirement"] += " FortyGigabitEthernet1/0/1"
    evidence = build_evidence(contexts)
    value = response(evidence)
    line = value["items"][0]["templates"].pop()
    line.update(cli=f"interface {invalid}", bindings={
        "interface-id": {"value": invalid, "origin": "requirement"},
    })
    value["items"][0]["configuration"].append(line)
    with pytest.raises(GenerationError, match="interface-id.*not present"):
        validate_draft(value, evidence)


def test_case_normalization_does_not_apply_to_vlan_names(contexts: dict[str, Any]) -> None:
    contexts["requirement"] += " VLAN name SALES"
    evidence = build_evidence(contexts)
    value = response(evidence)
    value["items"][0]["verification"][0].update(cli="show vlan name sales", bindings={
        "vlan-name": {"value": "sales", "origin": "requirement"},
    })
    with pytest.raises(GenerationError, match="vlan-name.*not present"):
        validate_draft(value, evidence)


def test_quote_error_identifies_the_exact_finding_and_source(contexts: dict[str, Any]) -> None:
    evidence = build_evidence(contexts)
    value = response(evidence)
    value["items"][0]["evidence"][0]["citations"][0]["quote"] = "Not in the supplied manual."
    with pytest.raises(
        GenerationError,
        match=r"item-1/evidence\[1\]/citations\[1\] is not present in source-1",
    ):
        validate_draft(value, evidence)


def test_validation_retains_partial_commands_templates_and_all_items(contexts: dict[str, Any]) -> None:
    evidence = build_evidence(contexts)
    draft = validate_draft(response(evidence), evidence)
    first, unresolved = draft["items"]
    assert first["configuration"][1]["cli"] == "vlan 120"
    assert first["configuration"][1]["citations"][0]["step_label"] == "Step 2"
    assert first["templates"][0]["unresolved_parameters"] == ["INTERFACE_ID"]
    assert first["inference_candidates"][0]["ready_to_apply"] is False
    assert unresolved["status"] == "not_found"
    assert "ready_to_apply" not in draft


def test_integer_binding_is_normalized_and_still_checked_against_requirement(
    contexts: dict[str, Any],
) -> None:
    evidence = build_evidence(contexts)
    value = response(evidence)
    line = value["items"][0]["configuration"][1]
    line["bindings"]["vlan-id"]["value"] = 120
    draft = validate_draft(value, evidence)
    assert draft["items"][0]["configuration"][1]["bindings"]["vlan-id"]["value"] == "120"
    line["bindings"]["vlan-id"]["value"] = 20
    line["cli"] = "vlan 20"
    with pytest.raises(GenerationError, match="not present in the user requirement"):
        validate_draft(value, evidence)


def test_empty_binding_is_omitted_only_when_source_syntax_does_not_need_it(
    contexts: dict[str, Any],
) -> None:
    evidence = build_evidence(contexts)
    value = response(evidence)
    line = value["items"][0]["verification"][0]
    line["bindings"]["vlan-name"] = {"value": "", "origin": "requirement"}
    draft = validate_draft(value, evidence)
    verified_line = draft["items"][0]["verification"][0]
    assert verified_line["cli"] == "show vlan id 120"
    assert set(verified_line["bindings"]) == {"vlan-id"}
    assert verified_line["omitted_bindings"] == [{
        "parameter": "vlan-name", "reason": "empty_binding_not_used_by_selected_syntax",
    }]
    line["bindings"].pop("vlan-id")
    line["cli"] = "show vlan name"
    with pytest.raises(GenerationError, match="vlan-name.*empty"):
        validate_draft(value, evidence)
    line["cli"] = "show vlan name <VLAN_NAME>"
    with pytest.raises(GenerationError, match="vlan-name.*empty"):
        validate_draft(value, evidence)


@pytest.mark.parametrize("invalid", [None, True, 120.0, "", [], {}])
def test_invalid_binding_value_identifies_command_and_parameter(
    contexts: dict[str, Any], invalid: Any,
) -> None:
    evidence = build_evidence(contexts)
    value = response(evidence)
    line = value["items"][0]["configuration"][1]
    line["bindings"]["vlan-id"]["value"] = invalid
    with pytest.raises(GenerationError) as error:
        validate_draft(value, evidence)
    assert f"Parameter value for {line['command_id']}/vlan-id" in str(error.value)


@pytest.mark.parametrize("binding", ["120", 120, None, [], True])
def test_shorthand_bindings_remain_invalid_with_specific_diagnostics(
    contexts: dict[str, Any], binding: Any,
) -> None:
    evidence = build_evidence(contexts)
    value = response(evidence)
    line = value["items"][0]["configuration"][1]
    line["bindings"]["vlan-id"] = binding
    with pytest.raises(GenerationError) as error:
        validate_draft(value, evidence)
    assert f"{line['command_id']}/vlan-id must be an object" in str(error.value)
    assert "item-1/configuration[2]" in str(error.value)


@pytest.mark.parametrize("group", ["templates", "verification_templates"])
def test_nested_template_commands_are_rejected_with_actionable_feedback(
    contexts: dict[str, Any], group: str,
) -> None:
    evidence = build_evidence(contexts)
    value = response(evidence)
    item = value["items"][0]
    item[group] = [{"commands": copy.deepcopy(item["templates"])}]
    with pytest.raises(GenerationError, match=rf"{group}\[1\]: Command arrays must be flat"):
        validate_draft(value, evidence)


@pytest.mark.parametrize(
    "change",
    [
        "unknown_command", "invented_cli", "invented_value", "unknown_parameter",
        "unbound_parameter", "multiline", "template_as_configuration",
        "unsupported_status", "missing_item", "duplicate_item", "unknown_item",
        "foreign_quote", "foreign_source", "foreign_item_command",
        "supported_with_missing_input", "empty_needs_input", "empty_partial",
        "not_found_with_cli", "conflict_without_citation",
    ],
)
def test_invalid_or_uncited_drafts_are_rejected(contexts: dict[str, Any], change: str) -> None:
    evidence = build_evidence(contexts)
    value = response(evidence)
    item = value["items"][0]
    line = item["configuration"][1]
    if change == "unknown_command":
        line["command_id"] = "unknown"
    elif change == "invented_cli":
        line["cli"] = "delete vlan 120"
    elif change == "invented_value":
        line["cli"] = "vlan 20"
        line["bindings"]["vlan-id"]["value"] = "20"
    elif change == "unknown_parameter":
        line["bindings"] = {"other": {"value": "120", "origin": "requirement"}}
    elif change == "unbound_parameter":
        line["cli"] = "vlan vlan-id"
        line["bindings"] = {}
    elif change == "multiline":
        line["cli"] = "vlan 120\nshutdown"
    elif change == "template_as_configuration":
        item["configuration"].append(item["templates"].pop())
    elif change == "unsupported_status":
        item["status"] = "complete"
    elif change == "missing_item":
        value["items"].pop()
    elif change == "duplicate_item":
        value["items"][1]["item_id"] = "item-1"
    elif change == "unknown_item":
        value["items"][1]["item_id"] = "item-3"
    elif change == "foreign_quote":
        item["evidence"][0]["citations"][0]["quote"] = "not in source"
    elif change == "foreign_source":
        item["evidence"][0]["citations"][0]["source_id"] = "source-999"
    elif change == "foreign_item_command":
        value["items"][1]["configuration"] = [line]
    elif change == "supported_with_missing_input":
        item["status"] = "supported"
    elif change == "empty_needs_input":
        item["missing_information"] = []
    elif change == "empty_partial":
        item.update(status="partial", configuration=[], verification=[], templates=[], evidence=[])
    elif change == "not_found_with_cli":
        item["status"] = "not_found"
    else:
        item["status"] = "conflict"
    with pytest.raises(GenerationError):
        validate_draft(value, evidence)


def test_optional_syntax_and_withheld_steps_are_supported(contexts: dict[str, Any]) -> None:
    evidence = build_evidence(contexts)
    value = response(evidence)
    item = value["items"][0]
    item["verification"][0]["cli"] += " brief"
    item["withheld_steps"] = [{
        "command_id": command_id(evidence, "end"), "reason": "Review duplicate source step.",
    }]
    draft = validate_draft(value, evidence)
    assert draft["items"][0]["verification"][0]["cli"] == "show vlan id 120 brief"
    assert draft["items"][0]["withheld_steps"][0]["citations"]


def test_optional_withheld_command_does_not_make_an_item_incomplete(contexts: dict[str, Any]) -> None:
    context = contexts["items"][0]["chapters"][0]["contexts"][0]
    context["markdown"] = context["markdown"].replace(
        "</table>",
        "<tr><td>Step 6</td><td><p>copy running-config startup-config</p></td>"
        "<td>(Optional) Save.</td></tr></table>",
    )
    evidence = build_evidence(contexts)
    value = response(evidence)
    item = value["items"][0]
    item.update(status="supported", missing_information=[], assumptions=[], templates=[])
    item["withheld_steps"] = [{
        "command_id": command_id(evidence, "copy"), "reason": "Saving was not requested.",
    }]
    validated = validate_draft(value, evidence)["items"][0]
    assert validated["status"] == "supported"
    assert validated["withheld_steps"][0]["optional"] is True
    item["withheld_steps"][0]["command_id"] = command_id(evidence, "end")
    with pytest.raises(GenerationError, match="Supported item"):
        validate_draft(value, evidence)


def test_supported_and_conflict_states_require_consistent_evidence(contexts: dict[str, Any]) -> None:
    evidence = build_evidence(contexts)
    value = response(evidence)
    item = value["items"][0]
    item.update(status="supported", missing_information=[], assumptions=[], templates=[])
    assert validate_draft(value, evidence)["items"][0]["status"] == "supported"
    item["status"] = "conflict"
    item["constraints"] = item["evidence"]
    assert validate_draft(value, evidence)["items"][0]["status"] == "conflict"


def test_template_retains_static_lines_in_the_same_ordered_block(contexts: dict[str, Any]) -> None:
    evidence = build_evidence(contexts)
    value = response(evidence)
    item = value["items"][0]
    item["templates"] = [
        item["configuration"][0], *item["templates"], item["configuration"][-1],
    ]
    item["configuration"] = []
    draft = validate_draft(value, evidence)
    assert [line["cli"] for line in draft["items"][0]["templates"]] == [
        "configure terminal", "interface <INTERFACE_ID>", "end",
    ]
    item["templates"].pop(1)
    with pytest.raises(GenerationError, match="template block"):
        validate_draft(value, evidence)


@pytest.mark.parametrize("foreign_command", [False, True])
def test_generation_scopes_evidence_and_rejects_cross_item_commands(
    contexts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    foreign_command: bool,
) -> None:
    contexts["items"][1]["chapters"] = copy.deepcopy(contexts["items"][0]["chapters"])
    contexts["items"][1]["chapters"][0]["document_id"] = "other-guide"
    path = tmp_path / "contexts.json"
    path.write_text(json.dumps(contexts))
    monkeypatch.setattr("configbrain.generate_config.expand_context", lambda *args: copy.deepcopy(contexts))
    evidence = build_evidence(contexts)
    calls = []

    class Response:
        status_code = 200

        def __init__(self, value: dict[str, Any]) -> None:
            self.value = value

        def json(self) -> dict[str, Any]:
            return {"model": "test-model", "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps(self.value)},
            }]}

    def post(url: str, **kwargs: Any) -> Response:
        assert kwargs["headers"] == {"api-key": "test-key"}
        assert kwargs["allow_redirects"] is False
        prompt = json.loads(kwargs["json"]["messages"][1]["content"])
        assert len(prompt["items"]) == 1
        item = prompt["items"][0]
        assert set(prompt["commands"]) == set(item["command_ids"])
        assert set(prompt["sources"]) == set(item["source_ids"])
        calls.append(item["item_id"])
        assert prompt["requirement"] == contexts["requirement"]
        assert "VLAN guide" in json.dumps(prompt)
        assert "/local/manual" not in json.dumps(prompt)
        assert "<table>" not in json.dumps(prompt)
        value = response(evidence)
        if item["item_id"] == "item-2":
            second = value["items"][1]
            second["status"] = "partial"
            second["configuration"] = [{
                "command_id": next(
                    cid for cid in item["command_ids"]
                    if evidence["commands"][cid]["syntax"] == "configure terminal"
                ),
                "cli": "configure terminal", "bindings": {},
            }]
            if foreign_command:
                second["configuration"][0]["command_id"] = command_id(evidence, "configure")
        value["items"] = [
            entry for entry in value["items"] if entry["item_id"] == item["item_id"]
        ]
        return Response(value)

    monkeypatch.setattr("configbrain.select_manuals.requests.post", post)
    args = (
        path, Path("search.json"), Path("catalog.json"), Path("okf"), Path("pdf"),
        CloudConfig("https://cloud.example.test/chat", "test-key", None),
    )
    if foreign_command:
        with pytest.raises(
            GenerationError,
            match=r"after one repair: item-2/configuration\[1\]: Command command-1.*outside",
        ):
            generate_config(*args)
        assert calls == ["item-1", "item-2", "item-2"]
        return
    result = generate_config(*args)
    assert calls == ["item-1", "item-2"]
    assert result["model"] == "test-model"
    assert result["item_models"] == {"item-1": "test-model", "item-2": "test-model"}
    assert [item["item_id"] for item in result["items"]] == ["item-1", "item-2"]
    assert result["next_checks"] == response(evidence)["next_checks"]
    assert result["validation_repairs"] == []
    assert not result["ready_to_apply"] and not result["configuration_complete"]
    assert result["pdf_verification_status"] == "not_verified"
    assert result["sources"]["source-1"]["manual_path"] == "/local/manual.md"
    assert len(result["contexts_sha256"]) == 64


def test_validation_failure_is_repaired_once_and_recorded_without_relaxing_checks(
    contexts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts["items"] = contexts["items"][:1]
    path = tmp_path / "contexts.json"
    path.write_text(json.dumps(contexts))
    monkeypatch.setattr("configbrain.generate_config.expand_context", lambda *args: contexts)
    evidence = build_evidence(contexts)
    valid = response(evidence)
    valid["items"] = valid["items"][:1]
    invalid = copy.deepcopy(valid)
    invalid["items"][0]["configuration"][1]["bindings"]["vlan-id"] = "120"
    invalid["items"][0]["status"] = "supported"
    calls = []

    def request(
        system_prompt: str, prompt: dict[str, Any], config: CloudConfig,
    ) -> tuple[dict[str, Any], str | None]:
        calls.append(copy.deepcopy(prompt))
        if len(calls) == 1:
            assert "validation_feedback" not in prompt
            return copy.deepcopy(invalid), "test-model"
        assert len(calls) == 2
        assert prompt["validation_feedback"]["previous_response"] == invalid
        assert "command-2/vlan-id must be an object" in prompt["validation_feedback"]["error"]
        assert {k: v for k, v in prompt.items() if k != "validation_feedback"} == calls[0]
        return copy.deepcopy(valid), "test-model"

    monkeypatch.setattr("configbrain.generate_config.request_json", request)
    result = generate_config(
        path, Path("search.json"), Path("catalog.json"), Path("okf"), Path("pdf"),
        CloudConfig("https://cloud.example.test/chat", "test-key", None),
    )
    assert len(calls) == 2
    assert result["items"] == validate_draft(valid, evidence)["items"]
    assert result["validation_repairs"] == [{
        "item_id": "item-1",
        "validation_error": calls[1]["validation_feedback"]["error"],
    }]
    invalid["items"][0]["configuration"][1]["bindings"]["vlan-id"] = {
        "value": "120", "origin": "requirement",
    }
    with pytest.raises(GenerationError, match="Supported item"):
        validate_draft(invalid, evidence)


def test_cloud_errors_do_not_trigger_validation_repair(
    contexts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "contexts.json"
    path.write_text(json.dumps(contexts))
    monkeypatch.setattr("configbrain.generate_config.expand_context", lambda *args: contexts)
    calls = []

    def request(*args: Any) -> tuple[dict[str, Any], str | None]:
        calls.append(args)
        raise SelectionError("LLM response was truncated.")

    monkeypatch.setattr("configbrain.generate_config.request_json", request)
    with pytest.raises(GenerationError, match="item-1.*truncated"):
        generate_config(
            path, Path("search.json"), Path("catalog.json"), Path("okf"), Path("pdf"),
            CloudConfig("https://cloud.example.test/chat", "test-key", None),
        )
    assert len(calls) == 1


def test_changed_contexts_fail_before_network(
    contexts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "contexts.json"
    modified = copy.deepcopy(contexts)
    modified["items"][0]["chapters"][0]["contexts"][0]["markdown"] += "\nchanged"
    path.write_text(json.dumps(modified))
    monkeypatch.setattr("configbrain.generate_config.expand_context", lambda *args: contexts)
    monkeypatch.setattr(
        "configbrain.generate_config.request_json",
        lambda *args: pytest.fail("No network request for altered evidence"),
    )
    with pytest.raises(GenerationError, match="differ"):
        generate_config(
            path, Path("search.json"), Path("catalog.json"), Path("okf"), Path("pdf"),
            CloudConfig("https://cloud.example.test/chat", "test-key", None),
        )


def test_cli_stdout_atomic_output_and_protected_inputs(
    contexts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    path = tmp_path / "contexts.json"
    path.write_text(json.dumps(contexts))
    monkeypatch.setattr("configbrain.generate_config.expand_context", lambda *args: contexts)
    monkeypatch.setattr(
        "configbrain.generate_config.load_cloud_config",
        lambda: CloudConfig("https://cloud.example.test/chat", "test-key", None),
    )
    evidence = build_evidence(contexts)

    def request(system_prompt: str, prompt: dict[str, Any], config: CloudConfig):
        value = response(evidence)
        value["items"] = [
            item for item in value["items"]
            if item["item_id"] == prompt["items"][0]["item_id"]
        ]
        return value, "test-model"

    monkeypatch.setattr(
        "configbrain.generate_config.request_json", request,
    )
    args = ["--contexts", str(path)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["purpose"] == "evaluation_only"
    output = tmp_path / "draft.json"
    assert main(args + ["--output", str(output)]) == 0
    saved = output.read_bytes()
    monkeypatch.setattr(
        "configbrain.generate_config.request_json",
        lambda system_prompt, prompt, config: (
            request(system_prompt, prompt, config)
            if prompt["items"][0]["item_id"] == "item-1"
            else ({"items": []}, None)
        ),
    )
    assert main(args + ["--output", str(output)]) == 1
    assert output.read_bytes() == saved
    assert "every input" in capsys.readouterr().err
    for protected in (path, tmp_path / "okf" / "manual.md", tmp_path / "pdf" / "manual.pdf"):
        assert main(args + ["--okf-root", str(tmp_path / "okf"),
                            "--source-pdf-root", str(tmp_path / "pdf"),
                            "--output", str(protected)]) == 1
