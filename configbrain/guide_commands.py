"""Read guide procedure rows without treating examples as command syntax."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator

from bs4 import BeautifulSoup, Tag

from configbrain.index_manuals import _visible_html_text

PROMPT_MODES = {
    "config": "Global configuration",
    "config-vlan": "VLAN configuration",
    "config-if": "Interface configuration",
}


@dataclass(frozen=True)
class ProcedureRow:
    table_index: int
    row_index: int
    cells: tuple[Tag, ...]
    text: str
    syntax: str
    parameters: tuple[str, ...]
    literal_prefix: str
    prompt_mode: str | None
    entered_mode: str | None
    purpose: str
    mode_ambiguous: bool


def procedure_rows(markdown: str) -> Iterator[ProcedureRow]:
    soup = BeautifulSoup(markdown, "html.parser")
    for table_index, table in enumerate(soup.find_all("table"), 1):
        rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
        command_column: int | None = None
        for row_index, row in enumerate(rows, 1):
            cells = tuple(row.find_all(["td", "th"], recursive=False))
            for column, cell in enumerate(cells):
                if cell.name == "th" and _visible_html_text(str(cell)).casefold() in (
                    "command", "command or action",
                ):
                    command_column = column
            syntax = ""
            parameters: tuple[str, ...] = ()
            literal_prefix = ""
            prompt_mode = None
            entered_mode = None
            purpose = ""
            mode_ambiguous = False
            if (
                command_column is not None and command_column < len(cells)
                and not any(cell.name == "th" for cell in cells)
            ):
                cell = cells[command_column]
                command_node = cell.select_one("p.cmd")
                if command_node is None:
                    command_node = cell.find("p", recursive=False)
                if command_node is None:
                    command_node = cell
                syntax = _visible_html_text(str(command_node)).split("Example:", 1)[0].strip()
                parameters = tuple(sorted({
                    parameter
                    for var in command_node.find_all(["var", "em", "i"])
                    for parameter in _visible_html_text(str(var)).split()
                }))
                words = []
                for token in re.findall(r"[{}\[\]|]|[^\s{}\[\]|]+", syntax):
                    if token in "{}[]|" or token in parameters:
                        break
                    words.append(token)
                literal_prefix = " ".join(words)
                prompts = {
                    match.group(1) or ("Privileged EXEC" if match.group(2) == "#" else "User EXEC")
                    for block in cell.find_all("pre")
                    for match in re.finditer(
                        r"(?m)^\s*[\w.-]+(?:\(([^()\n]+)\))?([#>])",
                        block.get_text(" ", strip=False),
                    )
                }
                if len(prompts) == 1:
                    prompt_mode = next(iter(prompts))
                mode_ambiguous = len(prompts) > 1
                purpose = " ".join(
                    _visible_html_text(str(other))
                    for column, other in enumerate(cells) if column > command_column
                )
                transitions = {
                    " ".join(match.group(1).split())
                    for match in re.finditer(
                        r"(?:^|[.;]\s+|\band\s+)(?:\(Optional\)\s*)?"
                        r"(?:enters|returns to)\s+(?:the\s+)?"
                        r"([A-Za-z][A-Za-z -]{0,80}?)\s+mode\b",
                        purpose, re.IGNORECASE,
                    )
                }
                mode_ambiguous = mode_ambiguous or len(transitions) > 1
                if len(transitions) == 1 and not mode_ambiguous:
                    entered_mode = next(iter(transitions))
            yield ProcedureRow(
                table_index, row_index, cells, _visible_html_text(str(row)),
                syntax, parameters, literal_prefix,
                prompt_mode, entered_mode, purpose, mode_ambiguous,
            )


def command_contexts(markdown: str) -> Iterator[dict[str, Any]]:
    table_index = None
    active_mode = None
    parent = None
    mode_source = None
    for row in procedure_rows(markdown):
        if row.table_index != table_index:
            table_index = row.table_index
            active_mode = parent = mode_source = None
        if not row.syntax:
            if row.cells and not any(cell.name == "th" for cell in row.cells):
                active_mode = parent = mode_source = None
            continue
        locator = {
            "table_index_in_context": row.table_index, "row_index": row.row_index,
            "syntax": row.syntax, "purpose": row.purpose,
        }
        prompt_mode = (
            PROMPT_MODES.get(row.prompt_mode, row.prompt_mode) if row.prompt_mode else None
        )
        ambiguous_mode = row.mode_ambiguous or (
            active_mode is not None and prompt_mode is not None
            and active_mode.casefold() != prompt_mode.casefold()
        )
        previous_mode_source = mode_source
        if ambiguous_mode:
            active_mode = parent = mode_source = None
        mode = None if ambiguous_mode else active_mode or prompt_mode
        yield {
            **locator, "parameters": list(row.parameters),
            "literal_prefix": row.literal_prefix,
            "mode": mode,
            "parent": parent,
            "mode_status": (
                "ambiguous" if ambiguous_mode else
                "documented" if mode else "unknown"
            ),
            "mode_source": ({
                "transition": previous_mode_source, "prompt_mode": row.prompt_mode,
            } if ambiguous_mode else mode_source or (
                {**locator, "prompt_mode": row.prompt_mode} if row.prompt_mode else None
            )),
        }
        if row.entered_mode:
            active_mode = row.entered_mode
            parent = row.literal_prefix or None
            mode_source = locator
        elif row.literal_prefix.casefold() in ("exit", "end"):
            active_mode = parent = mode_source = None
