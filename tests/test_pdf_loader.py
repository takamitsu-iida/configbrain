from pathlib import Path

import pymupdf

from app.ingestion.pdf_loader import PdfLoader


def test_pdf_loader_preserves_page_and_source_metadata(tmp_path: Path) -> None:
    pdf_path = tmp_path / "manual.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Configuring VLANs\n"
        "vlan 100\n"
        " name USERS\n"
        "This paragraph explains the configuration.",
    )
    document.save(pdf_path)
    document.close()

    pages = PdfLoader(pdf_path, "c9300_iosxe26_vlan_cg").load()

    assert len(pages) == 1
    assert pages[0].document_id == "c9300_iosxe26_vlan_cg"
    assert pages[0].page_number == 1
    assert "vlan 100" in pages[0].text
    assert pages[0].section_title == "Configuring VLANs"
    assert pages[0].headings == ("Configuring VLANs",)
    assert "vlan 100\n name USERS" in pages[0].code_blocks


def test_pdf_loader_extracts_headings_code_and_tables(tmp_path: Path) -> None:
    pdf_path = tmp_path / "structured-manual.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Interface Settings\ninterface GigabitEthernet1/0/1")
    page.insert_text((72, 110), "description Uplink\nno shutdown")
    page.draw_rect((72, 140, 260, 190))
    page.draw_line((72, 165), (260, 165))
    page.draw_line((166, 140), (166, 190))
    page.insert_text((78, 158), "Command")
    page.insert_text((172, 158), "Purpose")
    page.insert_text((78, 182), "no shutdown")
    page.insert_text((172, 182), "Enable port")
    document.save(pdf_path)
    document.close()

    page_record = PdfLoader(pdf_path, "test_document").load()[0]

    assert "Interface Settings" in page_record.headings
    assert "interface GigabitEthernet1/0/1" in page_record.code_blocks[0]
    assert page_record.tables == (
        (("Command", "Purpose"), ("no shutdown", "Enable port")),
    )