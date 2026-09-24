"""Freeze or validate REAL_RESEARCH_SNAPSHOT_V1."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.snapshot import SNAPSHOT_ID, freeze_snapshot, validate_snapshot


DEFAULT_ROOT = ROOT / "data" / "research_snapshots" / SNAPSHOT_ID


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store-root", type=Path, default=ROOT / "data" / "market_store")
    parser.add_argument("--snapshot-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--code-commit")
    args = parser.parse_args()
    if args.validate:
        result = validate_snapshot(args.snapshot_root)
    else:
        commit = args.code_commit or subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        result = freeze_snapshot(args.store_root, args.snapshot_root,
                                 cutoff=date(2026, 9, 23), code_commit=commit)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
