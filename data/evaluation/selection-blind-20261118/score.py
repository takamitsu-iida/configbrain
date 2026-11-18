"""Validate the saved runs and score the fixed representative chapter groups."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def read(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def main():
    inputs = read("inputs.json")
    gold = read("gold.json")
    catalog_path = ROOT / "data/catalog/c9300-iosxe26.json"
    if hashlib.sha256(catalog_path.read_bytes()).hexdigest() != gold["catalog_sha256"]:
        raise ValueError("Catalog SHA-256 does not match the fixed gold")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    chapters = {
        (document["document_id"], chapter["order"]): chapter
        for document in catalog["documents"]
        for chapter in document["chapters"]
    }
    gold_chapters = {name: tuple(key) for name, key in gold["chapters"].items()}
    if any(key not in chapters for key in gold_chapters.values()):
        raise ValueError("Gold references a chapter outside the catalog")
    item_ids = [
        f"{case['id']}/{item['id']}"
        for case in inputs["cases"]
        for item in case["items"]
    ]
    if len(set(item_ids)) != len(item_ids) or set(item_ids) != set(gold["items"]):
        raise ValueError("Input and gold item IDs do not match uniquely")
    outputs = {}
    for name in ("llm-run-01.json", "llm-run-02.json", "llm-run-03.json", "qdrant-run.json"):
        run = read(name)
        by_item = {}
        for result in run["results"]:
            item_id = f"{result['case_id']}/{result['item_id']}"
            if item_id in by_item or item_id not in item_ids:
                raise ValueError(f"{name}: duplicated or unexpected item {item_id}")
            candidates = result["candidates"]
            if len(candidates) > 5:
                raise ValueError(f"{name}: too many candidates for {item_id}")
            seen = set()
            normalized = []
            for candidate in candidates:
                key = (candidate["document_id"], candidate["chapter_order"])
                if key not in chapters or key in seen:
                    raise ValueError(f"{name}: invalid or duplicated chapter {key}")
                seen.add(key)
                chapter = chapters[key]
                if "chapter_url" in candidate and candidate["chapter_url"] != chapter["url"]:
                    raise ValueError(f"{name}: chapter URL does not match catalog: {key}")
                if name.startswith("llm-") and (
                    not candidate.get("reason") or not candidate.get("uncertainty")
                ):
                    raise ValueError(f"{name}: missing reason or uncertainty: {item_id}")
                normalized.append({
                    **candidate,
                    "chapter_title": chapter["title"],
                    "chapter_url": chapter["url"],
                })
            groups = gold["items"][item_id]
            row = {"candidates": normalized, "scores": None}
            if groups is not None:
                row["scores"] = {}
                for k in (1, 2, 5):
                    selected = [(x["document_id"], x["chapter_order"]) for x in normalized[:k]]
                    score = {}
                    for kind in ("chapter", "document"):
                        hits = []
                        for group in groups:
                            alternatives = [gold_chapters[label] for label in group]
                            if kind == "chapter":
                                hits.append(any(x in selected for x in alternatives))
                            else:
                                hits.append(any(x[0] == y[0] for x in selected for y in alternatives))
                        score[kind] = sum(hits) / len(hits)
                    row["scores"][str(k)] = score
            by_item[item_id] = row
        if set(by_item) != set(item_ids):
            raise ValueError(f"{name}: missing items {set(item_ids) - set(by_item)}")
        aggregates = {}
        for cohort, ids in (
            ("all", [x for x in item_ids if gold["items"][x] is not None]),
            ("original", [x for x in item_ids if not x.startswith(("parameter-vlan120/", "stateful-dhcpv6/")) and gold["items"][x] is not None]),
            ("parameter-vlan120", [x for x in item_ids if x.startswith("parameter-vlan120/")]),
            ("stateful-dhcpv6", ["stateful-dhcpv6/procedure"]),
        ):
            aggregates[cohort] = {"item_count": len(ids)}
            for k in (1, 2, 5):
                aggregates[cohort][str(k)] = {
                    kind: sum(by_item[x]["scores"][str(k)][kind] for x in ids) / len(ids)
                    for kind in ("chapter", "document")
                }
                aggregates[cohort][str(k)]["fully_covered"] = sum(
                    by_item[x]["scores"][str(k)]["chapter"] == 1 for x in ids
                )
        outputs[name] = {"aggregates": aggregates, "items": by_item}
    (HERE / "scored.json").write_text(
        json.dumps(outputs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for name, output in outputs.items():
        print(name, json.dumps(output["aggregates"], ensure_ascii=False))


if __name__ == "__main__":
    main()
