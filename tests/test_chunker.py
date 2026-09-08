from app.ingestion.chunker import HtmlChunker, SemanticChunker
from app.ingestion.html_loader import HtmlSection
from app.ingestion.pdf_loader import PdfPage


def test_chunker_keeps_code_block_and_table_as_structured_units() -> None:
    page = PdfPage(
        document_id="test_document",
        page_number=3,
        text=(
            "Configuring VLANs\n\n"
            "Create the VLAN before assigning ports.\n\n"
            "vlan 100\n name USERS\n\n"
            "The port must be configured as an access port."
        ),
        section_title="Configuring VLANs",
        headings=("Configuring VLANs",),
        code_blocks=("vlan 100\n name USERS",),
        tables=((('Command', 'Purpose'), ('vlan 100', 'Create VLAN')),),
    )

    chunks = SemanticChunker(max_chars=500).chunk_page(page)

    assert [chunk.content_type for chunk in chunks] == [
        "concept",
        "configuration_example",
        "concept",
        "parameter_reference",
    ]
    assert chunks[1].text == "vlan 100\n name USERS"
    assert "Command | Purpose" in chunks[3].text
    assert all(chunk.page_number == 3 for chunk in chunks)
    assert all(chunk.section_title == "Configuring VLANs" for chunk in chunks)


def test_chunker_splits_long_normal_text_at_line_boundaries() -> None:
    page = PdfPage(
        document_id="test_document",
        page_number=1,
        text="First paragraph.\n\nSecond paragraph.\n\nThird paragraph.",
        section_title="Overview",
        headings=("Overview",),
        code_blocks=(),
        tables=(),
    )

    chunks = SemanticChunker(max_chars=30).chunk_page(page)

    assert [chunk.text for chunk in chunks] == [
        "First paragraph.",
        "Second paragraph.",
        "Third paragraph.",
    ]


def test_chunker_does_not_split_oversized_command_block() -> None:
    code_block = "interface GigabitEthernet1/0/1\n" + " description Uplink\n" * 20
    page = PdfPage(
        document_id="test_document",
        page_number=4,
        text=f"Interface Settings\n\n{code_block}",
        section_title="Interface Settings",
        headings=("Interface Settings",),
        code_blocks=(code_block,),
        tables=(),
    )

    chunks = SemanticChunker(max_chars=40).chunk_page(page)

    command_chunks = [
        chunk for chunk in chunks if chunk.content_type == "configuration_example"
    ]
    assert len(command_chunks) == 1
    assert command_chunks[0].text == code_block.strip()


def test_chunker_ignores_document_title_table_fragment() -> None:
    page = PdfPage(
        document_id="test_document",
        page_number=2,
        text="Configuring VLANs",
        section_title="Configuring VLANs",
        headings=("Configuring VLANs",),
        code_blocks=(),
        tables=((
            ("", "VLAN Configuration Guide, Cisco IOS XE 26.x.x"),
            ("", "100"),
        ),),
    )

    chunks = SemanticChunker().chunk_page(page)

    assert all("Configuration Guide" not in chunk.text for chunk in chunks)


def test_html_chunker_preserves_section_url_and_structured_units() -> None:
    section = HtmlSection(
        document_id="doc-1",
        section_url="https://example.test/guide/vlans.html",
        section_title="Configuring VLANs",
        text="Create the VLAN before assigning ports.",
        headings=("Configuring VLANs",),
        code_blocks=("Device(config)# vlan 100",),
        tables=((('Command', 'Purpose'), ('vlan 100', 'Create VLAN')),),
    )

    chunks = HtmlChunker().chunk_section(section)

    assert [chunk.content_type for chunk in chunks] == ["configuration_example"]
    assert all(chunk.page_number is None for chunk in chunks)
    assert all(chunk.section_url == section.section_url for chunk in chunks)
    assert "Create the VLAN before assigning ports." in chunks[0].text
    assert "Device(config)# vlan 100" in chunks[0].text
    assert "Command | Purpose" in chunks[0].text


def test_html_chunker_keeps_each_chunk_with_its_own_section_title() -> None:
    sections = [
        HtmlSection(
            document_id="doc-1",
            section_url="https://example.test/guide.html#vlan",
            section_title="Creating VLANs",
            text="Create the VLAN.",
            headings=("Creating VLANs",),
            code_blocks=("vlan 100",),
            tables=(),
        ),
        HtmlSection(
            document_id="doc-1",
            section_url="https://example.test/guide.html#trunk",
            section_title="Configuring VLAN Trunks",
            text="Restrict the allowed VLAN list.",
            headings=("Configuring VLAN Trunks",),
            code_blocks=("switchport trunk allowed vlan 100",),
            tables=(),
        ),
    ]

    chunks = HtmlChunker().chunk_sections(sections)

    assert [chunk.section_title for chunk in chunks] == [
        "Creating VLANs",
        "Configuring VLAN Trunks",
    ]
    assert chunks[0].section_url != chunks[1].section_url


def test_html_chunker_rejects_missing_section_title() -> None:
    section = HtmlSection(
        document_id="doc-1",
        section_url="https://example.test/guide.html#unknown",
        section_title=" ",
        text="Unscoped content.",
        headings=(),
        code_blocks=(),
        tables=(),
    )

    try:
        HtmlChunker().chunk_section(section)
    except ValueError as error:
        assert "has no section title" in str(error)
    else:
        raise AssertionError("expected ValueError")


def test_html_chunker_combines_vlan_creation_commands() -> None:
    section = HtmlSection(
        document_id="doc-1",
        section_url="https://example.test/guide.html#creating-vlan",
        section_title="Creating or Modifying an Ethernet VLAN",
        text="Create the VLAN and optionally assign a name.",
        headings=("Creating or Modifying an Ethernet VLAN",),
        code_blocks=(
            "Device(config)# vlan 100",
            "Device(config-vlan)# name USERS",
        ),
        tables=(),
    )

    chunks = HtmlChunker().chunk_section(section)

    assert len(chunks) == 1
    assert chunks[0].content_type == "configuration_example"
    assert "Device(config)# vlan 100" in chunks[0].text
    assert "Device(config-vlan)# name USERS" in chunks[0].text
