import csv
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts.config_generation_service import ConfigurationGenerationRequest
from scripts.parameter_sheet_demo import (
    FIELDS,
    SheetError,
    _index_context_warning,
    _svi_no_shutdown,
    audit_configuration_citations,
    check_configuration,
    generate_sheet_configuration,
    load_sheet,
    main,
)
from scripts.rag import SECTION_INDEX_VERSION, _has_explicit_dhcp_vrf_context, _preflight_requirement


def write_sheet(path: Path, **changes: str) -> None:
    row = {
        "product": "catalyst9300",
        "release": "26.x",
        "vlan_id": "120",
        "access_port": "GigabitEthernet1/0/20",
        "svi_ipv4_cidr": "192.0.2.1/24",
        "svi_vrf": "default",
        "dhcp_server": "198.51.100.50",
        "dhcp_server_vrf": "default",
        "ip_routing": "already_enabled",
        "service_dhcp": "already_enabled",
    }
    row.update(changes)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow(row)


CONFIGURATION = """\
vlan 120
interface GigabitEthernet1/0/20
 switchport mode access
 switchport access vlan 120
interface Vlan120
 ip address 192.0.2.1 255.255.255.0
 ip helper-address 198.51.100.50
 no shutdown
"""


def component_configurations(vrf: str = "default") -> list[str]:
    vrf_line = f" vrf forwarding {vrf}\n" if vrf != "default" else ""
    return [
        "vlan 120\nexit",
        "interface GigabitEthernet1/0/20\n switchport mode access\n"
        " switchport access vlan 120\nexit",
        "interface Vlan120\n" + vrf_line
        + " ip address 192.0.2.1 255.255.255.0\n no shutdown\nexit",
        "interface Vlan120\n ip helper-address 198.51.100.50\nexit",
    ]


class FakeService:
    def __init__(
        self, vrf: str = "default", *, invalid: bool = False,
        irrelevant: bool = False, failed_component: int | None = None,
    ) -> None:
        self.fragments = component_configurations(vrf)
        if invalid:
            self.fragments[3] = self.fragments[3].replace("198.51.100.50", "198.51.100.51")
        self.source_text = "\n".join(component_configurations(vrf))
        self.irrelevant = irrelevant
        self.failed_component = failed_component
        self.requests: list[ConfigurationGenerationRequest] = []

    def generate(self, request: ConfigurationGenerationRequest) -> SimpleNamespace:
        index = len(self.requests)
        self.requests.append(request)
        configuration = self.fragments[index]
        failed = index == self.failed_component
        if failed:
            configuration = ""
        result = {
            "configuration": configuration,
            "warnings": ["根拠不足"] if failed else [],
            "unsupported_requirements": ["根拠不足"] if failed else [],
            "explanation": "\n".join(
                f"CLI: {line.strip()} [guide:section-1]"
                for line in configuration.splitlines()
            ),
            "citations": ["guide:section-1"],
            "missing_required_values": [],
            "search_queries": [f"query-{index}"],
            "cli_terms": [f"term-{index}"],
        }
        evidence = SimpleNamespace(
            citation_id="guide:section-1",
            title="RSPAN Configuration Guide" if self.irrelevant else "Configuration Guide",
            heading_path=("Configuring VLAN and Interfaces",),
            text=self.source_text,
        )
        return SimpleNamespace(result=result, evidence=[evidence])


def test_sheet_to_requirement_and_configuration(tmp_path: Path) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path)
    sheet = load_sheet(path)

    requirement = sheet.requirement()
    assert "VLAN 120を新規作成" in requirement
    assert "GigabitEthernet1/0/20" in requirement
    assert "192.0.2.1/24" in requirement
    assert "198.51.100.50" in requirement
    assert "ip routingは既に有効" in requirement
    assert "デフォルトVRF" in requirement
    assert _preflight_requirement(requirement, []) == ([], [])
    assert not _has_explicit_dhcp_vrf_context(requirement)
    assert check_configuration(sheet, CONFIGURATION) == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"svi_ipv4_cidr": ""}, "svi_ipv4_cidr"),
        ({"svi_vrf": ""}, "svi_vrf"),
        ({"dhcp_server_vrf": ""}, "dhcp_server_vrf"),
        ({"svi_vrf": "Blue"}, "同一VRF"),
        ({"svi_vrf": "Blue", "dhcp_server_vrf": "blue"}, "同一VRF"),
        ({"svi_vrf": "Bad\nVRF", "dhcp_server_vrf": "Bad\nVRF"}, "svi_vrf"),
        ({"vlan_id": "4095"}, "vlan_id"),
        ({"vlan_id": "1"}, "vlan_id"),
        ({"vlan_id": "1002"}, "vlan_id"),
        ({"access_port": "GigabitEthernet1/0/20\nshutdown"}, "access_port"),
        ({"svi_ipv4_cidr": "192.0.2.0/24"}, "SVI"),
        ({"dhcp_server": "192.0.2.1"}, "DHCPサーバー"),
        ({"ip_routing": "unknown"}, "ip_routing"),
        ({"product": "catalyst9500"}, "対象"),
    ],
)
def test_invalid_sheet_is_rejected(
    tmp_path: Path, changes: dict[str, str], message: str
) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path, **changes)
    with pytest.raises(SheetError, match=message):
        load_sheet(path)


def test_rejects_multiple_rows(tmp_path: Path) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("catalyst9300,26.x,121,GigabitEthernet1/0/21,192.0.2.2/24,default,198.51.100.50,default,already_enabled,already_enabled\n")
    with pytest.raises(SheetError, match="データ1行"):
        load_sheet(path)


def test_configuration_guard_checks_context_and_unrequested_commands(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path)
    sheet = load_sheet(path)
    wrong = CONFIGURATION.replace(
        " ip helper-address 198.51.100.50",
        " ip helper-address global 198.51.100.50",
    )
    wrong += " shutdown\n"

    errors = check_configuration(sheet, wrong)
    assert any("global" in error for error in errors)
    assert any("shutdown" in error for error in errors)
    assert any("必要な設定" in error and "helper-address" in error for error in errors)


def test_explicit_global_configuration_is_required(tmp_path: Path) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path, ip_routing="configure", service_dhcp="configure")
    sheet = load_sheet(path)
    assert len(check_configuration(sheet, CONFIGURATION)) == 2
    assert check_configuration(
        sheet, "ip routing\nservice dhcp\n" + CONFIGURATION
    ) == []


@pytest.mark.parametrize("command", ["vrf forwarding Blue", "ip vrf forwarding Blue"])
def test_same_named_vrf_requires_assignment_before_ip(
    tmp_path: Path, command: str
) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path, svi_vrf="Blue", dhcp_server_vrf="Blue")
    sheet = load_sheet(path)
    requirement = sheet.requirement()
    assert "同一の既存VRF Blue" in requirement
    assert "VRFの定義は既に存在" in requirement
    assert _preflight_requirement(requirement, []) == ([], [])
    assert not _has_explicit_dhcp_vrf_context(requirement)
    configuration = CONFIGURATION.replace(
        "interface Vlan120\n", f"interface Vlan120\n {command}\n"
    )
    assert check_configuration(sheet, configuration) == []
    assert check_configuration(sheet, CONFIGURATION)
    assert check_configuration(sheet, configuration.replace("Blue", "blue"))
    late_assignment = CONFIGURATION.replace(
        " ip address 192.0.2.1 255.255.255.0",
        f" ip address 192.0.2.1 255.255.255.0\n {command}",
    )
    assert any("IPアドレスより先" in error for error in check_configuration(sheet, late_assignment))


def test_default_vrf_rejects_named_vrf_configuration(tmp_path: Path) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path)
    sheet = load_sheet(path)
    configuration = CONFIGURATION.replace(
        "interface Vlan120\n", "interface Vlan120\n vrf forwarding Blue\n"
    )
    assert any("vrf forwarding" in error for error in check_configuration(sheet, configuration))


@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize("vrf", ["default", "Blue"])
def test_main_checks_generated_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    valid: bool,
    vrf: str,
) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path, svi_vrf=vrf, dhcp_server_vrf=vrf)
    service = FakeService(vrf, invalid=not valid)
    monkeypatch.setattr(
        "scripts.config_generation_service.ConfigurationGenerationService",
        lambda **kwargs: service,
    )
    monkeypatch.setattr("scripts.rag.E5Embedder", lambda: object())
    monkeypatch.setattr("scripts.transform_markdown.load_config", lambda: object())
    report_path = tmp_path / "reports" / "result.md"
    monkeypatch.setattr(
        sys, "argv", ["parameter_sheet_demo", str(path), "--markdown-output", str(report_path)]
    )
    assert main() == 0
    output = json.loads(capsys.readouterr().out)
    assert len(service.requests) == 4
    assert all(request.knowledge_policy == "disallow" for request in service.requests)
    assert "192.0.2.1/24" in service.requests[2].requirement
    if vrf != "default":
        assert all(f"既存VRF {vrf}" in service.requests[index].requirement for index in (2, 3))
    assert bool(output["configuration"]) is valid
    assert output["citations"] == ["guide:section-1"]
    report = report_path.read_text(encoding="utf-8")
    assert "## 生成コンフィグ" in report
    assert "guide:section-1" in report
    config_block = report.split("## 生成コンフィグ\n\n```text\n", 1)[1].split(
        "\n```", 1
    )[0]
    assert ("ip helper-address 198.51.100.50" in config_block) is valid
    if valid and vrf != "default":
        assert f"vrf forwarding {vrf}" in config_block
    if valid:
        assert all(check["verified"] for check in output["citation_checks"])
        assert "## CLIごとの引用確認" in report
        assert config_block.count("interface Vlan120") == 1
        assert config_block.count("exit") == 3
    if not valid:
        assert output["warnings"]
        assert output["unsupported_requirements"]
        assert "パラメータシートと生成設定の不一致" in report


def test_citation_audit_requires_per_command_relevant_cited_evidence() -> None:
    config = "vlan 120\ninterface Vlan120\n ip address 192.0.2.1 255.255.255.0\n no shutdown\n"
    source = SimpleNamespace(
        citation_id="guide:1",
        title="Configuration Guide",
        heading_path=("Configuring VLANs",),
        text=(
            "vlan vlan-id\ninterface Vlan120\n"
            " ip address 192.0.2.1 255.255.255.0\n no shutdown\n"
        ),
    )
    explanation = "\n".join(f"CLI: {line.strip()} [guide:1]" for line in config.splitlines())
    checks = audit_configuration_citations(config, explanation, ["guide:1"], [source])
    assert all(check["verified"] for check in checks)

    unrelated = SimpleNamespace(
        citation_id="guide:2",
        title="BGP EVPN VXLAN Configuration Guide",
        heading_path=("Configuring VLANs",),
        text=source.text,
    )
    explanation = explanation.replace("CLI: vlan 120 [guide:1]", "CLI: vlan 120 [guide:2]")
    checks = audit_configuration_citations(
        config, explanation, ["guide:1", "guide:2"], [source, unrelated]
    )
    assert not checks[0]["verified"]
    assert checks[0]["rejected_citations"] == ["guide:2"]
    assert all(check["verified"] for check in checks[1:])
    mixed = audit_configuration_citations(
        "vlan 120",
        "CLI: vlan 120 [guide:1] [guide:2]",
        ["guide:1", "guide:2"],
        [source, unrelated],
    )
    assert mixed[0]["citations"] == ["guide:1"]
    assert mixed[0]["rejected_citations"] == ["guide:2"]
    assert not mixed[0]["verified"]


def test_citation_audit_rejects_missing_or_wrong_command_evidence() -> None:
    config = "vlan 120\n no shutdown\n"
    source = SimpleNamespace(
        citation_id="guide:1",
        title="Configuration Guide",
        heading_path=("Configuring VLANs",),
        text="show vlan 120\ninterface GigabitEthernet1/0/20\n no shutdown\n",
    )
    checks = audit_configuration_citations(
        config, "CLI: vlan 120 [guide:1]\nCLI: no shutdown [guide:1]", ["guide:1"], [source]
    )
    assert not any(check["verified"] for check in checks)
    missing = audit_configuration_citations(
        "vlan 120", "vlan 120", ["guide:1"], [source]
    )
    assert not missing[0]["verified"]
    assert missing[0]["citations"] == []
    not_same_command = audit_configuration_citations(
        "vlan 120",
        "CLI: switchport access vlan 120 [guide:1]",
        ["guide:1"],
        [source],
    )
    assert not_same_command[0]["citations"] == []


def test_citation_audit_recognizes_markdown_command_syntax() -> None:
    source = SimpleNamespace(
        citation_id="guide:1",
        title="IP Addressing Services",
        heading_path=("Specifying the Packet Forwarding Address",),
        text="Use the **ip helper-address** *address* interface command.",
    )
    checks = audit_configuration_citations(
        "ip helper-address 198.51.100.50",
        "CLI: ip helper-address 198.51.100.50 [guide:1]",
        ["guide:1"],
        [source],
    )
    assert checks[0]["verified"]


def test_citation_audit_recognizes_table_cell_interface_syntax() -> None:
    source = SimpleNamespace(
        citation_id="guide:1",
        title="IP Addressing Services",
        heading_path=("Specifying the Packet Forwarding Address",),
        text="| interface | interface-id | Configures a single physical port. |",
    )
    checks = audit_configuration_citations(
        "interface GigabitEthernet1/0/20",
        "CLI: interface GigabitEthernet1/0/20 [guide:1]",
        ["guide:1"],
        [source],
    )
    assert checks[0]["verified"]


def test_citation_audit_recognizes_exit_in_command_table() -> None:
    source = SimpleNamespace(
        citation_id="guide:1",
        title="VLAN Configuration Guide",
        heading_path=("Creating a VLAN",),
        text="| exit | Applies changes and exits VLAN configuration mode. |",
    )
    checks = audit_configuration_citations(
        "exit", "CLI: exit [guide:1]", ["guide:1"], [source]
    )
    assert checks[0]["verified"]
    source.text = "| do not exit | This operation must remain in configuration mode. |"
    assert not audit_configuration_citations(
        "exit", "CLI: exit [guide:1]", ["guide:1"], [source]
    )[0]["verified"]


def test_exit_citation_must_match_the_current_mode() -> None:
    source = SimpleNamespace(
        citation_id="guide:1",
        title="Configuration Guide",
        heading_path=("Configuring VLANs",),
        text="vlan vlan-id\nDevice(config-mdns)# exit",
    )
    checks = audit_configuration_citations(
        "vlan 120\nexit",
        "CLI: vlan 120 [guide:1]\nCLI: exit [guide:1]",
        ["guide:1"], [source],
    )
    assert checks[0]["verified"]
    assert not checks[1]["verified"]


def test_svi_shutdown_evidence_stops_at_exit() -> None:
    assert not _svi_no_shutdown(
        "interface Vlan120\nDevice(config-if)# exit\n"
        "Device(config-vlan)# no shutdown"
    )


def test_index_context_warning_detects_old_or_missing_manifest(tmp_path: Path) -> None:
    assert "確認できません" in _index_context_warning(tmp_path)
    manifest = tmp_path / "index-manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    assert "未反映" in _index_context_warning(tmp_path)
    manifest.write_text(
        json.dumps({"section_versions": {"catalyst9300": SECTION_INDEX_VERSION}}),
        encoding="utf-8",
    )
    assert _index_context_warning(tmp_path) == ""
    manifest.write_text("not json", encoding="utf-8")
    with pytest.raises(SheetError, match="読み取れません"):
        _index_context_warning(tmp_path)


def test_citation_audit_rejects_vlan_prose_without_config_syntax() -> None:
    source = SimpleNamespace(
        citation_id="guide:1",
        title="Configuration Guide",
        heading_path=("VLAN information",),
        text="Do not configure VLAN 120 in this environment.",
    )
    checks = audit_configuration_citations(
        "vlan 120", "CLI: vlan 120 [guide:1]", ["guide:1"], [source]
    )
    assert not checks[0]["verified"]


@pytest.mark.parametrize(
    ("command", "source_text"),
    [
        ("ip helper-address 198.51.100.50", "Device(config-if)# no ip helper-address 198.51.100.50"),
        ("ip address 192.0.2.1 255.255.255.0", "Device(config-if)# no ip address 192.0.2.1 255.255.255.0"),
        ("no shutdown", "no interface Vlan120\n no shutdown"),
    ],
)
def test_citation_audit_does_not_treat_negated_commands_as_evidence(
    command: str, source_text: str
) -> None:
    source = SimpleNamespace(
        citation_id="guide:1",
        title="Configuration Guide",
        heading_path=("Configuring interfaces",),
        text=source_text,
    )
    checks = audit_configuration_citations(
        command, f"CLI: {command} [guide:1]", ["guide:1"], [source]
    )
    assert not checks[0]["verified"]


def test_demo_abstains_when_cli_citation_is_irrelevant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path)
    service = FakeService(irrelevant=True)
    monkeypatch.setattr(
        "scripts.config_generation_service.ConfigurationGenerationService",
        lambda **kwargs: service,
    )
    monkeypatch.setattr("scripts.rag.E5Embedder", lambda: object())
    monkeypatch.setattr("scripts.transform_markdown.load_config", lambda: object())
    report_path = tmp_path / "report.md"
    monkeypatch.setattr(
        sys, "argv", ["parameter_sheet_demo", str(path), "--markdown-output", str(report_path)]
    )
    assert main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["configuration"] == ""
    assert "CLIごとの引用を確認できません" in output["unsupported_requirements"]
    assert any("guide:section-1" in check["rejected_citations"] for check in output["citation_checks"])
    assert "未確認" in report_path.read_text(encoding="utf-8")


def test_one_failed_component_abstains_and_retains_diagnostics(tmp_path: Path) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path)
    service = FakeService(failed_component=2)

    result = generate_sheet_configuration(load_sheet(path), service, 20)

    assert result["configuration"] == ""
    assert [part["status"] for part in result["component_results"]] == [
        "verified", "verified", "failed", "verified",
    ]
    assert "SVI: 根拠不足" in result["unsupported_requirements"]
    assert result["component_results"][2]["search_queries"] == ["query-2"]
    assert result["component_results"][0]["candidate_configuration"] == "vlan 120\nexit"
    assert len(service.requests) == 4


def test_merge_rejects_missing_exit_and_wrong_component(tmp_path: Path) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path)
    service = FakeService()
    service.fragments[0] = "vlan 120\ninterface Vlan120\nexit"

    result = generate_sheet_configuration(load_sheet(path), service, 20)

    assert result["configuration"] == ""
    assert result["component_results"][0]["status"] == "failed"
    assert any("exit" in warning for warning in result["component_results"][0]["warnings"])


def test_merge_preserves_required_global_configuration(tmp_path: Path) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path, ip_routing="configure", service_dhcp="configure")
    service = FakeService()
    service.fragments[2] = "ip routing\n" + service.fragments[2]
    service.fragments[3] = "service dhcp\n" + service.fragments[3]
    service.source_text += "\nip routing\nservice dhcp\n"

    result = generate_sheet_configuration(load_sheet(path), service, 20)

    assert result["configuration"].startswith("ip routing\nservice dhcp\n")
    assert check_configuration(load_sheet(path), result["configuration"]) == []


def test_merge_abstains_when_vrf_assignment_follows_ip(tmp_path: Path) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path, svi_vrf="Blue", dhcp_server_vrf="Blue")
    service = FakeService("Blue")
    service.fragments[2] = service.fragments[2].replace(
        " vrf forwarding Blue\n ip address 192.0.2.1 255.255.255.0",
        " ip address 192.0.2.1 255.255.255.0\n vrf forwarding Blue",
    )

    result = generate_sheet_configuration(load_sheet(path), service, 20)

    assert result["configuration"] == ""
    assert result["component_results"][2]["status"] == "failed"
    assert any("IPアドレスより先" in warning for warning in result["warnings"])


def test_validate_only_cannot_write_a_markdown_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path)
    monkeypatch.setattr(
        sys, "argv",
        ["parameter_sheet_demo", str(path), "--validate-only", "--markdown-output", "report.md"],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_markdown_output_cannot_overwrite_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "sheet.csv"
    write_sheet(path)
    original = path.read_text(encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv",
        ["parameter_sheet_demo", str(path), "--markdown-output", str(path)],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert path.read_text(encoding="utf-8") == original
