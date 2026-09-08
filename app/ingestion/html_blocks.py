from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Iterable

from app.ingestion.html_loader import HtmlSection
from app.ingestion.rule_extractor import annotate_section


def section_to_record(section: HtmlSection) -> dict[str, object]:
    """Convert a normalized HTML section into one stable JSONL record."""
    record = asdict(section)
    record["rule_annotation"] = annotate_section(section).to_dict()
    return record


def write_html_blocks(path: Path, sections: Iterable[HtmlSection]) -> int:
    """Write normalized HTML sections as UTF-8 JSON Lines and return the count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as stream:
        for section in sections:
            stream.write(
                json.dumps(
                    section_to_record(section),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            count += 1
    return count


def write_llm_candidates(path: Path, sections: Iterable[HtmlSection]) -> int:
    """Write only sections that need LLM annotation for later processing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as stream:
        for section in sections:
            record = section_to_record(section)
            if not record["rule_annotation"]["needs_llm"]:
                continue
            stream.write(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            )
            count += 1
    return count
