"""Extract and search command records grounded in locally saved Cisco HTML."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections import Counter, defaultdict
from copy import copy
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup, Tag

from configbrain.convert_manual import (
    ConversionError,
    fetch_chapter_page,
    load_document,
    packages_match,
    promote_package,
    sha256_bytes,
    write_json,
)


SCHEMA_VERSION = 1
EXTRACTOR_VERSION = 1
SECTION_KINDS = {
    "syntax description": "syntax_description",
    "command default": "command_default",
    "command defaults": "command_default",
    "command modes": "command_modes",
    "command mode": "command_modes",
    "command history": "command_history",
    "usage guidelines": "usage_guidelines",
    "examples": "examples",
    "related commands": "related_commands",
}
SUBCOMMAND_INTRO = re.compile(
    r"(?:These|The following) (?:configuration )?commands are available in "
    r"(?P<mode>[\w ()/-]+?) mode\b",
    re.IGNORECASE,
)


def normalized_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def _lookup_names(value: str) -> set[str]:
    return {
        normalized_name(value),
        normalized_name(re.sub(r"\s+\([^()]+\)$", "", value.strip())),
    }


def _text(node: Tag) -> str:
    return " ".join(node.get_text(" ", strip=True).split())


def _locator(node: Tag) -> str:
    parts = []
    current: Tag | None = node
    while current is not None and current.get("id") != "chapterContent":
        position = 1 + sum(
            sibling.name == current.name
            for sibling in current.previous_siblings
            if isinstance(sibling, Tag)
        )
        parts.append(f"{current.name}:nth-of-type({position})")
        current = current.parent
    if current is None:
        raise ConversionError("Command element is outside chapterContent")
    return " > ".join(["#chapterContent", *reversed(parts)])


def _source(node: Tag, chapter: dict[str, Any]) -> dict[str, Any]:
    anchor = node.get("id")
    return {
        "chapter_order": chapter["order"],
        "html_file": f"html/{chapter['order']}.html",
        "chapter_url": chapter["url"],
        "anchor": anchor,
        "url": chapter["url"] + (f"#{anchor}" if anchor else ""),
        "locator": _locator(node),
    }


def _sections(
    body: Tag, chapter: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    sections: list[dict[str, Any]] = []
    modes = []
    current: dict[str, Any] | None = None
    for node in body.find_all(recursive=False):
        if not isinstance(node, Tag):
            continue
        heading = (
            node if "sectiontitle" in node.get("class", [])
            else node.find(class_="sectiontitle")
        )
        if heading is not None:
            title = _text(heading)
            kind = SECTION_KINDS.get(normalized_name(title), "other")
        elif "refsyn" in node.get("class", []):
            title, kind = "Syntax", "syntax"
        elif current is None:
            title, kind = "Description", "description"
        else:
            title, kind = "", ""
        if title:
            current = {"kind": kind, "title": title, "text": "", "sources": []}
            sections.append(current)
        if current is None:
            raise ConversionError("Could not identify command section")
        current["text"] += ("\n" if current["text"] else "") + _text(node)
        current["sources"].append(_source(node, chapter))
        if current["kind"] == "command_modes":
            paragraphs = (
                [node] if node.name in {"p", "li"} else node.find_all(["p", "li"])
            )
            for paragraph in paragraphs:
                mode_text = copy(paragraph)
                for line_break in mode_text.find_all("br"):
                    line_break.replace_with("\0")
                modes.extend(
                    " ".join(line.split())
                    for line in mode_text.get_text(" ", strip=True).split("\0")
                    if line.strip()
                )
    return sections, list(dict.fromkeys(modes))


def _subcommands(
    article: Tag, chapter: dict[str, Any], command_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records = []
    unresolved = []
    for paragraph in article.select(".usage_guidelines > p"):
        introduction = SUBCOMMAND_INTRO.search(_text(paragraph))
        if introduction is None:
            continue
        listing = paragraph.find_next_sibling()
        if listing is None or listing.name != "ul":
            unresolved.append({
                "name": None, "reason": "subcommand_list_missing",
                "source": _source(paragraph, chapter),
            })
            continue
        for item in listing.find_all("li", recursive=False):
            description = item.find("p", recursive=False)
            keyword = description.select_one(".keyword") if description else None
            if keyword is None or not _text(keyword):
                unresolved.append({
                    "name": None, "reason": "subcommand_name_missing",
                    "source": _source(item, chapter),
                })
                continue
            records.append({
                "name": _text(keyword),
                "parent_command_id": command_id,
                "modes": [introduction["mode"]],
                "content_html": str(item),
                "text": _text(item),
                "source": _source(item, chapter),
                "parse_status": "parsed",
            })
    counts = Counter(normalized_name(record["name"]) for record in records)
    parsed = []
    for record in records:
        if counts[normalized_name(record["name"])] > 1:
            unresolved.append({
                "name": record["name"], "reason": "duplicate_subcommand_name",
                "source": record["source"],
            })
        else:
            parsed.append(record)
    return parsed, unresolved


def extract_command_chapter(
    raw_html: bytes, chapter: dict[str, Any]
) -> dict[str, Any]:
    soup = BeautifulSoup(raw_html, "html.parser")
    contents = soup.select("#chapterContent")
    if len(contents) != 1:
        raise ConversionError(
            f"Expected one chapterContent container: {chapter['url']}"
        )
    content = contents[0]
    ids = Counter(str(node["id"]) for node in content.select("[id]"))
    toc: dict[str, list[str]] = defaultdict(list)
    warnings = []
    for link in content.select("nav.related-links a[href]"):
        target_url, anchor = urldefrag(urljoin(chapter["url"], str(link["href"])))
        if target_url != chapter["url"] or not anchor:
            warnings.append({"reason": "nonlocal_toc_link", "url": str(link["href"])})
        else:
            toc[anchor].append(_text(link))

    commands = []
    unparsed = []
    non_command_topics = []
    for article in content.find_all("article", recursive=False):
        heading = article.find(["h2", "h3"], class_="title", recursive=False)
        name = _text(heading) if heading else None
        source = _source(article, chapter)
        if "concept" in article.get("class", []):
            non_command_topics.append({"name": name, "source": source})
            continue
        reason = None
        anchor = source["anchor"]
        bodies = article.select(":scope > .refbody")
        if "reference" not in article.get("class", []):
            reason = "unknown_topic_type"
        elif not anchor or not name or len(bodies) != 1:
            reason = "missing_command_structure"
        elif ids[anchor] != 1:
            reason = "duplicate_anchor"
        elif article.find("article") is not None:
            reason = "nested_command_container"
        if reason:
            unparsed.append({"name": name, "reason": reason, "source": source})
            continue
        command_id = f"{chapter['order']}:{anchor}"
        sections, modes = _sections(bodies[0], chapter)
        subcommands, unresolved = _subcommands(article, chapter, command_id)
        toc_names = {normalized_name(title) for title in toc.get(anchor, [])}
        toc_status = (
            "matched" if normalized_name(name) in toc_names
            else "missing" if not toc_names else "title_mismatch"
        )
        if toc_status != "matched":
            warnings.append({
                "reason": f"toc_{toc_status}", "name": name, "source": source,
            })
        commands.append({
            "command_id": command_id,
            "name": name,
            "modes": modes,
            "source": source,
            "content_html": str(article),
            "sections": sections,
            "subcommands": subcommands,
            "subcommand_extraction": "explicit_lists_only",
            "unparsed_subcommands": unresolved,
            "parse_status": "parsed",
            "toc_status": toc_status,
        })

    for article in content.select("article.reference"):
        if article.parent is not content:
            heading = article.find(["h2", "h3"], class_="title", recursive=False)
            unparsed.append({
                "name": _text(heading) if heading else None,
                "reason": "nested_command_container",
                "source": _source(article, chapter),
            })
    for anchor, titles in toc.items():
        if anchor not in ids:
            unparsed.append({
                "name": titles[0], "reason": "toc_target_missing",
                "source": {"chapter_url": chapter["url"], "anchor": anchor},
            })
    if not commands and not non_command_topics and not unparsed:
        if normalized_name(chapter["title"]) != "index":
            unparsed.append({
                "name": None, "reason": "command_containers_missing",
                "source": {"chapter_url": chapter["url"]},
            })
    return {
        "order": chapter["order"],
        "title": chapter["title"],
        "url": chapter["url"],
        "html_file": f"html/{chapter['order']}.html",
        "html_sha256": sha256_bytes(raw_html),
        "status": "partial" if unparsed else "parsed" if commands else "no_commands",
        "commands": commands,
        "unparsed": unparsed,
        "warnings": warnings,
        "non_command_topics": non_command_topics,
    }


def _metadata(catalog: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "document_type": "command_reference",
        "document_id": document["document_id"],
        "product": catalog["product"],
        "release": catalog["release"],
        "title": document["title"],
        "html_book_url": document["html_book_url"],
    }


def convert_command_reference(
    catalog: dict[str, Any], document: dict[str, Any], output_root: Path
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    output_dir = output_root / document["document_id"]
    if output_dir.is_symlink() or output_dir.resolve().parent != output_root.resolve():
        raise ConversionError(f"Unsafe command reference output path: {output_dir}")
    output_existed = output_dir.exists()
    with tempfile.TemporaryDirectory(
        prefix=f".{document['document_id']}.", dir=output_root
    ) as temporary:
        staged = Path(temporary) / document["document_id"]
        (staged / "html").mkdir(parents=True)
        chapters = []
        for chapter in sorted(document["chapters"], key=lambda item: item["order"]):
            raw_html = fetch_chapter_page(chapter["url"], catalog["release"])
            chapters.append(extract_command_chapter(raw_html, chapter))
            (staged / "html" / f"{chapter['order']}.html").write_bytes(raw_html)
        data = {**_metadata(catalog, document), "chapters": chapters}
        command_count = sum(len(chapter["commands"]) for chapter in chapters)
        if not command_count:
            raise ConversionError("No unambiguous command records were extracted")
        write_json(staged / "commands.json", data)
        if output_existed and packages_match(staged, output_dir):
            status = "unchanged"
        else:
            promote_package(staged, output_dir)
            status = "updated" if output_existed else "created"
    return {
        "document_type": "command_reference",
        "status": status,
        "output": str(output_dir),
        "chapter_count": len(chapters),
        "command_count": command_count,
        "subcommand_count": sum(
            len(command["subcommands"])
            for chapter in chapters for command in chapter["commands"]
        ),
        "unparsed_count": sum(
            len(chapter["unparsed"]) + sum(
                len(command["unparsed_subcommands"]) for command in chapter["commands"]
            )
            for chapter in chapters
        ),
        "warning_count": sum(len(chapter["warnings"]) for chapter in chapters),
    }


class CommandReferenceIndex:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.names: dict[str, list[tuple[dict[str, Any], dict[str, Any] | None]]] = (
            defaultdict(list)
        )
        for chapter in data["chapters"]:
            for command in chapter["commands"]:
                for alias in _lookup_names(command["name"]):
                    self.names[alias].append((command, None))
                for child in command["subcommands"]:
                    self.names[normalized_name(child["name"])].append((command, child))

    def fingerprint_metadata(self) -> dict[str, Any]:
        return {
            **{key: self.data[key] for key in (
                "document_id", "document_type", "product", "release",
                "schema_version", "extractor_version",
            )},
            "commands_sha256": hashlib.sha256(
                json.dumps(self.data, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "chapters": [
                {key: chapter[key] for key in ("order", "url", "html_sha256")}
                for chapter in self.data["chapters"]
            ],
        }

    @classmethod
    def load(
        cls, catalog_path: Path, document_id: str,
        reference_root: Path = Path("data/command_references"),
    ) -> CommandReferenceIndex:
        catalog, document = load_document(catalog_path, document_id)
        if document.get("document_type") != "command_reference":
            raise ConversionError(f"Document is not a command reference: {document_id}")
        directory = reference_root / document_id
        try:
            data = json.loads((directory / "commands.json").read_text(encoding="utf-8"))
            if not isinstance(data, dict) or any(
                data.get(key) != value for key, value in _metadata(catalog, document).items()
            ):
                raise ConversionError("Command reference metadata/version mismatch")
            stored_chapters = data.get("chapters")
            if not isinstance(stored_chapters, list) or len(stored_chapters) != len(
                document["chapters"]
            ):
                raise ConversionError("Command reference chapter list mismatch")
            chapters = []
            for chapter, stored in zip(
                sorted(document["chapters"], key=lambda item: item["order"]),
                stored_chapters, strict=True,
            ):
                raw_html = (directory / "html" / f"{chapter['order']}.html").read_bytes()
                if not isinstance(stored, dict) or stored.get("html_sha256") != sha256_bytes(
                    raw_html
                ):
                    raise ConversionError(f"Stored HTML hash mismatch: {chapter['url']}")
                extracted = extract_command_chapter(raw_html, chapter)
                if stored != extracted:
                    raise ConversionError(
                        f"Command records do not match saved HTML: {chapter['url']}"
                    )
                chapters.append(extracted)
        except (OSError, ValueError) as error:
            raise ConversionError(f"Could not load command reference: {directory}: {error}") from error
        return cls({**_metadata(catalog, document), "chapters": chapters})

    def search(
        self, name: str, *, mode: str | None = None, parent: str | None = None
    ) -> dict[str, Any]:
        if not name.strip() or (mode is not None and not mode.strip()) or (
            parent is not None and not parent.strip()
        ):
            raise ConversionError("Command name, mode, and parent must not be blank")
        matches = []
        for command, child in self.names.get(normalized_name(name), []):
            if parent is not None and (
                child is None or normalized_name(parent) not in _lookup_names(command["name"])
            ):
                continue
            modes = (child or command)["modes"]
            if mode is not None and not any(
                normalized_name(mode) in {
                    normalized_name(value),
                    normalized_name(value.split("(", 1)[0]),
                    *(
                        normalized_name(prompt)
                        for prompt in re.findall(r"\(([^)]+)\)", value)
                    ),
                }
                for value in modes
            ):
                continue
            matches.append({"command": command, "matched_subcommand": child})
        unresolved = [
            item for chapter in self.data["chapters"] for item in chapter["unparsed"]
            if item["name"] is None or normalized_name(name) in _lookup_names(item["name"])
        ]
        unresolved.extend(
            item for chapter in self.data["chapters"] for command in chapter["commands"]
            for item in command["unparsed_subcommands"]
            if (parent is None or normalized_name(parent) in _lookup_names(command["name"]))
            and (item["name"] is None or normalized_name(item["name"]) == normalized_name(name))
        )
        if unresolved:
            status = "unparsed"
        elif len(matches) > 1:
            status = "ambiguous"
        elif not matches:
            status = "not_found"
        elif matches[0]["matched_subcommand"] is not None and not (mode and parent):
            status = "candidate"
        else:
            status = "unique_match"
        return {
            "document_id": self.data["document_id"],
            "document_title": self.data.get("title"),
            "product": self.data["product"],
            "release": self.data["release"],
            "query": {"name": name, "mode": mode, "parent": parent},
            "status": status,
            "evidence_type": "stored_html",
            "subcommand_coverage": "explicit_lists_only",
            "extractor_version": self.data["extractor_version"],
            "sources": [
                {key: chapter[key] for key in ("order", "title", "url", "html_file", "html_sha256")}
                for chapter in self.data["chapters"]
                if any(
                    match["command"]["source"]["chapter_order"] == chapter["order"]
                    for match in matches
                )
            ],
            "coverage": "partial" if any(
                chapter["unparsed"] or any(
                    command["unparsed_subcommands"] for command in chapter["commands"]
                ) for chapter in self.data["chapters"]
            ) else "parsed",
            "matches": matches,
            "unparsed": unresolved,
        }
