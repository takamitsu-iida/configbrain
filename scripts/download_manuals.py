#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse
from urllib.request import Request, urlopen


MANUALS = (
    {
        "document_id": "c9300_iosxe26_vlan_cg",
        "title": "VLAN Configuration Guide",
        "filename": "c9300_iosxe26_vlan_cg.pdf",
        "url": "https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg.pdf",
    },
    {
        "document_id": "c9300_iosxe26_int_hw_cg",
        "title": "Interface and Hardware Components Configuration Guide",
        "filename": "c9300_iosxe26_int_hw_cg.pdf",
        "url": "https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/int_hw/b_26x_int_and_hw_9300_cg.pdf",
    },
    {
        "document_id": "c9300_iosxe26_sys_mgmt_cg",
        "title": "System Management Configuration Guide",
        "filename": "c9300_iosxe26_sys_mgmt_cg.pdf",
        "url": "https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/sys_mgmt/b_26x_sys_mgmt_9300_cg.pdf",
    },
)


def download_manuals(root: Path, force: bool = False) -> list[dict[str, object]]:
    raw_dir = root / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []

    for manual in MANUALS:
        destination = raw_dir / str(manual["filename"])
        if destination.exists() and not force:
            raise FileExistsError(
                f"{destination} already exists; use --force to replace it"
            )
        _validate_url(str(manual["url"]))
        digest, size = _download(str(manual["url"]), destination)
        records.append(
            {
                **manual,
                "sha256": digest,
                "size_bytes": size,
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    metadata_path = root / "data" / "manuals.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(records, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    return records


def _download(url: str, destination: Path) -> tuple[str, int]:
    request = Request(url, headers={"User-Agent": "ConfigBrain/0.1 document fetcher"})
    digest = hashlib.sha256()
    size = 0
    with urlopen(request, timeout=60) as response:
        with NamedTemporaryFile("wb", dir=destination.parent, delete=False) as file:
            temporary_path = Path(file.name)
            while chunk := response.read(1024 * 1024):
                file.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    temporary_path.replace(destination)
    return digest.hexdigest(), size


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "www.cisco.com":
        raise ValueError(f"Refusing non-Cisco HTTPS URL: {url}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the ConfigBrain manuals")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--force", action="store_true")
    if len(sys.argv) == 1:
        parser.print_help()
        return
    args = parser.parse_args()
    records = download_manuals(args.root.resolve(), force=args.force)
    for record in records:
        print(f"Downloaded {record['document_id']} ({record['size_bytes']} bytes)")


if __name__ == "__main__":
    main()