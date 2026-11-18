import pytest


@pytest.fixture
def report_draft() -> dict:
    citation = {
        "source_id": "source-1", "step_label": "Step 2",
        "table_index_in_context": 1, "row_index": 3,
    }

    def command(cli: str) -> dict:
        return {"command_id": "command-1", "cli": cli, "citations": [citation]}

    return {
        "schema_version": 1, "purpose": "evaluation_only",
        "product": "catalyst9300", "release": "26.x",
        "requirement": "VLAN 120を作成してポートに割り当てる",
        "ready_to_apply": False, "configuration_complete": False,
        "device_changes_performed": False, "evidence_status": "candidates_only",
        "pdf_verification_status": "not_verified",
        "sources": {"source-1": {
            "document_title": "VLAN guide", "product": "catalyst9300", "release": "26.x",
            "chapter_order": 3, "chapter_title": "Configuring VLANs",
            "section_path": ["Configuring VLANs", "Procedure"],
            "chapter_url": "https://www.cisco.com/vlan.html",
            "chapter_line_start": 10, "chapter_line_end": 20,
            "manual_line_start": 100, "manual_line_end": 110,
            "manual_path": "/local/manual.md", "manual_sha256": "a" * 64,
            "source_pdf_path": "/local/manual.pdf", "source_pdf_sha256": "b" * 64,
            "pdf_page_index": 35, "pdf_page_scope": "chapter_start",
        }},
        "items": [{
            "item_id": "item-1", "requirement": "Create VLAN and assign port",
            "status": "needs_input", "assessment": "Port is unspecified.",
            "configuration": [command("vlan 120")],
            "templates": [command("interface <INTERFACE_ID>"), command("switchport access vlan 120")],
            "verification": [command("show vlan id 120")],
            "verification_templates": [command("show interfaces <INTERFACE_ID> switchport")],
            "missing_information": ["Target physical port."],
            "assumptions": ["Check VTP mode."],
            "constraints": [{"explanation": "A restriction.", "citations": [
                {"source_id": "source-1", "quote": "Manual restriction."},
            ]}],
            "evidence": [],
            "withheld_steps": [{
                "source_syntax": "copy running-config startup-config",
                "reason": "Saving was not requested.", "citations": [citation],
            }],
            "inference_candidates": [{
                "description": "Review port protection.",
                "assumptions": ["Requires separate evidence."], "ready_to_apply": False,
            }],
        }],
        "next_checks": ["Confirm physical port."],
        "limitations": ["Original PDF content not verified."],
        "validation_repairs": [{"item_id": "item-1", "validation_error": "Invalid binding shape."}],
    }
