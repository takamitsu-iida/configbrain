import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from configbrain.generate_config import (
    GenerationError, _fulfillment_status, build_evidence, generate_config, main,
    validate_draft,
)
from configbrain.report import ReportError, render_report
from configbrain.select_manuals import CloudConfig, SelectionError


pytestmark = pytest.mark.usefixtures("mock_semantic_reviewer")


@pytest.fixture(autouse=True)
def skip_pdf_verification_for_generation_unit_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "configbrain.generate_config.verify_pdf_citations",
        lambda _draft, _evidence: None,
    )


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
        "requirement": "VLAN 120を作成し、GigabitEthernet1/0/1をアクセスポートにする",
        "selection_sha256": "a" * 64, "search_results_sha256": "b" * 64,
        "catalog_sha256": "c" * 64, "dataset_fingerprint": "d" * 64,
        "evidence_status": "candidates_only", "pdf_verification_status": "not_verified",
        "limitations": ["Original PDF content not verified."],
        "items": [
            {"requirement": "Create VLAN 120 and assign interface GigabitEthernet1/0/1 to it",
             "uncertainty": "Port unspecified.",
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


def evidence_id(evidence: dict[str, Any], text: str, item_id: str = "item-1") -> str:
    allowed = next(item for item in evidence["items"] if item["item_id"] == item_id)
    return next(
        atom_id for atom_id in allowed["evidence_ids"]
        if text in evidence["evidence_atoms"][atom_id]["text"]
    )


def response(evidence: dict[str, Any]) -> dict[str, Any]:
    def line(prefix: str, cli: str, bindings: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"command_id": command_id(evidence, prefix), "cli": cli, "bindings": bindings or {}}

    return {
        "ready_to_apply": True,
        "items": [
            {
                "item_id": "item-1", "depends_on": [], "status": "needs_input",
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
                    "evidence_ids": [evidence_id(evidence, "Create the VLAN.")],
                }],
                "constraints": [],
                "withheld_steps": [],
                "inference_candidates": [{
                    "description": "Review whether additional protection is needed.",
                    "conditions": ["Only when the VLAN is exposed to untrusted access."],
                    "assumptions": ["Requires separate evidence and topology review."],
                }],
            },
            {
                "item_id": "item-2", "depends_on": [], "status": "not_found",
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
    atom = evidence["evidence_atoms"][vlan["evidence_id"]]
    assert atom["text"] == vlan["row_text"]
    assert atom["locator"] == {
        "kind": "table_row", "table_index_in_context": 1,
        "row_index": 3, "section_path": ["Configuring VLANs", "Procedure"],
        "step_label": "Step 2",
    }
    assert "Headers: Step | Command or Action | Purpose" in atom["context_text"]
    assert vlan["citation"]["evidence_id"] == vlan["evidence_id"]
    assert vlan["citation"]["source_id"] == "source-1"
    assert "Create the VLAN." in evidence["evidence_atoms"][vlan["evidence_id"]][
        "pdf_match_fragments"
    ]
    assert evidence["sources"]["source-1"]["pdf_page_scope"] == "chapter_start"
    assert evidence["items"][1]["source_ids"] == []
    assert evidence["items"][1]["evidence_ids"] == []


def test_canonical_evidence_ids_are_stable_when_item_order_changes(
    contexts: dict[str, Any],
) -> None:
    second = copy.deepcopy(contexts["items"][0])
    second["requirement"] = "Create a related VLAN."
    chapter = second["chapters"][0]
    chapter.update(
        document_id="other-guide",
        document_title="Other VLAN guide",
        source_pdf_sha256="9" * 64,
        html_content_sha256="8" * 64,
        manual_sha256="7" * 64,
    )
    contexts["items"].append(second)
    original_ids = set(build_evidence(contexts)["evidence_atoms"])
    contexts["items"].reverse()
    reordered_ids = set(build_evidence(contexts)["evidence_atoms"])
    assert original_ids == reordered_ids
    assert all(
        re.fullmatch(r"evidence-v1-[0-9a-f]{64}", atom_id)
        for atom_id in original_ids
    )


def test_canonical_evidence_id_changes_with_source_revision(contexts: dict[str, Any]) -> None:
    first = build_evidence(contexts)
    original_id = first["commands"][command_id(first, "vlan ")]["evidence_id"]
    contexts["items"][0]["chapters"][0]["html_content_sha256"] = "0" * 64
    revised = build_evidence(contexts)
    revised_id = revised["commands"][command_id(revised, "vlan ")]["evidence_id"]
    assert revised_id != original_id


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
    contexts["items"][0]["requirement"] += f" IPv4 {requirement}"
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


def test_parameter_value_may_come_from_the_overall_requirement(
    contexts: dict[str, Any],
) -> None:
    contexts["requirement"] += " IPv4 192.0.2.1/24"
    context = contexts["items"][0]["chapters"][0]["contexts"][0]
    context["markdown"] = context["markdown"].replace(
        "</table>",
        "<tr><td>Step 6</td><td><p>ip address <var>ip-address subnet-mask</var>"
        "</p></td><td>Assign IPv4.</td></tr></table>",
    )
    evidence = build_evidence(contexts)
    value = response(evidence)
    value["items"][0]["configuration"].append({
        "command_id": command_id(evidence, "ip address"),
        "cli": "ip address 192.0.2.1 255.255.255.0",
        "bindings": {
            "ip-address": {"value": "192.0.2.1", "origin": "requirement"},
            "subnet-mask": {"value": "255.255.255.0", "origin": "requirement"},
        },
    })
    validated = validate_draft(value, evidence)["items"][0]
    assert validated["configuration"][-1]["cli"] == (
        "ip address 192.0.2.1 255.255.255.0"
    )


def test_show_command_in_configuration_is_reclassified_as_verification(
    contexts: dict[str, Any],
) -> None:
    evidence = build_evidence(contexts)
    value = response(evidence)
    show_command = copy.deepcopy(value["items"][0]["verification"][0])
    value["items"][0]["configuration"].append(show_command)

    item = validate_draft(value, evidence)["items"][0]
    assert all(
        not command["cli"].casefold().startswith("show")
        for command in item["configuration"]
    )
    assert item["verification"][0]["cli"] == "show vlan id 120"
    assert sum(
        command["command_id"] == show_command["command_id"]
        for command in item["verification"]
    ) == 1


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
    contexts["items"][0]["requirement"] += " FortyGigabitEthernet1/0/1"
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
    contexts["items"][0]["requirement"] += " FortyGigabitEthernet1/0/1"
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
    contexts["items"][0]["requirement"] += " VLAN name SALES"
    evidence = build_evidence(contexts)
    value = response(evidence)
    value["items"][0]["verification"][0].update(cli="show vlan name sales", bindings={
        "vlan-name": {"value": "sales", "origin": "requirement"},
    })
    with pytest.raises(GenerationError, match="vlan-name.*not present"):
        validate_draft(value, evidence)


def test_unknown_evidence_id_identifies_the_exact_finding(contexts: dict[str, Any]) -> None:
    evidence = build_evidence(contexts)
    value = response(evidence)
    value["items"][0]["evidence"][0]["evidence_ids"][0] = "evidence-v1-" + "0" * 64
    with pytest.raises(
        GenerationError,
        match=r"Evidence ID at item-1/evidence\[1\]/evidence_ids\[1\].*outside its requirement item",
    ):
        validate_draft(value, evidence)


@pytest.mark.parametrize(
    ("items", "warnings", "expected"),
    [
        ([{"status": "supported"}], [], "complete"),
        ([{"status": "partial"}], [], "incomplete"),
        ([{"status": "supported", "missing_information": ["Confirm state."]}], [], "incomplete"),
        ([{"status": "supported", "withheld_steps": [{"optional": False}]}], [], "incomplete"),
        ([{"status": None, "generation_error": "Response invalid."}], [], "undetermined"),
        ([{"status": "supported"}], [{"kind": "duplicate_command_owner"}], "incomplete"),
    ],
)
def test_fulfillment_status_requires_resolved_supported_items(
    items: list[dict[str, Any]], warnings: list[dict[str, Any]], expected: str,
) -> None:
    assert _fulfillment_status(items, warnings) == expected


def test_validation_retains_partial_commands_templates_and_all_items(contexts: dict[str, Any]) -> None:
    evidence = build_evidence(contexts)
    generated = response(evidence)
    draft = validate_draft(generated, evidence)
    first, unresolved = draft["items"]
    assert first["configuration"][1]["cli"] == "vlan 120"
    assert first["configuration"][1]["citations"][0]["step_label"] == "Step 2"
    evidence_id_used = generated["items"][0]["evidence"][0]["evidence_ids"][0]
    citation = first["evidence"][0]["citations"][0]
    assert citation["evidence_id"] == evidence_id_used
    assert citation["quote"] == evidence["evidence_atoms"][evidence_id_used]["text"]
    assert first["templates"][0]["unresolved_parameters"] == ["INTERFACE_ID"]
    assert first["inference_candidates"][0]["ready_to_apply"] is False
    assert unresolved["status"] == "not_found"
    assert "ready_to_apply" not in draft
    assert first["inference_candidates"][0]["conditions"] == [
        "Only when the VLAN is exposed to untrusted access.",
    ]


def test_partial_validation_keeps_valid_components_and_marks_status_unassessed(
    contexts: dict[str, Any],
) -> None:
    evidence = build_evidence(contexts)
    generated = response(evidence)
    item = generated["items"][0]
    item["configuration"].append({
        **item["configuration"][1],
        "cli": "delete vlan 120",
    })
    item["evidence"].append({
        "explanation": "Invalid evidence reference.",
        "evidence_ids": ["evidence-v1-" + "0" * 64],
    })

    draft = validate_draft(generated, evidence, preserve_valid_components=True)
    validated = draft["items"][0]

    assert validated["status"] is None
    assert [command["cli"] for command in validated["configuration"]] == [
        "configure terminal", "vlan 120", "end",
    ]
    assert [finding["explanation"] for finding in validated["evidence"]] == [
        "The procedure describes VLAN creation.",
    ]
    assert any("configuration[4]" in error for error in validated["validation_errors"])
    assert any("evidence[2]" in error for error in validated["validation_errors"])
    assert "delete vlan 120" not in json.dumps(validated)


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
        "unknown_evidence", "foreign_item_evidence", "foreign_item_command",
        "unknown_dependency", "self_dependency", "duplicate_dependency",
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
    elif change == "unknown_evidence":
        item["evidence"][0]["evidence_ids"][0] = "evidence-v1-" + "0" * 64
    elif change == "foreign_item_evidence":
        value["items"][1]["evidence"] = [{
            "explanation": "Evidence from another requirement item.",
            "evidence_ids": [evidence_id(evidence, "Create the VLAN.")],
        }]
    elif change == "foreign_item_command":
        value["items"][1]["configuration"] = [line]
    elif change == "unknown_dependency":
        item["depends_on"] = ["item-999"]
    elif change == "self_dependency":
        item["depends_on"] = ["item-1"]
    elif change == "duplicate_dependency":
        item["depends_on"] = ["item-2", "item-2"]
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


@pytest.mark.parametrize("configuration_exists", [False, True])
def test_static_only_template_is_kept_as_configuration_or_withheld(
    contexts: dict[str, Any], configuration_exists: bool,
) -> None:
    evidence = build_evidence(contexts)
    value = response(evidence)
    item = value["items"][0]
    item["templates"] = [{
        "command_id": command_id(evidence, "interface"),
        "cli": "interface GigabitEthernet1/0/1",
        "bindings": {
            "interface-id": {
                "value": "GigabitEthernet1/0/1", "origin": "requirement",
            },
        },
    }]
    if not configuration_exists:
        item["configuration"] = []

    validated = validate_draft(value, evidence)["items"][0]
    assert validated["templates"] == []
    if configuration_exists:
        assert not any(
            command["cli"] == "interface GigabitEthernet1/0/1"
            for command in validated["configuration"]
        )
        assert any(
            step["source_syntax"] == "interface interface-id"
            for step in validated["withheld_steps"]
        )
        assert any("実行順序" in text for text in validated["missing_information"])
    else:
        assert validated["configuration"][0]["cli"] == "interface GigabitEthernet1/0/1"


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


@pytest.mark.parametrize("enable_requested", [False, True])
def test_no_shutdown_is_withheld_unless_interface_enable_is_requested(
    contexts: dict[str, Any], enable_requested: bool,
) -> None:
    if enable_requested:
        contexts["requirement"] += " インターフェイスを有効化する"
        contexts["items"][0]["requirement"] += " インターフェイスを有効化する"
    context = contexts["items"][0]["chapters"][0]["contexts"][0]
    context["markdown"] = context["markdown"].replace(
        "</table>",
        "<tr><td>Step 6</td><td><p>no shutdown</p></td>"
        "<td>Enable the interface.</td></tr></table>",
    )
    evidence = build_evidence(contexts)
    value = response(evidence)
    no_shutdown_id = command_id(evidence, "no shutdown")
    value["items"][0]["configuration"].append({
        "command_id": no_shutdown_id, "cli": "no shutdown", "bindings": {},
    })

    item = validate_draft(value, evidence)["items"][0]
    in_configuration = any(
        command["cli"] == "no shutdown" for command in item["configuration"]
    )
    assert in_configuration is enable_requested
    if enable_requested:
        assert not item["withheld_steps"]
    else:
        assert item["status"] == "needs_input"
        assert any(
            step["command_id"] == no_shutdown_id for step in item["withheld_steps"]
        )
        assert any("no shutdown" in text for text in item["missing_information"])


@pytest.mark.parametrize("routing_disabled_documented", [False, True])
@pytest.mark.parametrize("forwarding_requested", [False, True])
def test_ip_address_assignment_separates_forwarding_scope(
    contexts: dict[str, Any], routing_disabled_documented: bool,
    forwarding_requested: bool,
) -> None:
    cidr = "192.0.2.1/24"
    contexts["requirement"] += f" ルーテッドポートにIPv4アドレス{cidr}を設定する"
    contexts["items"][0]["requirement"] += f" ルーテッドポートにIPv4アドレス{cidr}を設定する"
    if forwarding_requested:
        contexts["requirement"] += " IPパケットを転送できるようにする"
        contexts["items"][0]["requirement"] += " IPパケットを転送できるようにする"
    context = contexts["items"][0]["chapters"][0]["contexts"][0]
    context["markdown"] = context["markdown"].replace(
        "</table>",
        "<tr><td>Step 6</td><td><p>ip address "
        "<var>ip-address subnet-mask</var></p></td>"
        "<td>Assign IPv4.</td></tr></table>",
    )
    if routing_disabled_documented:
        routing_context = copy.deepcopy(context)
        routing_context.update(
            context_id="lines-13-15", chapter_line_start=13, chapter_line_end=15,
            manual_line_start=112, manual_line_end=114,
            markdown=(
                "<p>By default, IP routing is disabled on the device, and you must "
                "enable it before routing can take place.</p>"
            ),
        )
        contexts["items"][0]["chapters"][0]["contexts"].append(routing_context)

    evidence = build_evidence(contexts)
    value = response(evidence)
    value["items"] = value["items"][:1]
    value["items"][0].update(
        status="partial",
        configuration=[{
            "command_id": command_id(evidence, "ip address "),
            "cli": "ip address 192.0.2.1 255.255.255.0",
            "bindings": {
                "ip-address": {"value": "192.0.2.1", "origin": "requirement"},
                "subnet-mask": {"value": "255.255.255.0", "origin": "requirement"},
            },
        }],
        templates=[], verification=[], verification_templates=[],
        evidence=[{
            "explanation": "The procedure describes assigning an IPv4 address.",
            "evidence_ids": [evidence_id(evidence, "Assign IPv4.")],
        }],
        constraints=[], missing_information=["Confirm the interface administrative state."],
        assumptions=[], withheld_steps=[], inference_candidates=[],
    )

    validated = validate_draft(value, {**evidence, "items": evidence["items"][:1]})
    item = validated["items"][0]

    assert [command["cli"] for command in item["configuration"]] == [
        "ip address 192.0.2.1 255.255.255.0",
    ]
    assert not any("ip routing" in command["cli"] for command in item["configuration"])
    forwarding_checks = [
        check for check in validated["next_checks"]
        if "IPパケット転送" in check or "IP転送" in check
    ]
    routing_constraints = [
        finding for finding in item["constraints"] if "IPパケット転送は別" in finding["explanation"]
    ]
    forwarding_questions = [
        question for question in item["missing_information"] if "IPパケット転送まで" in question
    ]
    if routing_disabled_documented:
        assert routing_constraints
        assert "IP routing is disabled" in routing_constraints[0]["citations"][0]["quote"]
    else:
        assert not routing_constraints
    if forwarding_requested:
        assert forwarding_checks
        assert forwarding_questions
    elif routing_disabled_documented:
        assert forwarding_checks
        assert not forwarding_questions
    else:
        assert not forwarding_checks
        assert not forwarding_questions


@pytest.mark.parametrize("documented_impact", [False, True])
def test_no_switchport_requires_confirmation_for_cited_configuration_impact(
    contexts: dict[str, Any], documented_impact: bool,
) -> None:
    contexts["requirement"] = "FortyGigabitEthernet1/0/1をルーテッドポートにする"
    contexts["items"] = contexts["items"][:1]
    contexts["items"][0]["requirement"] = contexts["requirement"]
    context = contexts["items"][0]["chapters"][0]["contexts"][0]
    context["markdown"] = context["markdown"].replace(
        "</table>",
        "<tr><td>Step 6</td><td><p>no switchport</p></td>"
        "<td>Enter Layer 3 mode.</td></tr></table>",
    )
    if documented_impact:
        risk_context = copy.deepcopy(context)
        risk_context.update(
            context_id="lines-13-15", chapter_line_start=13, chapter_line_end=15,
            manual_line_start=112, manual_line_end=114,
            markdown=(
                "<p>Entering a no switchport command disables and then re-enables "
                "the interface. The previous configuration information related to "
                "the affected interface might be lost.</p>"
            ),
        )
        contexts["items"][0]["chapters"][0]["contexts"].append(risk_context)
    evidence = build_evidence(contexts)
    value = response(evidence)
    value["items"] = value["items"][:1]
    value["items"][0].update(
        status="supported", configuration=[{
            "command_id": command_id(evidence, "no switchport"),
            "cli": "no switchport", "bindings": {},
        }],
        templates=[], verification=[], verification_templates=[],
        evidence=[], constraints=[], missing_information=[], assumptions=[],
        withheld_steps=[], inference_candidates=[],
    )

    item = validate_draft(value, evidence)["items"][0]

    if documented_impact:
        assert item["status"] == "needs_input"
        assert any("既存設定が失われる可能性" in text for text in item["missing_information"])
        constraint = next(
            finding for finding in item["constraints"]
            if "既存設定が失われる可能性" in finding["explanation"]
        )
        assert "previous configuration information" in constraint["citations"][0]["quote"]
        assert [command["cli"] for command in item["configuration"]] == ["no switchport"]
    else:
        assert item["status"] == "supported"
        assert item["missing_information"] == []
        assert item["constraints"] == []


@pytest.mark.parametrize(
    ("interface_id", "expected_status"),
    [
        ("FortyGigabitEthernet1/0/1", "needs_input"),
        ("GigabitEthernet1/0/1", "supported"),
    ],
)
def test_forty_gigabit_port_requires_hardware_applicability_confirmation(
    contexts: dict[str, Any], interface_id: str, expected_status: str,
) -> None:
    contexts["requirement"] += f" {interface_id}"
    contexts["items"][0]["requirement"] += f" {interface_id}"
    evidence = build_evidence(contexts)
    value = response(evidence)
    item = value["items"][0]
    interface_line = item["templates"].pop()
    interface_line.update(
        cli=f"interface {interface_id}",
        bindings={"interface-id": {"value": interface_id, "origin": "requirement"}},
    )
    item["configuration"].insert(-1, interface_line)
    item.update(
        status="supported", templates=[], missing_information=[], assumptions=[],
        inference_candidates=[],
    )

    validated = validate_draft(value, evidence)["items"][0]

    assert validated["status"] == expected_status
    hardware_questions = [
        question for question in validated["missing_information"]
        if "型番・搭載モジュール" in question
    ]
    assert bool(hardware_questions) is (expected_status == "needs_input")
    if hardware_questions:
        assert interface_id in hardware_questions[0]
    assert any(line["cli"] == f"interface {interface_id}" for line in validated["configuration"])


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
    draft = validate_draft(value, evidence)
    assert draft["items"][0]["templates"] == []
    assert [line["cli"] for line in draft["items"][0]["configuration"]] == [
        "configure terminal", "end",
    ]


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
        assert [entry["item_id"] for entry in prompt["requirement_items"]] == [
            "item-1", "item-2",
        ]
        assert set(prompt["commands"]) == set(item["command_ids"])
        assert set(prompt["sources"]) == set(item["source_ids"])
        assert set(prompt["evidence_atoms"]) == set(item["evidence_ids"])
        assert all(
            "source_id" not in atom and atom["text"]
            for atom in prompt["evidence_atoms"].values()
        )
        assert prompt["normalized_ipv4_networks"] == evidence["normalized_ipv4_networks"]
        calls.append(item["item_id"])
        if item["item_id"] == "item-1":
            assert prompt["previous_items"] == []
        else:
            assert len(prompt["previous_items"]) == 1
            assert prompt["previous_items"][0]["item_id"] == "item-1"
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
        result = generate_config(*args, generation_rounds=2)
        assert calls == ["item-1", "item-2", "item-2"]
        assert result["generation_status"] == "partial"
        assert result["items"][0]["status"] == response(evidence)["items"][0]["status"]
        assert result["items"][1]["status"] is None
        assert result["items"][1]["configuration"] == []
        assert "outside its requirement item" in result["items"][1]["generation_error"]
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


def test_generation_and_report_keep_validated_components_after_partial_failure(
    contexts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "contexts.json"
    path.write_text(json.dumps(contexts))
    monkeypatch.setattr("configbrain.generate_config.expand_context", lambda *args: contexts)
    evidence = build_evidence(contexts)
    calls = []

    def request(
        system_prompt: str, prompt: dict[str, Any], config: CloudConfig,
    ) -> tuple[dict[str, Any], str]:
        item_id = prompt["items"][0]["item_id"]
        calls.append(item_id)
        value = response(evidence)
        item = next(candidate for candidate in value["items"] if candidate["item_id"] == item_id)
        if item_id == "item-1":
            if "validation_feedback" in prompt:
                item["configuration"] = [item["configuration"][0]]
            item["configuration"].append({
                **item["configuration"][-1],
                "cli": "delete vlan 120",
            })
        value["items"] = [item]
        return value, "test-model"

    monkeypatch.setattr("configbrain.generate_config.request_json", request)
    result = generate_config(
        path, Path("search.json"), Path("catalog.json"), Path("okf"), Path("pdf"),
        CloudConfig("https://cloud.example.test/chat", "test-key", None),
        generation_rounds=2,
    )
    item = result["items"][0]

    assert calls == ["item-1", "item-2", "item-1"]
    assert result["generation_status"] == "partial"
    assert item["status"] is None
    assert [command["cli"] for command in item["configuration"]] == [
        "configure terminal", "vlan 120", "end",
    ]
    report = render_report(result)
    assert "充足状態は判定されていません" in report
    assert "個別検証済み" in report
    assert "vlan 120" in report
    assert "delete vlan 120" not in report
    assert "成立条件: Only when the VLAN is exposed to untrusted access." in report


@pytest.mark.parametrize("conditions", [None, []])
def test_inference_candidate_requires_explicit_conditions(
    contexts: dict[str, Any], conditions: list[str] | None,
) -> None:
    evidence = build_evidence(contexts)
    value = response(evidence)
    inference = value["items"][0]["inference_candidates"][0]
    if conditions is None:
        inference.pop("conditions")
    else:
        inference["conditions"] = conditions

    with pytest.raises(GenerationError, match="conditions for applying"):
        validate_draft(value, evidence)


def test_generation_does_not_infer_ownership_from_sibling_command_overlap(
    contexts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts["requirement"] += " FortyGigabitEthernet1/0/1 IPv4 192.0.2.1/24"
    contexts["items"][0]["requirement"] += " FortyGigabitEthernet1/0/1 IPv4 192.0.2.1/24"
    contexts["items"][1]["requirement"] = (
        "Assign IPv4 address 192.0.2.1/24 to FortyGigabitEthernet1/0/1"
    )
    context = contexts["items"][0]["chapters"][0]["contexts"][0]
    context["markdown"] = context["markdown"].replace(
        "</table>",
        "<tr><td>Step 6</td><td><p>ip address <var>ip-address subnet-mask</var>"
        "</p></td><td>Assign IPv4.</td></tr></table>",
    )
    contexts["items"][1]["chapters"] = copy.deepcopy(contexts["items"][0]["chapters"])
    contexts["items"][1]["chapters"][0].update(
        document_id="other-guide",
        document_title="Other VLAN guide",
        source_pdf_sha256="9" * 64,
        html_content_sha256="8" * 64,
        manual_sha256="7" * 64,
    )
    contexts["items"][1]["chapters"][0]["document_id"] = "other-guide"
    path = tmp_path / "contexts.json"
    path.write_text(json.dumps(contexts))
    monkeypatch.setattr("configbrain.generate_config.expand_context", lambda *args: contexts)
    evidence = build_evidence(contexts)
    def request(
        system_prompt: str, prompt: dict[str, Any], config: CloudConfig,
    ) -> tuple[dict[str, Any], str]:
        item_ids = [item["item_id"] for item in prompt["items"]]
        value = response(evidence)
        if item_ids == ["item-1"]:
            value["items"][0]["configuration"].append({
                "command_id": command_id(evidence, "ip address"),
                "cli": "ip address 192.0.2.1 255.255.255.0",
                "bindings": {
                    "ip-address": {"value": "192.0.2.1", "origin": "requirement"},
                    "subnet-mask": {"value": "255.255.255.0", "origin": "requirement"},
                },
            })
        if len(item_ids) == 1:
            value["items"] = [
                entry for entry in value["items"] if entry["item_id"] == item_ids[0]
            ]
        return value, "test-model"

    monkeypatch.setattr("configbrain.generate_config.request_json", request)
    args = (
        path, Path("search.json"), Path("catalog.json"), Path("okf"), Path("pdf"),
        CloudConfig("https://cloud.example.test/chat", "test-key", None),
    )
    result = generate_config(*args, generation_rounds=1)
    assert result["items"][1]["status"] == "not_found"
    assert result["items"][1]["depends_on"] == []
    assert result["consistency_warnings"] == []
    address_node = next(
        node for node in result["requirement_graph"]["commands"]
        if node["cli"] == "ip address 192.0.2.1 255.255.255.0"
    )
    assert address_node["owner_item_id"] == "item-1"
    assert address_node["required_by"] == ["item-1"]


def test_generation_reports_duplicate_command_ownership(
    contexts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts["requirement"] += " IPv4 192.0.2.1/24"
    contexts["items"][1]["requirement"] = "Assign IPv4 address 192.0.2.1/24 to a routed port"
    context = contexts["items"][0]["chapters"][0]["contexts"][0]
    context["markdown"] = context["markdown"].replace(
        "</table>",
        "<tr><td>Step 6</td><td><p>ip address <var>ip-address subnet-mask</var>"
        "</p></td><td>Assign IPv4.</td></tr></table>",
    )
    contexts["items"][1]["chapters"] = copy.deepcopy(contexts["items"][0]["chapters"])
    path = tmp_path / "contexts.json"
    path.write_text(json.dumps(contexts))
    monkeypatch.setattr("configbrain.generate_config.expand_context", lambda *args: contexts)
    evidence = build_evidence(contexts)

    def request(
        system_prompt: str, prompt: dict[str, Any], config: CloudConfig,
    ) -> tuple[dict[str, Any], str]:
        value = response(evidence)
        item_id = prompt["items"][0]["item_id"]
        item = next(candidate for candidate in value["items"] if candidate["item_id"] == item_id)
        item_evidence = next(
            candidate for candidate in evidence["items"] if candidate["item_id"] == item_id
        )
        item.update(
            status="partial",
            assessment="The VLAN is configured; review the other requirement.",
            missing_information=["Confirm the target port."],
            templates=[], verification_templates=[],
            configuration=[{
                "command_id": next(
                    cid for cid in item_evidence["command_ids"]
                    if evidence["commands"][cid]["syntax"] == "vlan vlan-id"
                ),
                "cli": "vlan 120",
                "bindings": {"vlan-id": {"value": "120", "origin": "requirement"}},
            }],
        )
        value["items"] = [
            item for item in value["items"]
            if item["item_id"] == prompt["items"][0]["item_id"]
        ]
        return value, "test-model"

    monkeypatch.setattr("configbrain.generate_config.request_json", request)
    result = generate_config(
        path, Path("search.json"), Path("catalog.json"), Path("okf"), Path("pdf"),
        CloudConfig("https://cloud.example.test/chat", "test-key", None),
        generation_rounds=1,
    )
    assert result["generation_status"] == "partial"
    assert result["consistency_warnings"] == [{
        "kind": "duplicate_command_owner",
        "item_id": "item-1", "related_item_id": "item-2",
        "message": (
            "同じ設定コマンド「vlan 120」がitem-1とitem-2の両方に"
            "割り当てられています。依存関係と単一の所有項目を明示してください。"
        ),
    }]
    nodes = [
        node for node in result["requirement_graph"]["commands"]
        if node["cli"] == "vlan 120"
    ]
    assert [node["owner_item_id"] for node in nodes] == ["item-1", "item-2"]


def test_generation_rejects_dependency_claims_without_source_order_evidence(
    contexts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "contexts.json"
    path.write_text(json.dumps(contexts))
    monkeypatch.setattr("configbrain.generate_config.expand_context", lambda *args: contexts)
    evidence = build_evidence(contexts)

    def request(
        system_prompt: str, prompt: dict[str, Any], config: CloudConfig,
    ) -> tuple[dict[str, Any], str]:
        value = response(evidence)
        item_id = prompt["items"][0]["item_id"]
        item = next(candidate for candidate in value["items"] if candidate["item_id"] == item_id)
        item["depends_on"] = ["item-2" if item_id == "item-1" else "item-1"]
        value["items"] = [item]
        return value, "test-model"

    monkeypatch.setattr("configbrain.generate_config.request_json", request)
    result = generate_config(
        path, Path("search.json"), Path("catalog.json"), Path("okf"), Path("pdf"),
        CloudConfig("https://cloud.example.test/chat", "test-key", None),
        generation_rounds=1,
    )

    assert result["generation_status"] == "partial"
    assert "semantic_review_error" in result["items"][1]
    assert result["items"][0]["depends_on"] == []
    assert result["items"][1]["depends_on"] == []
    assert result["consistency_warnings"] == []
    assert result["dependency_adjustments"] == [
        {
            "item_id": "item-1",
            "claimed_depends_on": ["item-2"],
            "verified_depends_on": [],
            "reason": (
                "依存関係は同一手順表のCLI順序で根拠付けられる場合に限って採用しました。"
            ),
        },
        {
            "item_id": "item-2",
            "claimed_depends_on": ["item-1"],
            "verified_depends_on": [],
            "reason": (
                "依存関係は同一手順表のCLI順序で根拠付けられる場合に限って採用しました。"
            ),
        },
    ]


def test_validation_failure_is_repaired_and_recorded_without_relaxing_checks(
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
        generation_rounds=2,
    )
    assert len(calls) == 2
    assert result["generation_rounds"] == 2
    assert result["generation_rounds_used"] == 2
    assert result["items"] == validate_draft(valid, evidence)["items"]
    assert result["validation_repairs"] == [{
        "item_id": "item-1",
        "round": 2,
        "validation_error": "item-1/configuration[2]: Binding for command-2/vlan-id "
                            "must be an object with value and origin (received str).",
    }]
    invalid["items"][0]["configuration"][1]["bindings"]["vlan-id"] = {
        "value": "120", "origin": "requirement",
    }
    with pytest.raises(GenerationError, match="Supported item"):
        validate_draft(invalid, evidence)


def test_generation_retry_loop_uses_configured_total_rounds(
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
    ) -> tuple[dict[str, Any], str]:
        calls.append(copy.deepcopy(prompt))
        if len(calls) == 1:
            return copy.deepcopy(invalid), "test-model"
        assert prompt["validation_feedback"]["round"] == len(calls)
        if len(calls) == 2:
            progressed = copy.deepcopy(invalid)
            progressed["items"][0]["configuration"][1]["bindings"]["vlan-id"] = {
                "value": "20", "origin": "requirement",
            }
            return progressed, "test-model"
        return (
            copy.deepcopy(valid),
            "test-model",
        )

    monkeypatch.setattr("configbrain.generate_config.request_json", request)
    result = generate_config(
        path, Path("search.json"), Path("catalog.json"), Path("okf"), Path("pdf"),
        CloudConfig("https://cloud.example.test/chat", "test-key", None),
        generation_rounds=3,
    )
    assert len(calls) == 3
    assert result["generation_rounds"] == 3
    assert result["generation_rounds_used"] == 3
    assert result["generation_status"] == "complete"
    assert result["items"] == validate_draft(valid, evidence)["items"]
    assert result["validation_repairs"] == [{
        "item_id": "item-1", "round": 3,
        "validation_error": (
            "item-1/configuration[2]: Parameter value for command-2/vlan-id "
            "is not present in the user requirement."
        ),
    }]


@pytest.mark.parametrize(
    ("failure_kind", "expected_outcome"),
    [("validation", "no_progress"), ("api", "request_failed")],
)
def test_generation_stops_after_repeated_no_progress(
    contexts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    failure_kind: str, expected_outcome: str,
) -> None:
    contexts["items"] = contexts["items"][:1]
    path = tmp_path / "contexts.json"
    path.write_text(json.dumps(contexts))
    monkeypatch.setattr("configbrain.generate_config.expand_context", lambda *args: contexts)
    evidence = build_evidence(contexts)
    invalid = response(evidence)
    invalid["items"] = invalid["items"][:1]
    invalid["items"][0]["configuration"][1]["bindings"]["vlan-id"] = "120"
    calls = []

    def request(
        system_prompt: str, prompt: dict[str, Any], config: CloudConfig,
    ) -> tuple[dict[str, Any], str]:
        calls.append(copy.deepcopy(prompt))
        if failure_kind == "api":
            raise SelectionError("Repeated API failure.")
        return copy.deepcopy(invalid), "test-model"

    monkeypatch.setattr("configbrain.generate_config.request_json", request)
    result = generate_config(
        path, Path("search.json"), Path("catalog.json"), Path("okf"), Path("pdf"),
        CloudConfig("https://cloud.example.test/chat", "test-key", None),
        generation_rounds=4,
    )

    assert len(calls) == 2
    assert result["generation_rounds"] == 4
    assert result["generation_rounds_used"] == 2
    assert [request["outcome"] for request in result["generation_requests"]] == [
        "request_failed" if failure_kind == "api" else "issues_remain",
        expected_outcome,
    ]
    assert result["generation_requests"][1]["no_progress"] is True
    assert "item-1" in result["generation_requests"][1]["unresolved_issues"]
    report = render_report(result)
    assert "生成ラウンド・リクエスト履歴" in report
    assert (
        "API失敗・進展なしで停止" if failure_kind == "api"
        else "進展なし・再試行停止"
    ) in report
    assert "item-1" in report


def test_generation_retries_invalid_item_with_its_dependency_group(
    contexts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts["requirement"] = "Create VLAN 120, then return to EXEC."
    contexts["items"][0]["requirement"] = "Create VLAN 120."
    contexts["items"][1]["requirement"] = "Return to EXEC after creating the VLAN."
    contexts["items"][1]["chapters"] = copy.deepcopy(contexts["items"][0]["chapters"])
    path = tmp_path / "contexts.json"
    path.write_text(json.dumps(contexts))
    monkeypatch.setattr("configbrain.generate_config.expand_context", lambda *args: contexts)
    evidence = build_evidence(contexts)
    calls = []

    def request(
        system_prompt: str, prompt: dict[str, Any], config: CloudConfig,
    ) -> tuple[dict[str, Any], str]:
        item_ids = [item["item_id"] for item in prompt["items"]]
        calls.append(item_ids)
        value = response(evidence)
        first, second = value["items"]
        first["configuration"] = []
        if item_ids == ["item-2"]:
            second.update(status="partial", depends_on=["item-1"])
            second["configuration"] = [{
                "command_id": command_id(evidence, "end"),
                "cli": "end",
                "bindings": {},
            }, {
                "command_id": command_id(evidence, "vlan "),
                "cli": "delete vlan 120",
                "bindings": {"vlan-id": {"value": "120", "origin": "requirement"}},
            }]
        elif item_ids == ["item-1", "item-2"]:
            second.update(
                status="partial", depends_on=["item-1"],
                configuration=[{
                    "command_id": command_id(evidence, "end"),
                    "cli": "end", "bindings": {},
                }],
            )
        value["items"] = [first, second] if len(item_ids) == 2 else [
            first if item_ids == ["item-1"] else second
        ]
        return value, "test-model"

    monkeypatch.setattr("configbrain.generate_config.request_json", request)
    result = generate_config(
        path, Path("search.json"), Path("catalog.json"), Path("okf"), Path("pdf"),
        CloudConfig("https://cloud.example.test/chat", "test-key", None),
        generation_rounds=2,
    )

    assert calls == [["item-1"], ["item-2"], ["item-1", "item-2"]]
    assert result["generation_requests"][-1]["item_ids"] == ["item-1", "item-2"]
    assert result["items"][1]["status"] == "partial"
    assert [command["cli"] for command in result["items"][1]["configuration"]] == ["end"]
    report = render_report(result)
    assert "[item-1](#item-1), [item-2](#item-2)" in report


@pytest.mark.parametrize("failure_kind", ["api", "validation"])
def test_item_generation_failure_preserves_other_items(
    contexts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    path = tmp_path / "contexts.json"
    path.write_text(json.dumps(contexts))
    monkeypatch.setattr("configbrain.generate_config.expand_context", lambda *args: contexts)
    evidence = build_evidence(contexts)
    calls = []

    def request(
        system_prompt: str, prompt: dict[str, Any], config: CloudConfig,
    ) -> tuple[dict[str, Any], str]:
        item_id = prompt["items"][0]["item_id"]
        calls.append(item_id)
        if item_id == "item-1" and failure_kind == "api":
            raise SelectionError("LLM response was truncated.")
        value = response(evidence)
        if item_id == "item-1":
            value["items"][0]["configuration"][1]["bindings"]["vlan-id"] = "120"
        value["items"] = [
            item for item in value["items"] if item["item_id"] == item_id
        ]
        return value, "test-model"

    monkeypatch.setattr("configbrain.generate_config.request_json", request)
    result = generate_config(
        path, Path("search.json"), Path("catalog.json"), Path("okf"), Path("pdf"),
        CloudConfig("https://cloud.example.test/chat", "test-key", None),
        generation_rounds=2,
    )
    assert calls == ["item-1", "item-2", "item-1"]
    assert result["generation_status"] == "partial"
    assert result["fulfillment_status"] == "undetermined"
    assert result["generation_rounds_used"] == 2
    assert result["items"][0]["status"] is None
    assert [command["cli"] for command in result["items"][0]["configuration"]] == (
        [] if failure_kind == "api" else ["configure terminal", "end"]
    )
    expected_error = (
        "truncated" if failure_kind == "api" else "must be an object"
    )
    assert expected_error in result["items"][0]["generation_error"]
    assert result["items"][1]["status"] == "not_found"


@pytest.mark.parametrize(
    "reverse_model_dependency", [True, False], ids=["reversed", "expected"],
)
def test_demo6_requirement_graph_assigns_commands_and_dependencies(
    contexts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    reverse_model_dependency: bool,
) -> None:
    expected_contract = {
        "items": {
            "item-1": {
                "status": "needs_input",
                "depends_on": [],
                "configuration": [
                    "interface FortyGigabitEthernet1/0/1", "no switchport",
                ],
                "missing_information": [
                    "Confirm the current interface use before changing its mode.",
                    (
                        "実機の正確な型番・搭載モジュールと、"
                        "FortyGigabitEthernet1/0/1が実際に存在して設定可能かを確認してください。"
                    ),
                ],
            },
            "item-2": {
                "status": "partial",
                "depends_on": ["item-1"],
                "configuration": [
                    "interface FortyGigabitEthernet1/0/1",
                    "ip address 192.0.2.1 255.255.255.0",
                ],
                "withheld": ["no shutdown"],
            },
        },
        "graph": {
            "dependencies": [("item-2", "item-1")],
            "dependency_evidence": [{
                "dependency_item_id": "item-1",
                "relation": "procedure_order",
                "steps": ["Step 6", "Step 7"],
            }],
            "command_owners": {
                "no switchport": "item-1",
                "ip address 192.0.2.1 255.255.255.0": "item-2",
            },
            "shared_context": [
                ("interface FortyGigabitEthernet1/0/1", ["item-1", "item-2"]),
            ],
            "evidence_anchors": {
                "no switchport": "Step 6",
                "ip address 192.0.2.1 255.255.255.0": "Step 7",
            },
        },
        "consistency_warnings": [],
    }
    contexts["requirement"] = (
        "Catalyst 9300のFortyGigabitEthernet1/0/1をルーテッドポートにし、"
        "IPv4アドレス192.0.2.1/24を設定してください。"
    )
    contexts["items"] = contexts["items"][:1]
    contexts["items"][0]["requirement"] = (
        "FortyGigabitEthernet1/0/1をルーテッドポートにする"
    )
    second = copy.deepcopy(contexts["items"][0])
    second["requirement"] = (
        "FortyGigabitEthernet1/0/1にIPv4アドレス192.0.2.1/24を設定する"
    )
    contexts["items"].append(second)
    markdown = contexts["items"][0]["chapters"][0]["contexts"][0]["markdown"]
    contexts["items"][0]["chapters"][0]["contexts"][0]["markdown"] = markdown.replace(
        "</table>",
        "<tr><td>Step 6</td><td><p>no switchport</p></td>"
        "<td>Enter Layer 3 mode.</td></tr>"
        "<tr><td>Step 7</td><td><p>ip address <var>ip-address subnet-mask</var></p></td>"
        "<td>Assign IPv4.</td></tr>"
        "<tr><td>Step 8</td><td><p>no shutdown</p></td>"
        "<td>Enable interface.</td></tr></table>",
    )
    path = tmp_path / "demo6-contexts.json"
    path.write_text(json.dumps(contexts))
    monkeypatch.setattr("configbrain.generate_config.expand_context", lambda *args: contexts)
    evidence = build_evidence(contexts)
    evidence_items = {item["item_id"]: item for item in evidence["items"]}
    routed_evidence_id = evidence_id(evidence, "Enter Layer 3 mode.", "item-1")
    address_evidence_id = evidence_id(evidence, "Assign IPv4.", "item-2")

    def item_command(
        item_id: str, prefix: str, cli: str, bindings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        command = next(
            command_id
            for command_id in evidence_items[item_id]["command_ids"]
            if evidence["commands"][command_id]["syntax"].startswith(prefix)
        )
        return {"command_id": command, "cli": cli, "bindings": bindings or {}}

    def item_result(item_id: str) -> dict[str, Any]:
        if item_id == "item-1":
            return {
                "item_id": item_id,
                "depends_on": ["item-2"] if reverse_model_dependency else [],
                "status": "needs_input",
                "assessment": "Verify the existing port use before converting it.",
                "configuration": [
                    item_command(
                        item_id, "interface ", "interface FortyGigabitEthernet1/0/1",
                        {"interface-id": {
                            "value": "FortyGigabitEthernet1/0/1", "origin": "requirement",
                        }},
                    ),
                    item_command(item_id, "no switchport", "no switchport"),
                ],
                "templates": [], "verification": [], "verification_templates": [],
                "evidence": [{
                    "explanation": "The port can be converted to Layer 3 mode.",
                    "evidence_ids": [routed_evidence_id],
                }],
                "constraints": [], "missing_information": [
                    "Confirm the current interface use before changing its mode.",
                ],
                "assumptions": [], "withheld_steps": [], "inference_candidates": [],
            }

        return {
            "item_id": item_id,
            "depends_on": [] if reverse_model_dependency else ["item-1"],
            "status": "partial",
            "assessment": "The interface address is documented; operational prerequisites remain.",
            "configuration": [
                item_command(
                    item_id, "interface ", "interface FortyGigabitEthernet1/0/1",
                    {"interface-id": {
                        "value": "FortyGigabitEthernet1/0/1", "origin": "requirement",
                    }},
                ),
                item_command(
                    item_id, "ip address ", "ip address 192.0.2.1 255.255.255.0",
                    {
                        "ip-address": {"value": "192.0.2.1", "origin": "requirement"},
                        "subnet-mask": {"value": "255.255.255.0", "origin": "requirement"},
                    },
                ),
            ],
            "templates": [], "verification": [], "verification_templates": [],
            "evidence": [{
                "explanation": "The procedure describes assigning an IPv4 address.",
                "evidence_ids": [address_evidence_id],
            }],
            "constraints": [], "missing_information": [
                "Confirm whether the interface should be enabled.",
            ],
            "assumptions": [],
            "withheld_steps": [{
                "command_id": next(
                    command_id
                    for command_id in evidence_items[item_id]["command_ids"]
                    if evidence["commands"][command_id]["syntax"] == "no shutdown"
                ),
                "reason": "The requirement does not request enabling the interface.",
            }],
            "inference_candidates": [],
        }

    request_groups = []

    def request(
        system_prompt: str, prompt: dict[str, Any], config: CloudConfig,
    ) -> tuple[dict[str, Any], str]:
        item_ids = [item["item_id"] for item in prompt["items"]]
        request_groups.append(item_ids)
        return {
            "items": [item_result(item_id) for item_id in item_ids],
            "next_checks": [],
        }, "test-model"

    monkeypatch.setattr("configbrain.generate_config.request_json", request)
    result = generate_config(
        path, Path("search.json"), Path("catalog.json"), Path("okf"), Path("pdf"),
        CloudConfig("https://cloud.example.test/chat", "test-key", None),
        generation_rounds=1,
    )

    assert result["generation_rounds_used"] == 1
    assert result["generation_status"] == "complete"
    assert [item["item_id"] for item in result["items"]] == ["item-1", "item-2"]
    actual_items = {
        item["item_id"]: {
            "status": item["status"],
            "depends_on": item["depends_on"],
            "configuration": [command["cli"] for command in item["configuration"]],
            **(
                {"missing_information": item["missing_information"]}
                if item["item_id"] == "item-1" else {}
            ),
            **(
                {"withheld": [step["source_syntax"] for step in item["withheld_steps"]]}
                if item["item_id"] == "item-2" else {}
            ),
        }
        for item in result["items"]
    }
    assert all(
        command["cli"] != "no shutdown"
        for item in result["items"] for command in item["configuration"]
    )
    no_shutdown_id = next(
        command_id
        for command_id in evidence_items["item-2"]["command_ids"]
        if evidence["commands"][command_id]["syntax"] == "no shutdown"
    )
    assert any(
        step["command_id"] == no_shutdown_id
        for step in result["items"][1]["withheld_steps"]
    )
    graph = result["requirement_graph"]
    actual_graph = {
        "dependencies": [
            (edge["from"], edge["to"])
            for edge in graph["edges"] if edge["kind"] == "depends_on"
        ],
        "dependency_evidence": [
            {
                "dependency_item_id": proof["dependency_item_id"],
                "relation": proof["relation"],
                "steps": [
                    citation["step_label"] for citation in proof["citations"]
                ],
            }
            for proof in result["items"][1]["dependency_evidence"]
        ],
        "command_owners": {
            node["cli"]: node["owner_item_id"]
            for node in graph["commands"]
            if node["role"] == "configuration"
        },
        "shared_context": [
            (node["cli"], node["required_by"]) for node in graph["commands"]
            if node["role"] == "shared_context"
        ],
        "evidence_anchors": {
            node["cli"]: evidence["evidence_atoms"][node["evidence_ids"][0]]["locator"][
                "step_label"
            ]
            for node in graph["commands"]
            if node["cli"] in (
                "no switchport", "ip address 192.0.2.1 255.255.255.0",
            )
        },
    }
    assert actual_items == expected_contract["items"]
    assert actual_graph == expected_contract["graph"]
    assert result["consistency_warnings"] == expected_contract["consistency_warnings"]
    assert result["dependency_adjustments"] == (
        [{
            "item_id": "item-1",
            "claimed_depends_on": ["item-2"],
            "verified_depends_on": [],
            "reason": (
                "依存関係は同一手順表のCLI順序で根拠付けられる場合に限って採用しました。"
            ),
        }, {
            "item_id": "item-2",
            "claimed_depends_on": [],
            "verified_depends_on": ["item-1"],
            "reason": (
                "依存関係は同一手順表のCLI順序で根拠付けられる場合に限って採用しました。"
            ),
        }]
        if reverse_model_dependency else []
    )
    assert request_groups == [["item-1"], ["item-2"]]
    assert [
        request["outcome"] for request in result["generation_requests"]
    ] == ["resolved", "resolved"]

    output_dir = tmp_path / "demo6-acceptance"
    output_dir.mkdir()
    draft_path = output_dir / "config-draft.json"
    draft_path.write_text(json.dumps(result))
    report = render_report(json.loads(draft_path.read_bytes()), pipeline_stages={
        "selection": "completed", "search": "completed", "contexts": "completed",
        "generation": "completed", "report": "completed",
    })
    (output_dir / "report.md").write_text(report)
    assert "生成ラウンド・リクエスト履歴" in report
    assert "### 要件の前提" in report
    assert "要件充足判定: 未完了（追加情報・未充足項目あり） (`incomplete`)" in report
    assert "[item-2](#item-2) は [item-1](#item-1) に依存します。" in report
    assert "手順順序の根拠" in report
    if reverse_model_dependency:
        assert "依存関係の検証" in report
    invalid_proof = copy.deepcopy(result)
    invalid_proof["items"][1]["dependency_evidence"][0]["citations"].reverse()
    invalid_proof["requirement_graph"]["requirements"][1]["dependency_evidence"] = (
        invalid_proof["items"][1]["dependency_evidence"]
    )
    with pytest.raises(ReportError, match="procedure order"):
        render_report(invalid_proof)
    assert result["fulfillment_status"] == "incomplete"
    assert "no switchport" in report and "ip address 192.0.2.1 255.255.255.0" in report
    assert "no shutdown" in report
    assert "項目間の整合性警告" not in report


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
    args = ["--contexts", str(path), "--generation-rounds", "1"]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["purpose"] == "evaluation_only"
    assert result["generation_rounds"] == result["generation_rounds_used"] == 1
    output = tmp_path / "draft.json"
    assert main(args + ["--output", str(output)]) == 0
    saved = output.read_bytes()
    path.write_text("{}")
    assert main(args + ["--output", str(output)]) == 1
    assert output.read_bytes() == saved
    assert "Contexts differ" in capsys.readouterr().err
    for protected in (
        path, tmp_path / "okf" / "manual.md", tmp_path / "pdf" / "manual.pdf",
        tmp_path / "references" / "commands.json",
    ):
        assert main(args + ["--okf-root", str(tmp_path / "okf"),
                            "--source-pdf-root", str(tmp_path / "pdf"),
                            "--reference-root", str(tmp_path / "references"),
                            "--output", str(protected)]) == 1
