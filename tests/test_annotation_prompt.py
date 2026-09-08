from app.llm.annotation_prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    annotation_metadata,
    build_annotation_request,
)


def test_annotation_request_is_versioned_and_reproducible() -> None:
    source_text = "Device(config)# vlan 100\nDevice(config-vlan)# name USERS"

    first = build_annotation_request(source_text)
    second = build_annotation_request(source_text)

    assert first.prompt_version == PROMPT_VERSION == "rag-annotation-v1"
    assert first.prompt_hash == second.prompt_hash
    assert first.input_hash == second.input_hash
    assert first.cache_key() == second.cache_key()
    assert SYSTEM_PROMPT in first.system_prompt
    assert source_text in first.user_prompt


def test_different_source_text_has_different_input_hash() -> None:
    first = build_annotation_request("vlan 100")
    second = build_annotation_request("vlan 200")

    assert first.prompt_hash == second.prompt_hash
    assert first.input_hash != second.input_hash
    assert first.cache_key() != second.cache_key()


def test_annotation_metadata_records_response_hash() -> None:
    request = build_annotation_request("vlan 100")

    metadata = annotation_metadata(
        request,
        model="test-model",
        created_at="2026-09-08T00:00:00Z",
        response={"content_type": "configuration_example"},
    )

    assert metadata["model"] == "test-model"
    assert metadata["prompt_version"] == PROMPT_VERSION
    assert len(metadata["prompt_hash"]) == 64
    assert len(metadata["input_hash"]) == 64
    assert len(metadata["response_hash"]) == 64