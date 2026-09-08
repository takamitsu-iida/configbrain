from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from app.ingestion.html_loader import HtmlSection


_COMMAND_LINE = re.compile(
    r"^(?:(?:Device|Router|Switch)(?:\([^)]*\))?[#>]|"
    r"(?:configure|conf|enable|end|exit|show|copy|interface|vlan|name|"
    r"description|switchport|no\s+shutdown|ip\s+address|remote-span)\b)",
    re.IGNORECASE,
)
_TABLE_COMMAND_HEADERS = {"command", "command or action", "action", "cli command"}


@dataclass(frozen=True)
class RuleAnnotation:
    extraction_method: str
    content_type: str
    source_text: str
    commands: tuple[dict[str, str], ...]
    prerequisites: tuple[str, ...]
    verification_commands: tuple[str, ...]
    keywords: tuple[str, ...]
    needs_llm: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def annotate_section(section: HtmlSection) -> RuleAnnotation:
    """Extract unambiguous commands from normalized HTML without an LLM."""
    source_text = _source_text(section)
    commands = _commands_from_pre(section.code_blocks)
    commands.extend(_commands_from_tables(section.tables))
    unique_commands = _unique_commands(commands)
    verification = tuple(
        command["text"]
        for command in unique_commands
        if command["text"].casefold().startswith(("show ", "copy "))
    )
    content_type = "configuration_example" if unique_commands else "concept"
    needs_llm = _needs_llm_annotation(section, unique_commands)
    return RuleAnnotation(
        extraction_method="rule_based",
        content_type=content_type,
        source_text=source_text,
        commands=tuple(unique_commands),
        prerequisites=(),
        verification_commands=verification,
        keywords=tuple(_keywords(section, unique_commands)),
        needs_llm=needs_llm,
    )


def _source_text(section: HtmlSection) -> str:
    parts = [section.text.strip()]
    parts.extend(code.strip() for code in section.code_blocks if code.strip())
    parts.extend(
        "\n".join(" | ".join(cell for cell in row) for row in table)
        for table in section.tables
        if table
    )
    return "\n\n".join(part for part in parts if part)


def _needs_llm_annotation(
    section: HtmlSection,
    commands: list[dict[str, str]],
) -> bool:
    """Select only sections whose structure rules cannot classify confidently."""
    if commands:
        return False
    if section.tables or section.code_blocks:
        return True
    return bool(section.text.strip())


def _commands_from_pre(code_blocks: tuple[str, ...]) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for block in code_blocks:
        for line in block.splitlines():
            text = " ".join(line.split()).strip()
            if text and _COMMAND_LINE.match(text):
                commands.append({"text": text, "role": _command_role(text)})
    return commands


def _commands_from_tables(tables: tuple[tuple[tuple[str, ...], ...], ...]) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for table in tables:
        if not table:
            continue
        headers = [cell.casefold().strip() for cell in table[0]]
        command_indexes = [
            index for index, header in enumerate(headers) if header in _TABLE_COMMAND_HEADERS
        ]
        if not command_indexes:
            continue
        for row in table[1:]:
            for index in command_indexes:
                if index < len(row):
                    text = " ".join(row[index].split()).strip()
                    if text and text.casefold() not in {"example:", "command"}:
                        commands.append({"text": text, "role": _command_role(text)})
    return commands


def _unique_commands(commands: list[dict[str, str]]) -> list[dict[str, str]]:
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for command in commands:
        key = command["text"].casefold()
        if key not in seen:
            seen.add(key)
            unique.append(command)
    return unique


def _command_role(text: str) -> str:
    normalized = text.casefold()
    if normalized.startswith("show ") or normalized.startswith("copy "):
        return "verification"
    if normalized.startswith("vlan "):
        return "enter_vlan"
    if normalized.startswith("name "):
        return "set_name"
    if normalized.startswith("interface "):
        return "select_interface"
    return "configuration"


def _keywords(section: HtmlSection, commands: list[dict[str, str]]) -> list[str]:
    values = [section.section_title]
    values.extend(command["text"] for command in commands)
    return list(dict.fromkeys(value for value in values if value))
