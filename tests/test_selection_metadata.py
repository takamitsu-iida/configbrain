import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from configbrain.generate_selection_metadata import (
    generate_all_selection_metadata,
    generate_selection_metadata,
)
from configbrain.index_manuals import (
    ManualChapter,
    ManualCorpus,
    ManualDocument,
    extract_text_blocks,
)
from configbrain.select_manuals import CloudConfig
from configbrain.selection_metadata import (
    SelectionMetadataError,
    extract_selection_segments,
    load_selection_metadata,
)


MANUAL = """## VLAN configuration

Create and verify VLANs on the switch.

### Procedure

<table>
<thead><tr><th>Step</th><th>Command</th><th>Purpose</th></tr></thead>
<tbody><tr><td>1</td><td>vlan 120</td><td>Creates VLAN 120.</td></tr></tbody>
</table>
"""
SOURCE_URL = "https://www.cisco.com/example/vlans.html"
HTML_HASH = "b" * 64
PDF_HASH = "a" * 64


def make_chapter() -> ManualChapter:
    return ManualChapter(
        order=1,
        title="Configuring VLANs",
        html_url=SOURCE_URL,
        html_content_sha256=HTML_HASH,
        pdf_page_index=None,
        pdf_mapping_status="unmapped",
        markdown=MANUAL,
    )


def make_metadata() -> dict:
    section = {
        "section_id": "c1-s1",
        "section_path": ["VLAN configuration"],
        "heading_locations": [],
        "source_range": {"chapter_line_start": 3, "chapter_line_end": 3},
        "source_refs": [{
            "kind": "text",
            "chapter_line_start": 3,
            "chapter_line_end": 3,
        }],
        "summary": "Explains VLAN configuration.",
        "operations": ["Create a VLAN"],
        "answerable_questions": ["How is a VLAN created?"],
    }
    return {
        "schema_version": 1,
        "document_id": "vlan-guide",
        "source_manual_sha256": hashlib.sha256(MANUAL.encode()).hexdigest(),
        "source_pdf_sha256": PDF_HASH,
        "model": "test-model",
        "chapters": [{
            "order": 1,
            "title": "Configuring VLANs",
            "source_url": SOURCE_URL,
            "html_content_sha256": HTML_HASH,
            "summary": "Explains VLAN configuration.",
            "operations": [{"text": "Create a VLAN", "section_ids": ["c1-s1"]}],
            "answerable_questions": [{
                "text": "How is a VLAN created?",
                "section_ids": ["c1-s1"],
            }],
            "sections": [section],
        }],
    }


def test_extracts_heading_hierarchy_and_exact_chapter_line_ranges() -> None:
    segments = extract_selection_segments(make_chapter())

    assert len(segments) == 2
    narrative, procedure = segments
    assert narrative["section_path"] == ["VLAN configuration"]
    assert narrative["source_range"] == {
        "chapter_line_start": 3,
        "chapter_line_end": 3,
    }
    assert procedure["section_path"] == ["VLAN configuration", "Procedure"]
    assert procedure["source_range"]["chapter_line_start"] == 7
    assert procedure["source_range"]["chapter_line_end"] == 10
    assert procedure["source_refs"][0]["kind"] == "table"
    assert procedure["source_refs"][0]["chapter_line_start"] == 7
    assert procedure["heading_locations"][0]["chapter_line_start"] == 1
    assert procedure["heading_locations"][1]["chapter_line_start"] == 5


def test_text_and_code_blocks_include_source_line_ranges() -> None:
    blocks = extract_text_blocks(
        "## Verification\n\nConfirm the VLAN exists.\n\n```text\nshow vlan\n```\n"
    )

    assert blocks[0].source_range == {
        "okf_line_start": 3,
        "okf_line_end": 3,
    }
    assert blocks[1].source_range == {
        "okf_line_start": 5,
        "okf_line_end": 7,
    }


def test_long_extracted_blocks_are_split_without_losing_source_locations() -> None:
    chapter = make_chapter()
    chapter = ManualChapter(
        order=chapter.order,
        title=chapter.title,
        html_url=chapter.html_url,
        html_content_sha256=chapter.html_content_sha256,
        pdf_page_index=None,
        pdf_mapping_status="unmapped",
        markdown="## Details\n\n" + ("x" * 13001),
    )

    segments = extract_selection_segments(chapter)

    assert len(segments) == 3
    assert all(len(segment["source_text"]) <= 6000 for segment in segments)
    assert all(segment["source_range"] == {
        "chapter_line_start": 3,
        "chapter_line_end": 3,
    } for segment in segments)


def test_loads_only_fresh_metadata_and_warns_when_manual_changes(
    tmp_path: Path,
) -> None:
    metadata_root = tmp_path / "metadata"
    metadata_root.mkdir()
    (metadata_root / "vlan-guide.json").write_text(
        json.dumps(make_metadata()), encoding="utf-8",
    )
    package_dir = tmp_path / "okf" / "vlan-guide"
    package_dir.mkdir(parents=True)
    (package_dir / "manual.md").write_text(MANUAL, encoding="utf-8")
    (package_dir / "provenance.json").write_text(json.dumps({
        "source_pdf": {"sha256": PDF_HASH},
        "chapters": [{
            "order": 1,
            "title": "Configuring VLANs",
            "html_url": SOURCE_URL,
            "html_content_sha256": HTML_HASH,
        }],
    }), encoding="utf-8")
    documents = [{
        "document_id": "vlan-guide",
        "document_type": "configuration_guide",
        "chapters": [{"order": 1, "title": "Configuring VLANs"}],
    }]
    catalog_chapters = {
        ("vlan-guide", 1): {"title": "Configuring VLANs", "url": SOURCE_URL},
    }

    loaded, warnings = load_selection_metadata(
        documents, catalog_chapters, metadata_root, tmp_path / "okf",
    )
    assert loaded["vlan-guide"][1]["summary"] == "Explains VLAN configuration."
    assert warnings == []

    (package_dir / "manual.md").write_text(MANUAL + "\nUpdated source.\n", encoding="utf-8")
    loaded, warnings = load_selection_metadata(
        documents, catalog_chapters, metadata_root, tmp_path / "okf",
    )
    assert loaded == {}
    assert warnings == [
        "Selection metadata is stale for vlan-guide; chapter titles only."
    ]


def test_generation_links_model_descriptions_to_extracted_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    chapter = make_chapter()
    chapter = ManualChapter(
        order=chapter.order,
        title=chapter.title,
        html_url=chapter.html_url,
        html_content_sha256=chapter.html_content_sha256,
        pdf_page_index=chapter.pdf_page_index,
        pdf_mapping_status=chapter.pdf_mapping_status,
        markdown="\n\n".join(
            f"## Topic {index}\n\nDo action {index}."
            for index in range(1, 34)
        ),
    )
    document = ManualDocument(
        document_id="vlan-guide",
        title="VLAN Guide",
        product="catalyst9300",
        release="26.x",
        source_pdf_sha256=PDF_HASH,
        chapters=(chapter,),
    )
    package_dir = tmp_path / "okf" / "vlan-guide"
    package_dir.mkdir(parents=True)
    manual_bytes = b"# VLAN Guide\n\nSource manual bytes.\n"
    (package_dir / "manual.md").write_bytes(manual_bytes)
    monkeypatch.setattr(
        "configbrain.generate_selection_metadata.load_validated_corpus",
        lambda *args: ManualCorpus((document,), ()),
    )
    request_models = []

    def request(system_prompt: str, prompt: dict, config: CloudConfig):
        request_models.append(prompt)
        if "segments" in prompt:
            return {
                "segments": [{
                    "section_id": segment["section_id"],
                    "summary": "Describes VLAN configuration.",
                    "operations": ["Create a VLAN"],
                    "answerable_questions": ["How is a VLAN created?"],
                } for segment in prompt["segments"]],
            }, "test-model"
        section_ids = [
            section_id
            for section in prompt["sections"]
            for operation in section["operations"]
            for section_id in operation["section_ids"]
        ]
        return {
            "summary": "Covers VLAN setup and procedure.",
            "operations": [{
                "text": "Create a VLAN",
                "section_ids": [section_ids[0]],
            }],
            "answerable_questions": [{
                "text": "How is a VLAN created?",
                "section_ids": [section_ids[0]],
            }],
        }, "test-model"

    monkeypatch.setattr("configbrain.generate_selection_metadata.request_json", request)
    output_root = tmp_path / "selection_metadata"
    result = generate_selection_metadata(
        tmp_path / "catalog.json",
        "vlan-guide",
        CloudConfig("https://example.test/chat", "test-key", "test-model"),
        okf_root=tmp_path / "okf",
        metadata_root=output_root,
    )

    assert len(request_models) == 5
    assert result["source_manual_sha256"] == hashlib.sha256(manual_bytes).hexdigest()
    generated_section = result["chapters"][0]["sections"][0]
    assert generated_section["source_range"]["chapter_line_start"] > 0
    assert generated_section["summary"] == "Describes VLAN configuration."
    assert result["chapters"][0]["operations"][0]["section_ids"] == [
        generated_section["section_id"],
    ]
    metadata_path = output_root / "vlan-guide.json"
    saved_metadata = metadata_path.read_bytes()
    assert json.loads(saved_metadata) == result

    monkeypatch.setattr(
        "configbrain.generate_selection_metadata.request_json",
        lambda *args: ({}, "test-model"),
    )
    with pytest.raises(SelectionMetadataError, match="failed validation"):
        generate_selection_metadata(
            tmp_path / "catalog.json",
            "vlan-guide",
            CloudConfig("https://example.test/chat", "test-key", "test-model"),
            okf_root=tmp_path / "okf",
            metadata_root=output_root,
        )
    assert metadata_path.read_bytes() == saved_metadata


def test_single_batch_combines_section_and_chapter_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from configbrain.generate_selection_metadata import _chapter_metadata

    calls = []

    def request(system_prompt: str, prompt: dict, config: CloudConfig):
        calls.append((system_prompt, prompt))
        segment_ids = [segment["section_id"] for segment in prompt["segments"]]
        return {
            "segments": [{
                "section_id": section_id,
                "summary": "Describes VLAN configuration.",
                "operations": ["Create a VLAN"],
                "answerable_questions": [],
            } for section_id in segment_ids],
            "chapter": {
                "summary": "Covers VLAN setup.",
                "operations": [{
                    "text": "Create a VLAN",
                    "section_ids": [segment_ids[0]],
                }],
                "answerable_questions": [],
            },
        }, "test-model"

    monkeypatch.setattr("configbrain.generate_selection_metadata.request_json", request)
    result, model = _chapter_metadata(
        make_chapter(),
        CloudConfig("https://example.test/chat", "test-key", "test-model"),
        None,
    )

    assert len(calls) == 1
    assert "chapter" in calls[0][0]
    assert result["summary"] == "Covers VLAN setup."
    assert model == "test-model"


def test_generation_resumes_from_completed_chapter_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_chapter = make_chapter()
    second_chapter = ManualChapter(
        order=2,
        title="Verifying VLANs",
        html_url="https://www.cisco.com/example/verify.html",
        html_content_sha256="c" * 64,
        pdf_page_index=None,
        pdf_mapping_status="unmapped",
        markdown="## Verification\n\nConfirm the VLAN exists.\n",
    )
    document = ManualDocument(
        document_id="vlan-guide",
        title="VLAN Guide",
        product="catalyst9300",
        release="26.x",
        source_pdf_sha256=PDF_HASH,
        chapters=(first_chapter, second_chapter),
    )
    package_dir = tmp_path / "okf" / "vlan-guide"
    package_dir.mkdir(parents=True)
    manual_bytes = b"# VLAN Guide\n\nSource manual bytes.\n"
    (package_dir / "manual.md").write_bytes(manual_bytes)
    monkeypatch.setattr(
        "configbrain.generate_selection_metadata.load_validated_corpus",
        lambda *args: ManualCorpus((document,), ()),
    )
    requested_orders = []
    fail_on_second_chapter = True

    def request(system_prompt: str, prompt: dict, config: CloudConfig):
        nonlocal fail_on_second_chapter
        order = prompt["chapter"]["order"]
        requested_orders.append(order)
        if order == 2 and fail_on_second_chapter:
            raise SelectionMetadataError("simulated interruption")
        segment_ids = [segment["section_id"] for segment in prompt["segments"]]
        return {
            "segments": [{
                "section_id": section_id,
                "summary": "Explains VLAN configuration.",
                "operations": [],
                "answerable_questions": [],
            } for section_id in segment_ids],
            "chapter": {
                "summary": "Covers VLAN setup.",
                "operations": [],
                "answerable_questions": [],
            },
        }, "test-model"

    monkeypatch.setattr("configbrain.generate_selection_metadata.request_json", request)
    metadata_root = tmp_path / "metadata"
    kwargs = {
        "okf_root": tmp_path / "okf",
        "metadata_root": metadata_root,
    }

    with pytest.raises(SelectionMetadataError, match="simulated interruption"):
        generate_selection_metadata(
            tmp_path / "catalog.json",
            "vlan-guide",
            CloudConfig("https://example.test/chat", "test-key", "test-model"),
            **kwargs,
        )

    checkpoint_path = metadata_root / "vlan-guide.checkpoint"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert [chapter["order"] for chapter in checkpoint["chapters"]] == [1]
    assert requested_orders == [1, 2]

    fail_on_second_chapter = False
    requested_orders.clear()
    result = generate_selection_metadata(
        tmp_path / "catalog.json",
        "vlan-guide",
        CloudConfig("https://example.test/chat", "test-key", "test-model"),
        **kwargs,
    )

    assert requested_orders == [2]
    assert [chapter["order"] for chapter in result["chapters"]] == [1, 2]
    assert not checkpoint_path.exists()


def test_rejects_malformed_metadata_instead_of_silently_falling_back(
    tmp_path: Path,
) -> None:
    metadata_root = tmp_path / "metadata"
    metadata_root.mkdir()
    (metadata_root / "vlan-guide.json").write_text('{"schema_version": 99}')

    with pytest.raises(SelectionMetadataError, match="schema version"):
        load_selection_metadata(
            [{
                "document_id": "vlan-guide",
                "document_type": "configuration_guide",
                "chapters": [{"order": 1, "title": "Configuring VLANs"}],
            }],
            {("vlan-guide", 1): {"title": "Configuring VLANs", "url": SOURCE_URL}},
            metadata_root,
            tmp_path / "okf",
        )


def test_all_guides_mode_skips_fresh_metadata_on_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = tuple(
        SimpleNamespace(
            document_id=document_id,
            chapters=(SimpleNamespace(order=1),),
        )
        for document_id in ("guide-one", "guide-two")
    )
    corpus = ManualCorpus(documents, ())
    catalog = SimpleNamespace(
        documents=[
            {
                "document_id": document.document_id,
                "document_type": "configuration_guide",
                "chapters": [{"order": 1, "title": "Chapter"}],
            }
            for document in documents
        ],
        chapters={},
    )
    fresh_ids = {"guide-one"}
    generated_ids: list[str] = []
    monkeypatch.setattr(
        "configbrain.generate_selection_metadata.load_catalog",
        lambda *args: catalog,
    )
    monkeypatch.setattr(
        "configbrain.generate_selection_metadata._load_corpus",
        lambda *args: corpus,
    )
    monkeypatch.setattr(
        "configbrain.generate_selection_metadata.load_selection_metadata",
        lambda *args: ({document_id: {} for document_id in fresh_ids}, []),
    )

    def generate(document, config, okf_root, metadata_root):
        generated_ids.append(document.document_id)
        fresh_ids.add(document.document_id)
        return {}

    monkeypatch.setattr(
        "configbrain.generate_selection_metadata._generate_document_selection_metadata",
        generate,
    )
    kwargs = {
        "okf_root": tmp_path / "okf",
        "metadata_root": tmp_path / "metadata",
    }

    first = generate_all_selection_metadata(
        tmp_path / "catalog.json",
        CloudConfig("https://example.test/chat", "test-key", "test-model"),
        **kwargs,
    )
    second = generate_all_selection_metadata(
        tmp_path / "catalog.json",
        CloudConfig("https://example.test/chat", "test-key", "test-model"),
        **kwargs,
    )

    assert first["generated_documents"] == ["guide-two"]
    assert first["skipped_documents"] == ["guide-one"]
    assert second["generated_documents"] == []
    assert second["skipped_documents"] == ["guide-one", "guide-two"]
    assert generated_ids == ["guide-two"]


def test_all_guides_mode_continues_after_document_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = tuple(
        SimpleNamespace(
            document_id=document_id,
            chapters=(SimpleNamespace(order=1),),
        )
        for document_id in ("guide-one", "guide-two")
    )
    corpus = ManualCorpus(documents, ())
    catalog = SimpleNamespace(
        documents=[
            {
                "document_id": document.document_id,
                "document_type": "configuration_guide",
                "chapters": [{"order": 1, "title": "Chapter"}],
            }
            for document in documents
        ],
        chapters={},
    )
    generated_ids = []
    monkeypatch.setattr(
        "configbrain.generate_selection_metadata.load_catalog",
        lambda *args: catalog,
    )
    monkeypatch.setattr(
        "configbrain.generate_selection_metadata._load_corpus",
        lambda *args: corpus,
    )
    monkeypatch.setattr(
        "configbrain.generate_selection_metadata.load_selection_metadata",
        lambda *args: ({}, []),
    )

    def generate(document, config, okf_root, metadata_root):
        if document.document_id == "guide-one":
            raise SelectionMetadataError("invalid response")
        generated_ids.append(document.document_id)
        return {}

    monkeypatch.setattr(
        "configbrain.generate_selection_metadata._generate_document_selection_metadata",
        generate,
    )
    result = generate_all_selection_metadata(
        tmp_path / "catalog.json",
        CloudConfig("https://example.test/chat", "test-key", "test-model"),
        okf_root=tmp_path / "okf",
        metadata_root=tmp_path / "metadata",
    )

    assert result["generated_documents"] == ["guide-two"]
    assert result["failed_documents"] == {"guide-one": "invalid response"}
    assert generated_ids == ["guide-two"]
