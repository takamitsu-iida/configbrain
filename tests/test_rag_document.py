from app.models.rag_document import (
    AnnotationStatus,
    Command,
    ContentType,
    RagDocument,
    verify_annotation,
)


SOURCE_URL = "https://www.cisco.com/c/en/us/td/docs/guide.html"
SECTION_URL = f"{SOURCE_URL}#creating-vlan"


def make_vlan_document() -> RagDocument:
    return RagDocument(
        block_id="doc-1:creating-vlan:001",
        document_id="doc-1",
        source_url=SOURCE_URL,
        section_url=SECTION_URL,
        section_title="Creating or Modifying an Ethernet VLAN",
        content_type=ContentType.CONFIGURATION_EXAMPLE,
        product="catalyst9300",
        os="IOS XE",
        os_version="26.x",
        source_text=(
            "Create a normal-range VLAN.\n"
            "Use VLAN configuration mode.\n"
            "Device(config)# vlan 100\n"
            "Device(config-vlan)# name USERS"
        ),
        parent_block_id="doc-1:creating-vlan:parent",
        prerequisites=["Use VLAN configuration mode."],
        commands=[
            {"text": "Device(config)# vlan 100", "role": "enter_vlan"},
            {"text": "Device(config-vlan)# name USERS", "role": "set_name"},
        ],
        keywords=["VLAN", "normal-range VLAN"],
        annotation_status=AnnotationStatus.NEEDS_REVIEW,
        annotation_confidence=0.95,
    )


def test_vlan_schema_accepts_parent_child_document() -> None:
    document = verify_annotation(make_vlan_document())

    assert document.annotation_status is AnnotationStatus.VERIFIED
    assert document.validation_errors == []
    assert document.commands[0].role == "enter_vlan"


def test_schema_rejects_command_missing_from_source() -> None:
    document = RagDocument.model_validate(
        make_vlan_document().model_dump(
            mode="json",
            exclude={"commands"},
        )
        | {"commands": [{"text": "name ADMINS", "role": "set_name"}]}
    )

    verified = verify_annotation(document)

    assert verified.annotation_status is AnnotationStatus.REJECTED
    assert verified.validation_errors == ["command not found in source_text: name ADMINS"]


def test_source_validation_allows_whitespace_differences_only() -> None:
    document = make_vlan_document().model_copy(
        update={
            "commands": [
                Command(text="Device(config)#   vlan\n100", role="enter_vlan"),
                Command(text="Device(config-vlan)# name   USERS", role="set_name"),
            ],
            "prerequisites": [],
        }
    )

    verified = verify_annotation(document)

    assert verified.annotation_status is AnnotationStatus.VERIFIED


def test_source_validation_rejects_case_changes_and_missing_verification() -> None:
    document = make_vlan_document().model_copy(
        update={
            "commands": [Command(text="Device(config)# VLAN 100", role="enter_vlan")],
            "prerequisites": [],
            "verification_commands": ["show vlan brief"],
        }
    )

    verified = verify_annotation(document)

    assert verified.annotation_status is AnnotationStatus.REJECTED
    assert verified.validation_errors == [
        "command not found in source_text: Device(config)# VLAN 100",
        "verification command not found in source_text: show vlan brief",
    ]


def test_source_validation_rejects_unverifiable_prerequisite() -> None:
    document = make_vlan_document().model_copy(
        update={"prerequisites": ["The device must be in VTP server mode."]}
    )

    verified = verify_annotation(document)

    assert verified.annotation_status is AnnotationStatus.REJECTED
    assert verified.validation_errors == [
        "prerequisite not found in source_text: The device must be in VTP server mode."
    ]


def test_json_schema_declares_core_metadata() -> None:
    schema = RagDocument.model_json_schema()
    required = set(schema["required"])

    assert {"block_id", "source_text", "section_url", "section_title"} <= required
    assert schema["properties"]["content_type"]["$ref"].endswith("ContentType")
