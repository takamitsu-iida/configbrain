import json
from pathlib import Path

from scripts.index_manuals import select_manuals


def test_select_manuals_reads_download_metadata(tmp_path: Path) -> None:
    metadata_dir = tmp_path / "data"
    raw_dir = metadata_dir / "raw"
    raw_dir.mkdir(parents=True)
    (metadata_dir / "manuals.json").write_text(
        json.dumps(
            [
                {
                    "document_id": "doc-1",
                    "title": "Test Guide",
                    "filename": "doc-1.pdf",
                    "url": "https://www.cisco.com/test.pdf",
                }
            ]
        ),
        encoding="utf-8",
    )

    records = select_manuals(tmp_path, Path("data/raw/doc-1.pdf"), False)

    assert records[0]["document_id"] == "doc-1"