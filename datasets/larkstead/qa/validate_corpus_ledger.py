"""Validate or regenerate the deterministic Larkstead corpus ledger."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mastervault.corpus_ledger import (
    build_ledger,
    validate_committed_ledger,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
LEDGER = REPO_ROOT / "datasets" / "larkstead" / "corpus-ledger.json"
HISTORY = REPO_ROOT / "datasets" / "larkstead" / "failures" / "historical-ingest.json"
EXCLUSIONS = REPO_ROOT / "datasets" / "larkstead" / "exclusions.json"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write",
        action="store_true",
        help="Regenerate the current ledger (never rewrites immutable historical observations).",
    )
    args = parser.parse_args()
    if args.write:
        ledger, errors = build_ledger(REPO_ROOT, HISTORY, EXCLUSIONS)
        if errors:
            for error in errors:
                print(f"error: {error}", file=sys.stderr)
            return 1
        _write_json(LEDGER, ledger)
    errors = validate_committed_ledger(REPO_ROOT, LEDGER, HISTORY, EXCLUSIONS)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    summary = ledger["summary"]
    print(
        f"corpus ledger valid: raw={summary['raw']} processed={summary['processed']} "
        f"excluded={summary['excluded']} "
        f"historical_no_output={summary['historical_no_output']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
