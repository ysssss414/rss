"""Materialize frozen-Entry T-close context without loading outcomes or PnL."""

from __future__ import annotations

from collections import deque
import csv
from hashlib import sha256
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb
import pandas as pd

from research.context_features_v1 import (AsOfDaily, PRIMARY_FEATURES, market_regime,
                                          ratio_slope, score, style_regime, tercile_boundaries,
                                          trailing_percentile)
from research.snapshot import FrozenResearchSnapshot


SNAPSHOT = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
PUBLIC = ROOT / "artifacts/stage1_context_discovery_v1"
PRIVATE = ROOT / ".local_research_data/stage1_context_discovery_v1"
ENTRIES = ROOT / "artifacts/stage1_real_strategy_smoke_v1/entries.csv"
ENTRY_SHA = "82d16ddf0f3c1af3c5015dd34ae08b9bbe83e23ed90c03b2c8b7ade7ec4bb0f1"
SNAPSHOT_SHA = "a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67"


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_bytes((json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                 allow_nan=False) + "\n").encode("utf-8"))


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _median(values: list[float], minimum: int) -> float | None:
    return statistics.median(values) if len(values) >= minimum else None


def _daily_base(snapshot: FrozenResearchSnapshot, calendar: list[str]) -> list[dict]:
    """Stream D3/D4 by date; each stock state and daily accumulator is <= current date."""
    paths = snapshot._files("daily_bars"), snapshot._files("daily_status")
    sql = """SELECT b.trade_date, b.security_id, b.close, b.amount,
                    s.preclose, s.high_limited, s.low_limited, s.is_susp_sec
             FROM read_parquet(?, hive_partitioning=false) b
             LEFT JOIN read_parquet(?, hive_partitioning=false) s
               USING (trade_date, security_id)
             ORDER BY b.trade_date, b.security_id"""
    cursor = snapshot.con.execute(sql, list(paths))
    positions = {day: i for i, day in enumerate(calendar)}
    states: dict[str, tuple[int, int, bool, float, deque[float]]] = {}
    daily: list[dict] = []
    current_day = ""
    acc: dict = {}

    def finish() -> None:
        if not current_day:
            return
        n = acc["n_bars"]
        coverage = acc["n_valid"] / n
        qualified = coverage >= .98
        prior_qualified = bool(daily) and daily[-1]["status_coverage"] >= .98
        high_history_qualified = qualified and len(daily) >= 3 and all(
            item["status_coverage"] >= .98 for item in daily[-3:])
        top = sorted(acc["top"], key=lambda x: (-x[0], x[1]))[:100]
        top_return = [x[2] for x in top if x[2] is not None]
        top_ma20 = [x[3] for x in top if x[3] is not None]
        top_five = [x[4] for x in top if x[4] is not None]
        prior_lu = acc["prior_lu"]
        prior_high = acc["prior_high"]
        advance = acc["advance"]
        row = {
            "trade_date": current_day, "market_turnover": acc["amount"],
            "n_bars": n, "n_valid_status": acc["n_valid"], "status_coverage": coverage,
            "source_quality_pass": qualified, "prior_source_quality_pass": prior_qualified,
            "high_board_history_quality_pass": high_history_qualified,
            "limit_up_count": acc["lu"] if qualified else None,
            "limit_down_count": acc["ld"] if qualified else None,
            "strong_up_count": acc["strong_up"] if qualified else None,
            "strong_down_count": acc["strong_down"] if qualified else None,
            "market_breadth_score": ((acc["lu"] + acc["strong_up"] - acc["ld"] - acc["strong_down"])
                                      / acc["n_valid"] if qualified and acc["n_valid"] else None),
            "prior_limit_up_n": len(prior_lu) if qualified and prior_qualified else None,
            "prior_limit_up_next_day_median_return": _median(prior_lu, 5) if qualified and prior_qualified else None,
            "prior_limit_up_next_day_mean_return": statistics.fmean(prior_lu) if len(prior_lu) >= 5 and qualified and prior_qualified else None,
            "prior_limit_up_next_day_positive_fraction": sum(x > 0 for x in prior_lu) / len(prior_lu) if len(prior_lu) >= 5 and qualified and prior_qualified else None,
            "prior_limit_up_next_day_deep_negative_fraction": sum(x <= -.05 for x in prior_lu) / len(prior_lu) if len(prior_lu) >= 5 and qualified and prior_qualified else None,
            "previous_high_position_n": len(prior_high) if high_history_qualified else None,
            "high_position_negative_feedback_rate": sum(x <= -.05 for x in prior_high) / len(prior_high) if high_history_qualified and len(prior_high) >= 5 else None,
            "high_position_median_return": _median(prior_high, 5) if high_history_qualified else None,
            "previous_two_board_n": len(advance) if high_history_qualified else None,
            "high_board_advancement_rate": sum(advance) / len(advance) if high_history_qualified and len(advance) >= 5 else None,
            "high_board_count": acc["high_boards"] if high_history_qualified else None,
            "maximum_consecutive_board_height": acc["max_streak"] if high_history_qualified else None,
            "top100_amount_n": len(top), "top100_return_n": len(top_return),
            "top100_above_ma20_n": len(top_ma20), "top100_five_day_n": len(top_five),
            "top100_median_return": _median(top_return, 80) if qualified else None,
            "top100_above_ma20_fraction": sum(top_ma20) / len(top_ma20) if qualified and len(top_ma20) >= 80 else None,
            "top100_median_five_day_return": _median(top_five, 80) if qualified else None,
        }
        daily.append(row)

    while batch := cursor.fetchmany(100000):
        for day_value, code, close, amount, preclose, upper, lower, suspended in batch:
            day = day_value.isoformat()
            if day != current_day:
                finish()
                current_day = day
                acc = {"n_bars": 0, "n_valid": 0, "amount": 0.0, "lu": 0, "ld": 0,
                       "strong_up": 0, "strong_down": 0, "prior_lu": [], "prior_high": [],
                       "advance": [], "high_boards": 0, "max_streak": 0, "top": []}
            acc["n_bars"] += 1
            acc["amount"] += amount
            valid = (preclose is not None and preclose > 0 and upper is not None
                     and lower is not None and not suspended)
            ret = close / preclose - 1 if valid else None
            is_lu = bool(valid and upper > 0 and abs(close - upper) <= .005)
            is_ld = bool(valid and lower > 0 and abs(close - lower) <= .005)
            old = states.get(code)
            adjacent = old is not None and old[0] == positions[day] - 1
            streak = old[1] + 1 if is_lu and adjacent else 1 if is_lu else 0
            level = old[3] * (1 + ret) if valid and adjacent else 1.0
            history = deque(old[4], maxlen=21) if valid and adjacent else deque(maxlen=21)
            history.append(level)
            above_ma20 = level > statistics.fmean(list(history)[-20:]) if valid and len(history) >= 20 else None
            five_day = level / list(history)[-6] - 1 if valid and len(history) >= 6 else None
            states[code] = (positions[day], streak, is_lu, level, history)
            acc["top"].append((amount, code, ret, above_ma20, five_day))
            if valid:
                acc["n_valid"] += 1
                acc["lu"] += is_lu
                acc["ld"] += is_ld
                acc["strong_up"] += ret >= .05
                acc["strong_down"] += ret <= -.05
                acc["high_boards"] += streak >= 3
                acc["max_streak"] = max(acc["max_streak"], streak)
                if adjacent and old[2]:
                    acc["prior_lu"].append(ret)
                if adjacent and old[1] >= 3:
                    acc["prior_high"].append(ret)
                if adjacent and old[1] >= 2:
                    acc["advance"].append(is_lu)
    finish()
    if [row["trade_date"] for row in daily] != calendar:
        raise ValueError("BLOCKED_MARKET_CONTEXT_PIT: market calendar coverage")
    return daily


def _derived_daily(rows: list[dict]) -> list[dict]:
    raw = {name: [] for name in ("high_board_count", "maximum_consecutive_board_height",
                               "high_board_advancement_rate", "prior_limit_up_next_day_median_return",
                               "high_position_negative_feedback_rate", "top100_median_return",
                               "top100_above_ma20_fraction", "top100_median_five_day_return")}
    turnover: list[float] = []
    for row in rows:
        turnover.append(row["market_turnover"])
        row["turnover_ma20"] = statistics.fmean(turnover[-20:]) if len(turnover) >= 20 else None
        row["turnover_ratio_to_ma20"] = (turnover[-1] / row["turnover_ma20"]
                                         if row["turnover_ma20"] else None)
        row["turnover_20d_percentile"] = trailing_percentile(turnover, width=20, minimum=20)
        row["turnover_60d_percentile"] = trailing_percentile(turnover, width=60, minimum=60)
        row["turnover_3d_slope"] = ratio_slope(turnover, 3)
        for name in raw:
            raw[name].append(row[name])
            row[name + "_p60"] = trailing_percentile(raw[name], width=60, minimum=20)
        def centered(name: str, invert: bool = False) -> float | None:
            value = row[name + "_p60"]
            return (0.5 - value if invert else value - 0.5) if value is not None else None
        shortline = [centered("high_board_count"), centered("maximum_consecutive_board_height"),
                     centered("high_board_advancement_rate"),
                     centered("prior_limit_up_next_day_median_return"),
                     centered("high_position_negative_feedback_rate", invert=True)]
        trend = [centered("top100_median_return"), centered("top100_above_ma20_fraction"),
                 centered("top100_median_five_day_return")]
        row["shortline_style_score"], row["shortline_component_n"] = score(shortline, 3)
        row["trend_style_score"], row["trend_component_n"] = score(trend, 3)
        row["style_differential"] = (row["shortline_style_score"] - row["trend_style_score"]
                                     if row["shortline_style_score"] is not None
                                     and row["trend_style_score"] is not None else None)
        row["style_regime"] = style_regime(row["style_differential"])
        row["market_risk_regime"], row["market_risk_vote_n"] = market_regime(
            row["turnover_20d_percentile"], row["market_breadth_score"],
            row["prior_limit_up_next_day_median_return"],
            row["high_position_negative_feedback_rate"])
    return rows


def build() -> dict:
    contract = json.loads((PUBLIC / "context_feature_contract_v1.json").read_text(encoding="utf-8"))
    if contract["id"] != "ENTRY_CONTEXT_FEATURES_V1" or tuple(contract["primary_features"]) != PRIMARY_FEATURES:
        raise ValueError("BLOCKED_CONTEXT_FEATURE_LEAKAGE: contract mismatch")
    if digest(ENTRIES) != ENTRY_SHA:
        raise ValueError("BLOCKED_CONTEXT_POPULATION_DRIFT")
    with ENTRIES.open(encoding="utf-8", newline="") as stream:
        entries = list(csv.DictReader(stream))
    if (len(entries) != 505 or sum(x["execution_intent"] == "NORMAL_CLOSE_INTENT" for x in entries) != 242
            or sum(x["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT" for x in entries) != 263):
        raise ValueError("BLOCKED_CONTEXT_POPULATION_DRIFT: counts")
    with FrozenResearchSnapshot(SNAPSHOT) as snapshot:
        if snapshot.validation["manifest_sha256"] != SNAPSHOT_SHA:
            raise ValueError("BLOCKED_CONTEXT_POPULATION_DRIFT: snapshot")
        calendar = [x.date().isoformat() for x in snapshot.load_calendar().trade_date]
        daily_rows = _derived_daily(_daily_base(snapshot, calendar))
    daily = {x["trade_date"]: x for x in daily_rows}
    entry_rows = []
    for entry in sorted(entries, key=lambda x: (x["security_id"], x["entry_date"], x["observation_instance_id"])):
        day = entry["entry_date"]
        market = AsOfDaily(daily, day).get(day)
        if market is None:
            raise ValueError("BLOCKED_MARKET_CONTEXT_PIT: missing Entry date")
        entry_rows.append({"entry_event_id": sha256(f'{entry["security_id"]}|{day}|{entry["observation_instance_id"]}'.encode()).hexdigest(),
                           "security_id": entry["security_id"], "entry_date": day,
                           "execution_intent": entry["execution_intent"], "entry_type": entry["entry_reasons"],
                           "feature_source_max_date": day, **{k: v for k, v in market.items() if k != "trade_date"}})
    if len({x["entry_event_id"] for x in entry_rows}) != 505:
        raise ValueError("BLOCKED_CONTEXT_POPULATION_DRIFT: duplicate context key")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        for name, rows in (("entry_context_features_v1.parquet", entry_rows),
                           ("daily_market_style_context_v1.parquet", daily_rows)):
            con.register("context_rows", pd.DataFrame(rows))
            con.execute("COPY (SELECT * FROM context_rows) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                        [str(PRIVATE / name)])
            con.unregister("context_rows")
    finally:
        con.close()
    inventory = [{"feature": name, "n_available": sum(x[name] is not None for x in entry_rows),
                  "n_missing": sum(x[name] is None for x in entry_rows),
                  "source_max_date": "ENTRY_T_CLOSE", "diagnostic": "PRE_REGISTERED_TERCILES"}
                 for name in PRIMARY_FEATURES]
    write_csv(PUBLIC / "context_feature_inventory.csv", inventory)
    development = [x for x in entry_rows if x["entry_date"] < "2026-01-01"
                   and x["execution_intent"] == "NORMAL_CLOSE_INTENT"]
    if len(development) != 174:
        raise ValueError("BLOCKED_CONTEXT_POPULATION_DRIFT: development count")
    bins = {name: tercile_boundaries([x[name] for x in development if x[name] is not None])
            for name in PRIMARY_FEATURES}
    write_json(PUBLIC / "context_bins_v1.json", {
        "id": "CONTEXT_DEVELOPMENT_TERCILES_V1", "population": "174 frozen 2024-2025 normal-close Entries",
        "source": "ENTRY_CONTEXT_FEATURES_V1_ONLY_NO_OUTCOMES", "bin_semantics": "LOW <= p33; MID <= p67; HIGH > p67; missing separate",
        "boundaries": bins, "duplicate_boundary_features": [name for name, pair in bins.items() if pair[0] == pair[1]]})
    snapshot_datasets = sorted(snapshot.manifest["datasets"])
    theme_code_paths = ["research/theme_pool_analyzer.py", "theme_pool_analyzer.py",
                        "artifacts/theme_pool_analyzer", ".local_research_data/theme_pool_analyzer"]
    theme = {"status": "THEME_CONTEXT_DEFERRED_DATA_UNQUALIFIED", "snapshot_theme_dataset": False,
             "snapshot_datasets_inspected": snapshot_datasets,
             "repo_theme_paths_inspected": theme_code_paths,
             "repo_theme_paths_found": [path for path in theme_code_paths if (ROOT / path).exists()],
             "pit_membership_2024_2026": False,
             "daily_strength_rank_2024_2026": False, "historical_reproducibility": False,
             "reason": "Frozen snapshot and inspected repository provide no dated security-theme membership plus dated theme strength/rank; no retrospective mainline labels or proxy substitution.",
             "market_style_can_continue": True}
    write_json(PUBLIC / "theme_context_data_qualification.json", theme)
    receipt = {"status": "PASS", "entry_n": 505, "normal_n": 242, "board_n": 263,
               "daily_sessions": len(daily_rows), "source_entry_sha256": ENTRY_SHA,
               "snapshot_manifest_sha256": SNAPSHOT_SHA,
               "contract_sha256": digest(PUBLIC / "context_feature_contract_v1.json"),
               "code_sha256": {name: digest(ROOT / name) for name in (
                   "research/context_features_v1.py", "scripts/build_entry_context_features_v1.py")},
               "daily_low_status_coverage": [{"date": x["trade_date"], "coverage": x["status_coverage"],
                                              "missing_status": x["n_bars"] - x["n_valid_status"]}
                                             for x in daily_rows if not x["source_quality_pass"]],
               "parquet_sha256": {name: digest(PRIVATE / name) for name in (
                   "entry_context_features_v1.parquet", "daily_market_style_context_v1.parquet")},
               "inventory_sha256": digest(PUBLIC / "context_feature_inventory.csv"),
               "bins_sha256": digest(PUBLIC / "context_bins_v1.json"),
               "theme_qualification_sha256": digest(PUBLIC / "theme_context_data_qualification.json"),
               "forbidden_economic_datasets_loaded": False,
               "snapshot_integrity_note": contract["snapshot_integrity_note"]}
    write_json(PUBLIC / "context_feature_builder_receipt.json", receipt)
    return receipt


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, sort_keys=True, indent=2))
