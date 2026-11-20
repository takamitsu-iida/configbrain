import hashlib
import json
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from configbrain.index_manuals import (
    COLLECTION_ALIAS,
    ManualChapter,
    ManualDocument,
    _index_documents,
)
from configbrain.search_manuals import SearchError, main, search_manuals


class FakeTokenizer:
    def tokenize(self, text: str) -> list[str]:
        return text.split()

    def encode(self, text: str, *, add_special_tokens: bool = True) -> list[int]:
        return list(range(len(text.split()) + (2 if add_special_tokens else 0)))

    def num_special_tokens_to_add(self, pair: bool = False) -> int:
        return 2


class FakeModel:
    tokenizer = FakeTokenizer()
    max_seq_length = 512

    def __init__(self) -> None:
        self.queries: list[str] = []

    def get_embedding_dimension(self) -> int:
        return 3

    def encode(self, sentences: list[str], **kwargs: object) -> list[list[float]]:
        self.queries.extend(sentences)
        return [[1.0, 0.0, 0.0] for _ in sentences]


@pytest.fixture
def search_data(tmp_path: Path) -> tuple[Path, Path, Path]:
    catalog_path = tmp_path / "catalog.json"
    documents = []
    entries = []
    candidates = []
    for document_id in ("vlan-guide", "ip-guide"):
        chapters = [
            {
                "order": order, "title": f"Chapter {order}",
                "url": f"https://www.cisco.com/{document_id}/{order}.html",
            }
            for order in (1, 2)
        ]
        entries.append({
            "document_id": document_id, "title": document_id,
            "category": "Configuration Guides", "html_status": "available",
            "chapters": chapters,
        })
        documents.append(ManualDocument(
            document_id=document_id, title=document_id,
            product="catalyst9300", release="26.x", source_pdf_sha256="a" * 64,
            chapters=tuple(
                ManualChapter(
                    order=chapter["order"], title=chapter["title"],
                    html_url=chapter["url"], html_content_sha256="b" * 64,
                    pdf_page_index=12, pdf_mapping_status="matched",
                    markdown=f"## Procedure\n\n{document_id} chapter {chapter['order']}\n\n"
                    "<table><tr><th>Step</th><th>Command</th></tr>"
                    "<tr><td>1</td><td>example command</td></tr></table>\n",
                )
                for chapter in chapters
            ),
        ))
        candidates.append({
            "document_id": document_id, "chapter_order": 1,
            "chapter_title": chapters[0]["title"], "chapter_url": chapters[0]["url"],
            "reason": "Relevant chapter.", "uncertainty": "Not yet verified.",
        })
    catalog_path.write_text(json.dumps({
        "schema_version": 1, "product": "catalyst9300", "release": "26.x",
        "documents": entries,
    }), encoding="utf-8")
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps({
        "product": "catalyst9300", "release": "26.x", "model": "selection-model",
        "catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "requirement": "VLAN作成とDHCPリレー",
        "items": [
            {"requirement": "VLAN作成", "uncertainty": "Needs review.",
             "candidates": candidates},
            {"requirement": "Unknown feature", "uncertainty": "No chapter found.",
             "candidates": []},
        ],
    }), encoding="utf-8")
    database = tmp_path / "db"
    _index_documents(documents, database, model=FakeModel())
    return selection_path, catalog_path, database


def test_search_filters_each_selected_chapter_and_preserves_provenance(
    search_data: tuple[Path, Path, Path],
) -> None:
    model = FakeModel()
    result = search_manuals(*search_data, limit=1, model=model)
    assert model.queries == ["query: VLAN作成"]
    assert result["evidence_status"] == "candidates_only"
    item, unresolved = result["items"]
    assert item["requirement"] == "VLAN作成"
    assert [chapter["document_id"] for chapter in item["chapters"]] == ["vlan-guide", "ip-guide"]
    for rank, chapter in enumerate(item["chapters"], 1):
        assert chapter["selection_rank"] == rank
        assert len(chapter["matches"]) == 1
        hit = chapter["matches"][0]
        assert hit["document_id"] == chapter["document_id"]
        assert hit["chapter_order"] == 1
        assert hit["chapter_url"] == chapter["chapter_url"]
        assert hit["pdf_page_scope"] == "chapter_start"
        assert hit["pdf_page_index"] == 12
        assert hit["source_pdf_sha256"] == "a" * 64
        assert hit["html_content_sha256"] == "b" * 64
        assert any(ref.get("row_index") == 2 for ref in hit["source_refs"])
        assert hit["section_path"] == ["Procedure"]
        assert hit["point_id"] and isinstance(hit["score"], float)
        assert hit["dataset_fingerprint"] == result["dataset_fingerprint"]
    assert unresolved == {
        "requirement": "Unknown feature", "uncertainty": "No chapter found.",
        "status": "no_candidates", "chapters": [],
    }


def test_ipv4_cidr_adds_addressing_terms_to_search_query(
    search_data: tuple[Path, Path, Path],
) -> None:
    selection, catalog, database = search_data
    data = json.loads(selection.read_text())
    data["items"][0]["requirement"] = (
        "FortyGigabitEthernet1/0/1にIPv4アドレス192.0.2.1/24を設定する"
    )
    selection.write_text(json.dumps(data), encoding="utf-8")
    model = FakeModel()

    search_manuals(selection, catalog, database, limit=1, model=model)

    assert model.queries == [
        "query: FortyGigabitEthernet1/0/1にIPv4アドレス192.0.2.1/24を設定する "
        "IP address interface subnet mask",
    ]


@pytest.mark.parametrize("change", ["hash", "url", "order", "items", "product"])
def test_invalid_selection_fails_before_loading_model(
    search_data: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    selection_path, catalog_path, database = search_data
    data = json.loads(selection_path.read_text())
    if change == "hash":
        data["catalog_sha256"] = "0" * 64
    elif change == "url":
        data["items"][0]["candidates"][0]["chapter_url"] = "https://www.cisco.com/wrong.html"
    elif change == "order":
        data["items"][0]["candidates"][0]["chapter_order"] = True
    elif change == "items":
        data["items"] = []
    else:
        data["product"] = "other"
    selection_path.write_text(json.dumps(data))
    monkeypatch.setattr(
        "configbrain.search_manuals._load_embedding_model",
        lambda *args: pytest.fail("Model must not load for invalid input"),
    )
    assert main([
        "--selection", str(selection_path), "--catalog", str(catalog_path),
        "--database", str(database),
    ]) == 1


def test_missing_database_does_not_create_an_empty_database(
    search_data: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    selection, catalog, _ = search_data
    missing = tmp_path / "missing"
    with pytest.raises(SearchError, match="does not exist"):
        search_manuals(selection, catalog, missing, model=FakeModel())
    assert not missing.exists()


@pytest.mark.parametrize("failure", ["missing_chapter", "old_metadata", "model", "generation"])
def test_incompatible_index_is_rejected(
    search_data: tuple[Path, Path, Path], failure: str,
) -> None:
    selection, catalog, database = search_data
    client = QdrantClient(path=str(database))
    try:
        points, _ = client.scroll(COLLECTION_ALIAS, limit=100)
        if failure == "missing_chapter":
            client.delete(COLLECTION_ALIAS, points_selector=[point.id for point in points])
        elif failure == "old_metadata":
            client.delete_payload(COLLECTION_ALIAS, keys=["pdf_page_scope"], points=[p.id for p in points])
        elif failure == "model":
            client.set_payload(COLLECTION_ALIAS, payload={"embedding_model": "other"}, points=[p.id for p in points])
        else:
            ids = [p.id for p in points if p.payload["document_id"] == "ip-guide"]
            client.set_payload(COLLECTION_ALIAS, payload={"dataset_fingerprint": "other"}, points=ids)
    finally:
        client.close()
    with pytest.raises(SearchError):
        search_manuals(selection, catalog, database, model=FakeModel())


def test_cli_outputs_json_and_preserves_output_on_failure(
    search_data: tuple[Path, Path, Path], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    selection, catalog, database = search_data
    model = FakeModel()
    monkeypatch.setattr("configbrain.search_manuals._load_embedding_model", lambda *args: model)
    args = ["--selection", str(selection), "--catalog", str(catalog), "--database", str(database)]
    capsys.readouterr()
    assert main(args + ["--limit", "1"]) == 0
    assert len(json.loads(capsys.readouterr().out)["items"]) == 2
    output = tmp_path / "search.json"
    assert main(args + ["--output", str(output)]) == 0
    saved = output.read_bytes()
    assert main(args + ["--output", str(output), "--limit", "0"]) == 1
    assert output.read_bytes() == saved
    assert "positive integer" in capsys.readouterr().err
    references = tmp_path / "references"
    references.mkdir()
    commands = references / "commands.json"
    commands.write_text("existing reference")
    args += ["--reference-root", str(references)]
    for protected in (selection, catalog, database / "meta.json", commands):
        original = protected.read_bytes()
        assert main(args + ["--output", str(protected)]) == 1
        assert protected.read_bytes() == original


def test_overlong_query_is_rejected_instead_of_truncated(
    search_data: tuple[Path, Path, Path],
) -> None:
    model = FakeModel()
    model.max_seq_length = 2
    with pytest.raises(SearchError, match="exceeds"):
        search_manuals(*search_data, model=model)
    assert not model.queries


def test_unresolved_selection_does_not_load_embedding_model(
    search_data: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection, _, _ = search_data
    data = json.loads(selection.read_text())
    data["items"] = [data["items"][1]]
    selection.write_text(json.dumps(data))
    monkeypatch.setattr(
        "configbrain.search_manuals._load_embedding_model",
        lambda *args: pytest.fail("No model needed without selected chapters"),
    )
    result = search_manuals(*search_data)
    assert result["items"][0]["status"] == "no_candidates"
    assert result["dataset_fingerprint"] is None


def test_cli_reports_locked_database_without_replacing_output(
    search_data: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    selection, catalog, database = search_data
    output = tmp_path / "search.json"
    output.write_text("existing output")
    client = QdrantClient(path=str(database))
    try:
        assert main([
            "--selection", str(selection), "--catalog", str(catalog),
            "--database", str(database), "--output", str(output),
        ]) == 1
        assert output.read_text() == "existing output"
        assert "already accessed" in capsys.readouterr().err
    finally:
        client.close()
