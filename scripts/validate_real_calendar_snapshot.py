"""Reconcile a private frozen vendor calendar against public exchange notices."""

from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.real_data_qualification import reconcile_calendar


PRIVATE = ROOT / ".local_research_data"
OFFICIAL = ROOT / "artifacts" / "stage1_real_data_event_study" / "official_calendar_2024_2026.json"


def validate(private_path: Path) -> dict[str, object]:
    if not private_path.resolve().is_relative_to(PRIVATE.resolve()):
        raise ValueError("Calendar snapshot must remain in the ignored private data area")
    raw = private_path.read_bytes()
    capture = json.loads(raw)
    official = json.loads(OFFICIAL.read_bytes())
    if (capture["schema"] != "private-research-calendar-capture/1"
            or capture["request"] != {"markets": ["SH", "SZ"], "as_of": 20260923}):
        raise ValueError("Unexpected calendar capture request")
    start, end = date.fromisoformat(official["coverage_start"]), date.fromisoformat(official["coverage_end"])
    closed = [(date.fromisoformat(first), date.fromisoformat(last))
              for first, last in official["closed_intervals"]]
    results = {}
    session_hashes = {}
    for market in ("SH", "SZ"):
        sessions = capture["sessions"][market]
        session_hashes[market] = sha256("\n".join(sessions).encode()).hexdigest()
        results[market] = reconcile_calendar(tuple(date.fromisoformat(day) for day in sessions),
                                             start, end, closed)
    left = {day for day in capture["sessions"]["SH"] if official["coverage_start"] <= day <= official["coverage_end"]}
    right = {day for day in capture["sessions"]["SZ"] if official["coverage_start"] <= day <= official["coverage_end"]}
    return {"schema": "stage1-real-calendar-reconciliation/1",
            "provider": capture["provider"], "provider_version": capture["provider_version"],
            "retrieved_at": capture["retrieved_at"], "request": capture["request"],
            "private_snapshot_sha256": sha256(raw).hexdigest(),
            "official_schedule_sha256": sha256(OFFICIAL.read_bytes()).hexdigest(),
            "all_history_vendor_session_counts": {market: len(capture["sessions"][market])
                                                  for market in ("SH", "SZ")},
            "all_history_vendor_session_hashes": session_hashes,
            "reconciliation": results,
            "exchange_mismatch_count_in_window": len(left ^ right),
            "status": "QUALIFIED_FOR_2024_2026_WINDOW" if all(
                item["exact_match"] for item in results.values()) and left == right else "BLOCKED"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-snapshot", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.private_snapshot)
    print(json.dumps(result, sort_keys=True))
    if result["status"] == "BLOCKED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
