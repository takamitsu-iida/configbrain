from app.ingestion.chunker import SemanticChunker
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
