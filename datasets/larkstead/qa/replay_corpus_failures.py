"""Verify retained Larkstead historical no-output observations."""

from __future__ import annotations

import sys
from pathlib import Path

from mastervault.corpus_ledger import verify_historical_no_output

REPO_ROOT = Path(__file__).resolve().parents[3]
HISTORY = REPO_ROOT / "datasets" / "larkstead" / "failures" / "historical-ingest.json"


def main() -> int:
    errors = verify_historical_no_output(REPO_ROOT, HISTORY)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print("20/20 historical no-output observations verified (raw hashes retained)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
