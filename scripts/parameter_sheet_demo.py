"""Generate a reviewed configuration proposal from a fixed-format CSV sheet."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Protocol, Sequence

if __package__:
    from .config_generation_service import (
        ConfigurationGenerationOutcome,
        ConfigurationGenerationRequest,
    )
else:
    from config_generation_service import (
        ConfigurationGenerationOutcome,
        ConfigurationGenerationRequest,
    )


FIELDS = (
    "product",
    "release",
    "vlan_id",
    "access_port",
    "svi_ipv4_cidr",
    "svi_vrf",
    "dhcp_server",
    "dhcp_server_vrf",
    "ip_routing",
    "service_dhcp",
)
STATES = {"already_enabled", "configure"}


class SheetError(ValueError):
    """Raised when the demo sheet cannot be used safely."""


class CitationEvidence(Protocol):
    citation_id: str
    title: str
    heading_path: tuple[str, ...]
    text: str


class GenerationService(Protocol):
    def generate(self, request: ConfigurationGenerationRequest) -> ConfigurationGenerationOutcome: ...


@dataclass(frozen=True)
class SheetComponent:
    name: str
    requirement: str


@dataclass(frozen=True)
class AccessSheet:
    vlan_id: int
    access_port: str
    svi: ipaddress.IPv4Interface
    vrf: str
    dhcp_server: ipaddress.IPv4Address
    ip_routing: str
    service_dhcp: str

    def requirement(self) -> str:
        def action(state: str, command: str) -> str:
            if state == "configure":
                return f"{command}を設定してください。"
            return f"{command}は既に有効なので設定を追加しないでください。"

        vrf_requirement = (
            "SVIとDHCPサーバーはデフォルトVRFを使用してください。"
            "SVIにVRF割り当ては設定しないでください。"
            if self.vrf == "default"
            else (
                f"SVIとDHCPサーバーは同一の既存VRF {self.vrf}を使用してください。"
                "SVIのVRF割り当てをIPアドレスより先に設定してください。"
                "VRFの定義は既に存在するため作成しないでください。"
            )
        )
        return (
            f"VLAN {self.vlan_id}を新規作成し、{self.access_port}を"
            f"VLAN {self.vlan_id}のアクセスポートにしてください。"
            f"SVI Vlan{self.vlan_id}のIPアドレスは{self.svi}とし、"
            "SVIを有効化してください。"
            f"Vlan{self.vlan_id}のDHCP要求を{self.dhcp_server}へ転送してください。"
            f"{action(self.ip_routing, 'ip routing')}"
            f"{action(self.service_dhcp, 'service dhcp')}"
            f"{vrf_requirement}"
            "指定されていないオプションは設定しないでください。"
            "説明には各設定CLIについて「CLI: 設定CLI行 [根拠ID]」の形式を"
            "1行ずつ記載してください。各行には対応する根拠だけを記載してください。"
        )

    def components(self) -> tuple[SheetComponent, ...]:
        vrf = (
            "SVIとDHCPサーバーはデフォルトVRFを使用し、VRF割り当ては設定しません。"
            if self.vrf == "default"
            else (
                f"SVIとDHCPサーバーは同一の既存VRF {self.vrf}を使用します。"
                "VRFの定義は既に存在するため作成しません。"
            )
        )
        routing = (
            "ip routingを設定してください。"
            if self.ip_routing == "configure"
            else "ip routingは既に有効なので追加しないでください。"
        )
        dhcp = (
            "service dhcpを設定してください。"
            if self.service_dhcp == "configure"
            else "service dhcpは既に有効なので追加しないでください。"
        )
        instructions = (
            "他の要件は別途設定するので、指定された設定だけを出力してください。"
            "入ったVLANまたはインターフェース設定モードをexitで終了してください。"
            "各CLI行について「CLI: 設定CLI行 [根拠ID]」の形式を1行ずつ記載し、"
            "exitを含む各行に対応する根拠だけを示してください。"
        )
        return (
            SheetComponent("VLAN", f"VLAN {self.vlan_id}を新規作成してください。" + instructions),
            SheetComponent(
                "アクセスポート",
                f"{self.access_port}をVLAN {self.vlan_id}のアクセスポートにしてください。"
                + instructions,
            ),
            SheetComponent(
                "SVI",
                f"SVI Vlan{self.vlan_id}のIPアドレスは{self.svi}とし、SVIを有効化してください。"
                + vrf + routing
                + ("SVIのVRF割り当てをIPアドレスより先に設定してください。" if self.vrf != "default" else "")
                + instructions,
            ),
            SheetComponent(
                "DHCPリレー",
                f"SVI Vlan{self.vlan_id}のDHCP要求を{self.dhcp_server}へ転送してください。"
                "SVIの作成とIPアドレスは別途設定します。" + vrf + dhcp + instructions,
            ),
        )


def load_sheet(path: Path) -> AccessSheet:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            if reader.fieldnames != list(FIELDS):
                raise SheetError("CSVのヘッダーはデモ用の項目と順序に一致させてください")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as error:
        raise SheetError(f"CSVを読み取れません: {error}") from error
    if len(rows) != 1 or None in rows[0]:
        raise SheetError("CSVにはヘッダーと、列数が一致するデータ1行が必要です")
    return validate_sheet_values(rows[0])


def validate_sheet_values(row: Mapping[str, str | None]) -> AccessSheet:
    values = {field: (row.get(field) or "").strip() for field in FIELDS}
    missing = [field for field in FIELDS if not values[field]]
    if missing:
        raise SheetError("必須値が不足しています: " + ", ".join(missing))
    if values["product"] != "catalyst9300" or values["release"] != "26.x":
        raise SheetError("このデモの対象はcatalyst9300 / 26.xのみです")
    if not re.fullmatch(r"[0-9]+", values["vlan_id"]):
        raise SheetError("vlan_idは2〜1001または1006〜4094の整数で指定してください")
    vlan_id = int(values["vlan_id"])
    if not (2 <= vlan_id <= 1001 or 1006 <= vlan_id <= 4094):
        raise SheetError("vlan_idは2〜1001または1006〜4094の整数で指定してください")
    port = values["access_port"]
    if not re.fullmatch(r"GigabitEthernet[0-9]+/[0-9]+/[0-9]+", port):
        raise SheetError("access_portはGigabitEthernet1/0/20形式で指定してください")
    for field in ("svi_vrf", "dhcp_server_vrf"):
        vrf = values[field]
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", vrf)
            or (vrf.lower() == "default" and vrf != "default")
        ):
            raise SheetError(f"{field}にはdefaultまたは有効なVRF名を指定してください")
    if values["svi_vrf"] != values["dhcp_server_vrf"]:
        raise SheetError("このデモはSVIとDHCPサーバーが同一VRFの場合のみ対応します")
    try:
        if "/" not in values["svi_ipv4_cidr"]:
            raise ValueError("prefix is required")
        svi = ipaddress.IPv4Interface(values["svi_ipv4_cidr"])
        server = ipaddress.IPv4Address(values["dhcp_server"])
    except ValueError as error:
        raise SheetError("SVIのIPv4アドレス/プレフィックスまたはDHCPサーバーが不正です") from error
    if (
        svi.network.prefixlen > 30
        or svi.ip in {svi.network.network_address, svi.network.broadcast_address}
        or svi.ip.is_unspecified
        or svi.ip.is_multicast
        or svi.ip.is_loopback
    ):
        raise SheetError("SVIには利用可能なユニキャストIPv4アドレスを指定してください")
    if (
        server.is_unspecified
        or server.is_multicast
        or server.is_loopback
        or server == ipaddress.IPv4Address("255.255.255.255")
        or server == svi.ip
        or (
            server in svi.network
            and server in {svi.network.network_address, svi.network.broadcast_address}
        )
    ):
        raise SheetError("DHCPサーバーには利用可能な別のユニキャストIPv4アドレスを指定してください")
    for field in ("ip_routing", "service_dhcp"):
        if values[field] not in STATES:
            raise SheetError(f"{field}はalready_enabledまたはconfigureを指定してください")
    return AccessSheet(
        vlan_id, port, svi, values["svi_vrf"], server,
        values["ip_routing"], values["service_dhcp"]
    )


def check_configuration(sheet: AccessSheet, configuration: str) -> list[str]:
    vlan = f"vlan {sheet.vlan_id}"
    port = f"interface {sheet.access_port}".lower()
    svi = f"interface vlan{sheet.vlan_id}"
    required = {
        ("global", vlan),
        ("global", port),
        ("global", svi),
        (port, "switchport mode access"),
        (port, f"switchport access vlan {sheet.vlan_id}"),
        (svi, f"ip address {sheet.svi.ip} {sheet.svi.netmask}"),
        (svi, f"ip helper-address {sheet.dhcp_server}"),
        (svi, "no shutdown"),
    }
    if sheet.ip_routing == "configure":
        required.add(("global", "ip routing"))
    if sheet.service_dhcp == "configure":
        required.add(("global", "service dhcp"))
    if sheet.vrf != "default":
        required.add((svi, f"vrf forwarding {sheet.vrf}"))
    allowed = required | {(port, "switchport")}
    found: set[tuple[str, str]] = set()
    unexpected: list[str] = []
    ordering_errors: list[str] = []
    context = "global"
    svi_address_seen = False
    for line in configuration.splitlines():
        raw_command = " ".join(line.split())
        command = raw_command.lower()
        if not command or command == "!":
            continue
        if command == "exit":
            context = "global"
            continue
        vrf_match = re.fullmatch(r"(?:ip )?vrf forwarding (\S+)", raw_command, re.I)
        if vrf_match:
            command = f"vrf forwarding {vrf_match.group(1)}"
            if context == svi and svi_address_seen:
                ordering_errors.append("SVIのVRF割り当てはIPアドレスより先に設定してください")
        if context == svi and command.startswith("ip address "):
            svi_address_seen = True
        if command.startswith(("interface ", "vlan ")):
            context = "global"
            item = (context, command)
            context = command
        else:
            item = (context, command)
        if item not in allowed:
            unexpected.append(line.strip())
        else:
            found.add(item)
    errors = [f"未指定または対象外の設定: {line}" for line in unexpected]
    errors.extend(ordering_errors)
    errors.extend(
        f"必要な設定がありません ({context}): {command}"
        for context, command in sorted(required - found)
    )
    return errors


UNRELATED_EVIDENCE_PATTERN = re.compile(
    r"\brspan\b|\bevpn\b|\bvxlan\b|private\s+vlan|"
    r"protocol\s+tunneling|interface\s+templates?|"
    r"feature\s+history|\bverifying\b|\bsubinterfaces?\b|"
    r"\bmacsec\b|\btrustsec\b|\bsgt\b|\bbonjour\b|\bmpls\b|"
    r"ipv6\s+neighbor\s+discovery\s+proxy",
    re.IGNORECASE,
)


def _source_has_command(text: str, pattern: str) -> bool:
    for line in text.splitlines():
        line = line.replace("`", "").replace("*", "").replace("|", " ")
        match = re.search(pattern, line, re.IGNORECASE)
        if match and not re.search(
            r"\b(?:show|clear|debug|ping|traceroute|never|avoid|not)\b|"
            r"\bno\s*$",
            line[:match.start()],
            re.IGNORECASE,
        ):
            return True
    return False


def _svi_no_shutdown(text: str) -> bool:
    in_svi = False
    for line in text.splitlines():
        line = line.replace("`", "").replace("*", "")
        if re.search(r"\(config-if\)#\s*exit\b|^\s*exit\s*$", line, re.I):
            in_svi = False
            continue
        interface = re.search(r"\binterface\s+([a-z][a-z0-9/-]*)", line, re.I)
        if interface:
            in_svi = (
                interface.group(1).lower().startswith("vlan")
                and _source_has_command(line, r"\binterface\s+[a-z][a-z0-9/-]*")
            )
        if in_svi and _source_has_command(line, r"\bno\s+shutdown\b"):
            return True
    return False


def _supports_exit(text: str, mode: str = "", context: str = "") -> bool:
    has_interface = bool(re.search(r"\binterface\s+", text, re.IGNORECASE))
    for line in text.splitlines():
        cleaned = line.replace("`", "").replace("*", "").strip()
        if mode.startswith("vlan "):
            if re.search(r"\(config-vlan\)#\s*exit\b", cleaned, re.IGNORECASE):
                return True
            if not re.search(r"\bvlan", context, re.IGNORECASE):
                continue
        elif mode.startswith("interface "):
            if re.search(r"\(config-if\)#\s*exit\b", cleaned, re.IGNORECASE):
                return True
            if not has_interface:
                continue
        if mode and re.search(r"\([^)]+\)#", cleaned):
            continue
        if cleaned.startswith("|"):
            cells = [cell.strip() for cell in cleaned.split("|") if cell.strip()]
            if cells and cells[0].lower() == "exit":
                return True
        elif re.fullmatch(
            r"(?:[a-z0-9._/-]+\([^)]+\)#\s*)?exit", cleaned, re.IGNORECASE
        ):
            return True
    return False


def _evidence_supports_command(
    command: str, evidence: CitationEvidence, mode: str = ""
) -> bool:
    context = " ".join((evidence.title, *evidence.heading_path))
    if UNRELATED_EVIDENCE_PATTERN.search(context):
        return False
    line = command.lower()
    patterns = (
        (r"^vlan\s+\d+$", r"(?:\bvlan\s+vlan[-_]?id\b|^\s*(?:\|\s*)?(?:[a-z0-9._/-]+\([^)]+\)#\s*)?vlan\s+\d+\b)"),
        (r"^interface\s+vlan\d+$", r"\binterface\s+vlan(?:\s*\d+|\s+vlan[-_]?id)\b"),
        (r"^interface\s+gigabitethernet", r"\binterface\s+(?:interface[-_]?id|gigabitethernet)"),
        (r"^switchport\s+mode\s+access$", r"\bswitchport\s+mode\s+access\b"),
        (r"^switchport\s+access\s+vlan\s+\d+$", r"\bswitchport\s+access\s+vlan\b"),
        (r"^ip\s+address\s+", r"\bip\s+address\s+(?:ip[-_]?address|address|\d+\.\d+\.\d+\.\d+)"),
        (r"^ip\s+helper-address\s+", r"\bip\s+helper-address\s+(?!global\b|vrf\b)(?:ip[-_]?address|address|\d+\.\d+\.\d+\.\d+)"),
        (r"^(?:ip\s+)?vrf\s+forwarding\s+", r"\b(?:ip\s+)?vrf\s+forwarding\s+\S+"),
        (r"^ip\s+routing$", r"\bip\s+routing\b"),
        (r"^service\s+dhcp$", r"\bservice\s+dhcp\b"),
        (r"^switchport$", r"\bswitchport\b"),
    )
    if line == "no shutdown":
        return _svi_no_shutdown(evidence.text)
    if line == "exit":
        return _supports_exit(evidence.text, mode, context)
    for command_pattern, source_pattern in patterns:
        if re.search(command_pattern, line):
            return _source_has_command(evidence.text, source_pattern)
    return False


def audit_configuration_citations(
    configuration: str,
    explanation: str,
    citations: Sequence[str],
    evidence: Sequence[CitationEvidence],
) -> list[dict[str, Any]]:
    cited_evidence = {
        item.citation_id: item for item in evidence if item.citation_id in citations
    }
    explanation_lines = [
        " ".join(line.replace("`", "").replace("**", "").split())
        for line in explanation.splitlines()
    ]
    checks: list[dict[str, Any]] = []
    mode = ""
    for raw_command in configuration.splitlines():
        command = " ".join(raw_command.split())
        if not command or command == "!":
            continue
        pattern = re.compile(
            r"^(?:[-*]\s*)?CLI\s*[:：]\s*"
            + re.escape(command)
            + r"\s+(?=\[)",
            re.IGNORECASE,
        )
        references = {
            citation_id
            for line in explanation_lines
            if pattern.search(line)
            for citation_id in cited_evidence
            if f"[{citation_id}]" in line
        }
        supported = sorted(
            citation_id for citation_id in references
            if _evidence_supports_command(command, cited_evidence[citation_id], mode)
        )
        rejected = sorted(references - set(supported))
        checks.append({
            "command": command,
            "citations": supported,
            "rejected_citations": rejected,
            "verified": bool(supported) and not rejected,
        })
        if command.lower().startswith(("interface ", "vlan ")):
            mode = command.lower()
        elif command.lower() == "exit":
            mode = ""
    return checks


def _component_commands(
    sheet: AccessSheet, name: str, configuration: str
) -> tuple[list[tuple[str, str, str]], list[str]]:
    port = f"interface {sheet.access_port}".lower()
    svi = f"interface vlan{sheet.vlan_id}"
    vlan = f"vlan {sheet.vlan_id}"
    required: dict[str, set[tuple[str, str]]] = {
        "VLAN": {("global", vlan), (vlan, "exit")},
        "アクセスポート": {
            ("global", port),
            (port, "switchport mode access"),
            (port, f"switchport access vlan {sheet.vlan_id}"),
            (port, "exit"),
        },
        "SVI": {
            ("global", svi),
            (svi, f"ip address {sheet.svi.ip} {sheet.svi.netmask}"),
            (svi, "no shutdown"),
            (svi, "exit"),
        },
        "DHCPリレー": {
            ("global", svi),
            (svi, f"ip helper-address {sheet.dhcp_server}"),
            (svi, "exit"),
        },
    }
    if sheet.vrf != "default":
        required["SVI"].add((svi, f"vrf forwarding {sheet.vrf}"))
    if sheet.ip_routing == "configure":
        required["SVI"].add(("global", "ip routing"))
    if sheet.service_dhcp == "configure":
        required["DHCPリレー"].add(("global", "service dhcp"))
    allowed = required[name] | ({(port, "switchport")} if name == "アクセスポート" else set())
    items: list[tuple[str, str, str]] = []
    errors: list[str] = []
    context = "global"
    svi_address_seen = False
    for line in configuration.splitlines():
        raw = " ".join(line.split())
        command = raw.lower()
        if not command or command == "!":
            continue
        vrf_match = re.fullmatch(r"(?:ip )?vrf forwarding (\S+)", raw, re.I)
        if vrf_match:
            command = f"vrf forwarding {vrf_match.group(1)}"
            if context == svi and svi_address_seen:
                errors.append("SVIのVRF割り当てはIPアドレスより先に設定してください")
        if context == svi and command.startswith("ip address "):
            svi_address_seen = True
        if command.startswith(("interface ", "vlan ")):
            if context != "global":
                errors.append(f"設定モードをexitせずに移動しました: {raw}")
            item = ("global", command)
            context = command
        else:
            item = (context, command)
            if command == "exit":
                context = "global"
        if item not in allowed:
            errors.append(f"未指定または対象外の設定 ({item[0]}): {raw}")
        items.append((*item, raw))
    if context != "global":
        errors.append(f"設定モードが終了していません: {context}")
    found = {(context, command) for context, command, _ in items}
    errors.extend(
        f"必要な設定がありません ({context}): {command}"
        for context, command in sorted(required[name] - found)
    )
    return items, errors


def generate_sheet_configuration(
    sheet: AccessSheet, service: GenerationService, top_k: int
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "requirement": sheet.requirement(),
        "knowledge_policy": "disallow",
        "configuration": "",
        "explanation": "",
        "warnings": [],
        "unsupported_requirements": [],
        "missing_required_values": [],
        "inferred_commands": [],
        "citations": [],
        "retrieval": [],
        "search_queries": [],
        "cli_terms": [],
        "citation_checks": [],
        "component_results": [],
    }
    merged: dict[str, dict[str, str]] = {"global": {}}
    failed = False
    for component in sheet.components():
        outcome = service.generate(ConfigurationGenerationRequest(
            requirement=component.requirement,
            product="catalyst9300",
            release="26.x",
            knowledge_policy="disallow",
            top_k=top_k,
        ))
        part = outcome.result
        configuration = part["configuration"]
        items, errors = _component_commands(sheet, component.name, configuration) if configuration else ([], [])
        checks = audit_configuration_citations(
            configuration, part["explanation"], part["citations"], outcome.evidence
        ) if configuration else []
        associated = {
            citation_id
            for check in checks
            for citation_id in (*check["citations"], *check["rejected_citations"])
        }
        unassociated = [
            citation_id for citation_id in part["citations"]
            if citation_id not in associated
        ]
        if configuration and unassociated:
            result["warnings"].append(
                f"{component.name}: 設定CLIと対応付いていない引用は文脈未確認です: "
                + ", ".join(unassociated)
            )
        unverified = [check["command"] for check in checks if not check["verified"]]
        if errors:
            result["unsupported_requirements"].append("パラメータシートと生成設定の不一致")
        if unverified:
            result["unsupported_requirements"].append("CLIごとの引用を確認できません")
            errors.append("引用の未確認または文脈不一致: " + ", ".join(unverified))
        if not configuration and not part["unsupported_requirements"] and not part["missing_required_values"]:
            errors.append("設定が生成されませんでした")
            result["unsupported_requirements"].append(f"{component.name}の設定を生成できません")
        part_failed = bool(
            not configuration or errors
            or part["unsupported_requirements"] or part["missing_required_values"]
        )
        failed |= part_failed
        for context, command, raw in items:
            if context == "global" and command.startswith(("interface ", "vlan ")):
                merged.setdefault(command, {})
                merged[command].setdefault("__mode__", raw)
            else:
                merged.setdefault(context, {}).setdefault(command, raw)
        for field in ("warnings", "unsupported_requirements", "missing_required_values"):
            result[field].extend(f"{component.name}: {item}" for item in part[field])
        result["warnings"].extend(f"{component.name}: {error}" for error in errors)
        result["citation_checks"].extend(checks)
        result["citations"].extend(part["citations"])
        result["retrieval"].extend(part.get("retrieval", []))
        result["search_queries"].extend(part.get("search_queries", []))
        result["cli_terms"].extend(part.get("cli_terms", []))
        result["component_results"].append({
            "name": component.name,
            "requirement": component.requirement,
            "status": "failed" if part_failed else "verified",
            "candidate_configuration": configuration,
            "explanation": part["explanation"],
            "warnings": [*part["warnings"], *errors],
            "unsupported_requirements": part["unsupported_requirements"],
            "missing_required_values": part["missing_required_values"],
            "search_queries": part.get("search_queries", []),
            "cli_terms": part.get("cli_terms", []),
            "citation_checks": checks,
        })
    result["citations"] = list(dict.fromkeys(result["citations"]))
    result["search_queries"] = list(dict.fromkeys(result["search_queries"]))
    result["cli_terms"] = list(dict.fromkeys(result["cli_terms"]))
    result["unsupported_requirements"] = list(dict.fromkeys(result["unsupported_requirements"]))
    result["retrieval"] = list({
        item["citation_id"]: item for item in result["retrieval"]
    }.values())
    result["explanation"] = "\n\n".join(
        f"### {part['name']}\n\n{part['explanation']}"
        for part in result["component_results"]
    )
    if not failed:
        lines = list(merged["global"].values())
        for context, commands in merged.items():
            if context == "global":
                continue
            lines.append(commands["__mode__"])
            lines.extend(
                f" {raw}" for key, raw in commands.items()
                if key not in {"__mode__", "exit"}
            )
            lines.append(commands["exit"])
        configuration = "\n".join(lines)
        errors = check_configuration(sheet, configuration)
        if errors:
            failed = True
            result["unsupported_requirements"].append("パラメータシートと生成設定の不一致")
            result["warnings"].extend(errors)
        else:
            result["configuration"] = configuration
    if failed:
        result["warnings"].append("要件ごとの確認または結合後の検証に失敗したため、設定全体を棄権しました。")
    return result


def _index_context_warning(qdrant_path: Path) -> str:
    manifest_path = qdrant_path / "index-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "索引の見出し形式を確認できません。索引マニフェストを確認してください。"
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SheetError(f"索引マニフェストを読み取れません: {error}") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("section_versions", {}), dict):
        raise SheetError("索引マニフェストの見出し形式情報が不正です")
    if __package__:
        from .rag import SECTION_INDEX_VERSION
    else:
        from rag import SECTION_INDEX_VERSION
    if manifest.get("section_versions", {}).get("catalyst9300") != SECTION_INDEX_VERSION:
        return (
            "索引に手順見出しの改善が未反映です。READMEの手順で対象製品を再索引化してください。"
        )
    return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sheet", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--markdown-output", type=Path, help="Markdownレポートの保存先"
    )
    parser.add_argument("--qdrant-path", type=Path, default=Path("data/qdrant"))
    parser.add_argument("--top-k", type=int, default=20)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.validate_only and args.markdown_output:
        parser.error("--markdown-outputは--validate-onlyと併用できません")
    if args.markdown_output and args.markdown_output.resolve() == args.sheet.resolve():
        parser.error("--markdown-outputには入力CSVとは異なるパスを指定してください")
    try:
        sheet = load_sheet(args.sheet)
    except SheetError as error:
        print(f"パラメータシートエラー: {error}", file=sys.stderr)
        return 1
    if args.validate_only:
        print(json.dumps({
            "requirement": sheet.requirement(),
            "components": [
                {"name": component.name, "requirement": component.requirement}
                for component in sheet.components()
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    if __package__:
        from .config_generation_service import (
            ConfigurationGenerationError,
            ConfigurationGenerationService,
        )
        from .rag import (
            E5Embedder, RAGError, expand_search_plan, generate_configuration,
            format_markdown_report, search_expanded,
        )
        from .transform_markdown import TransformationError, load_config
    else:
        from config_generation_service import (
            ConfigurationGenerationError,
            ConfigurationGenerationService,
        )
        from rag import (
            E5Embedder, RAGError, expand_search_plan, generate_configuration,
            format_markdown_report, search_expanded,
        )
        from transform_markdown import TransformationError, load_config
    try:
        index_warning = _index_context_warning(args.qdrant_path)
        service = ConfigurationGenerationService(
            qdrant_path=args.qdrant_path,
            embedder=E5Embedder(),
            llm_config=load_config(),
            search_planner=expand_search_plan,
            evidence_searcher=search_expanded,
            configuration_generator=generate_configuration,
        )
        result = generate_sheet_configuration(sheet, service, args.top_k)
        if index_warning:
            result["warnings"].append(index_warning)
    except (RAGError, TransformationError, ConfigurationGenerationError, SheetError) as error:
        print(f"設定生成エラー: {error}", file=sys.stderr)
        return 1
    if args.markdown_output:
        try:
            args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
            args.markdown_output.write_text(
                format_markdown_report(result, args.markdown_output),
                encoding="utf-8",
            )
        except OSError as error:
            print(f"Markdownレポートの保存エラー: {error}", file=sys.stderr)
            return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
