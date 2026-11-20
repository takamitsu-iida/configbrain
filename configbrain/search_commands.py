"""Look up a command in a validated, locally stored command reference."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from configbrain.command_references import CommandReferenceIndex
from configbrain.convert_manual import ConversionError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--reference-root", type=Path, default=Path("data/command_references"))
    parser.add_argument("--command", required=True)
    parser.add_argument("--mode")
    parser.add_argument("--parent")
    args = parser.parse_args(argv)
    try:
        index = CommandReferenceIndex.load(
            args.catalog, args.document_id, args.reference_root
        )
        result = index.search(args.command, mode=args.mode, parent=args.parent)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ConversionError as error:
        print(f"Command search failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
