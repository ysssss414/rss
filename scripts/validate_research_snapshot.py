"""Offline snapshot validation and structure-only research data-path smoke."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.snapshot import FrozenResearchSnapshot, SNAPSHOT_ID, validate_snapshot


def validate(root: Path) -> dict[str, object]:
    first = validate_snapshot(root)
    second = validate_snapshot(root)
    if first != second:
        raise ValueError("Deterministic snapshot reopen changed validation identity")
    with FrozenResearchSnapshot(root) as snapshot:
        universe = snapshot.load_universe()
        calendar = snapshot.load_calendar()
        code = "600519.SH"
        bars = snapshot.load_daily_bars([code])
        status = snapshot.load_daily_status([code])
        factors = snapshot.load_adjustment_factor([code])
        d8 = snapshot.load_d8([code])
        required = {
            "bars": {"security_id", "trade_date", "open", "high", "low", "close"},
            "status": {"security_id", "trade_date", "is_susp_sec", "is_xr_sec"},
            "factors": {"security_id", "trade_date", "factor"},
            "d8": {"security_id", "effective_date", "single_factor", "event_kind"},
        }
        frames = {"bars": bars, "status": status, "factors": factors, "d8": d8}
        missing = {name: sorted(columns - set(frames[name]))
                   for name, columns in required.items() if columns - set(frames[name])}
        if missing or bars.empty or status.empty or factors.empty:
            raise ValueError(f"Research data-path schema is incomplete: {missing}")
        if (bars.duplicated(["security_id", "trade_date"]).any()
                or status.duplicated(["security_id", "trade_date"]).any()
                or factors.duplicated(["security_id", "trade_date"]).any()
                or not bars.trade_date.max() <= calendar.trade_date.max()):
            raise ValueError("Research smoke key/date contract failed")
        manifest_bytes = (root / "metadata" / "manifest.json").read_bytes()
        smoke = {
            "schema": "stage1-research-data-path-smoke/1", "status": "PASS",
            "snapshot_id": snapshot.snapshot_id, "manifest_sha256": sha256(manifest_bytes).hexdigest(),
            "sample_security": code,
            "loaded_datasets": ["universe", "calendar", "daily_bars", "daily_status",
                                "adjustment_factor", "outcome_adjustment"],
            "schema_consumable": True, "key_contract": "PASS", "date_contract": "PASS",
            "network_or_vendor_session": False, "strategy_metrics_computed": False,
            "scope": "DATA_PATH_ONLY_NO_EDGE_ASSESSMENT",
            "universe_count": len(universe), "calendar_count": len(calendar),
        }
    return {"offline_validation": first, "deterministic_reopen": "PASS", "smoke": smoke}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-root", type=Path,
                        default=ROOT / "data" / "research_snapshots" / SNAPSHOT_ID)
    args = parser.parse_args()
    print(json.dumps(validate(args.snapshot_root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
