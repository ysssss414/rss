import csv
from hashlib import sha256
import json
from pathlib import Path

import pytest
import duckdb

from research.context_discovery_v1 import bucket, cluster_contrast, summary
from research.context_features_v1 import (AsOfDaily, PRIMARY_FEATURES, market_regime,
                                          score, style_regime, tercile_boundaries,
                                          trailing_percentile)
from scripts.run_context_discovery_v1 import _join, read_parquet


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "artifacts/stage1_context_discovery_v1"
PRIVATE = ROOT / ".local_research_data/stage1_context_discovery_v1"


def test_asof_guard_and_prefix_only_percentile():
    days = {"2026-09-22": {"value": 1}, "2026-09-23": {"value": 1000}}
    assert AsOfDaily(days, "2026-09-22").get("2026-09-22") == {"value": 1}
    with pytest.raises(ValueError, match="FEATURE_LEAKAGE"):
        AsOfDaily(days, "2026-09-22").get("2026-09-23")
    values = [float(x) for x in range(1, 21)]
    assert trailing_percentile(values, width=20, minimum=20) == 1.0
    assert trailing_percentile(values[:19], width=20, minimum=20) is None
    future = values + [1000.0]
    assert trailing_percentile(future[:20], width=20, minimum=20) == trailing_percentile(
        values, width=20, minimum=20)


def test_fixed_regime_boundaries_and_no_missing_imputation():
    assert market_regime(.8, .1, .02, None) == ("RISK_ON", 3)
    assert market_regime(None, None, .02, None) == (None, 1)
    assert score([.1, None, -.2], 3) == (None, 2)
    assert style_regime(.11) == "SHORTLINE_DOMINANT"
    assert style_regime(-.11) == "TREND_DOMINANT"
    assert style_regime(.10) == "MIXED"
    assert style_regime(None) is None
    assert tercile_boundaries([1, 2, 3, 4, 5, 6]) == pytest.approx([2.6666666667, 4.3333333333])
    assert [bucket(x, [1, 2]) for x in (None, 1, 1.5, 2.1)] == ["MISSING", "LOW", "MID", "HIGH"]


def test_summary_keeps_censored_returns_and_tail_denominators_separate():
    base = {"entry_event_id": "A", "security_id": "S", "tp5_net": -.1,
            "tp5_success": False, "h5_signal": -.2, "tail10": True, "tail20": True,
            "time_h1_net": -.05, "time_h3_net": -.1, "time_h5_net": -.2}
    other = {**base, "entry_event_id": "B", "tp5_net": None, "tp5_success": None,
             "h5_signal": None, "tail10": None, "tail20": None,
             "time_h3_net": None, "time_h5_net": None}
    result = summary([base, other])
    assert result["n_events"] == 2 and result["n_securities"] == 1
    assert result["tp5_closed_n"] == 1 and result["tp5_net_mean"] == -.1
    assert result["left_tail_h5_10_n"] == 1 and result["left_tail_h5_10_fraction"] == 1
    assert result["sample_guardrail"] == "N_LT_10_NO_SHORTLIST"


def test_cluster_bootstrap_is_deterministic_and_keeps_repeated_security():
    rows = [{"security_id": "A", "side": "GOOD", "tp5_net": x, "tail10": False}
            for x in (.1, -.2)] + [
            {"security_id": "B", "side": "BAD", "tp5_net": -.05, "tail10": True},
            {"security_id": "C", "side": "BAD", "tp5_net": -.1, "tail10": True}]
    a = cluster_contrast(rows, "side", "GOOD", "BAD", "TEST")
    b = cluster_contrast(rows, "side", "GOOD", "BAD", "TEST")
    assert a == b and a["security_clusters"] == 3
    assert a["n_favorable"] == 2 and a["n_adverse"] == 2
    assert a["tp5_net_mean_difference"] == pytest.approx(.025)


def test_feature_builder_has_no_outcome_access_or_future_market_join():
    builder = (ROOT / "scripts/build_entry_context_features_v1.py").read_text(encoding="utf-8")
    primitive = (ROOT / "research/context_features_v1.py").read_text(encoding="utf-8")
    for forbidden in ("entry_event_outcome_v1.parquet", "entry_trade_pnl_v1.parquet",
                      "load_d8(", "from research.exit_pnl", "from research.event_study"):
        assert forbidden not in builder + primitive
    assert "ORDER BY b.trade_date, b.security_id" in builder
    assert "AsOfDaily(daily, day).get(day)" in builder
    receipt = json.loads((PUBLIC / "context_feature_builder_receipt.json").read_text(encoding="utf-8"))
    assert receipt["forbidden_economic_datasets_loaded"] is False
    assert receipt["daily_low_status_coverage"] == [
        {"date": "2026-07-10", "coverage": pytest.approx(.8060104026), "missing_status": 1007}]


def test_frozen_features_bins_and_theme_qualification():
    features = read_parquet(PRIVATE / "entry_context_features_v1.parquet")
    assert len(features) == len({r["entry_event_id"] for r in features}) == 505
    assert sum(r["execution_intent"] == "NORMAL_CLOSE_INTENT" for r in features) == 242
    assert all(r["feature_source_max_date"] == r["entry_date"] for r in features)
    assert not any(token in key.lower() for key in features[0] for token in ("outcome", "pnl", "d8", "h1", "h3", "h5"))
    inventory = list(csv.DictReader((PUBLIC / "context_feature_inventory.csv").open(encoding="utf-8")))
    assert [r["feature"] for r in inventory] == list(PRIMARY_FEATURES)
    assert all(int(r["n_available"]) + int(r["n_missing"]) == 505 for r in inventory)
    bins = json.loads((PUBLIC / "context_bins_v1.json").read_text(encoding="utf-8"))
    assert set(bins["boundaries"]) == set(PRIMARY_FEATURES)
    assert bins["population"].startswith("174 frozen")
    contract = json.loads((PUBLIC / "context_feature_contract_v1.json").read_text(encoding="utf-8"))
    assert set(contract["primary_feature_provenance"]) == set(PRIMARY_FEATURES)
    assert all(set(item) == {"source_dataset", "timestamp_semantics", "available_by_entry_T",
                             "calculation_window", "missing_policy", "version"}
               for item in contract["primary_feature_provenance"].values())
    theme = json.loads((PUBLIC / "theme_context_data_qualification.json").read_text(encoding="utf-8"))
    assert theme["status"] == "THEME_CONTEXT_DEFERRED_DATA_UNQUALIFIED"
    assert not theme["pit_membership_2024_2026"]


def test_independent_one_day_market_source_reconciliation():
    day = "2024-07-19"
    snapshot = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
    bars = str(snapshot / "daily_bars/year=2024/month=07/part.parquet")
    status = str(snapshot / "daily_status/year=2024/month=07/part.parquet")
    con = duckdb.connect()
    try:
        amount, n_valid, lu, ld, strong_up, strong_down = con.execute("""
            SELECT sum(b.amount), count(*) FILTER (WHERE s.preclose>0),
                   count(*) FILTER (WHERE s.high_limited>0 AND abs(b.close-s.high_limited)<=.005),
                   count(*) FILTER (WHERE s.low_limited>0 AND abs(b.close-s.low_limited)<=.005),
                   count(*) FILTER (WHERE s.preclose>0 AND b.close/s.preclose-1>=.05),
                   count(*) FILTER (WHERE s.preclose>0 AND b.close/s.preclose-1<=-.05)
            FROM read_parquet(?) b LEFT JOIN read_parquet(?) s USING (trade_date, security_id)
            WHERE b.trade_date=CAST(? AS DATE)""", [bars, status, day]).fetchone()
    finally:
        con.close()
    daily = read_parquet(PRIVATE / "daily_market_style_context_v1.parquet")
    row = next(x for x in daily if x["trade_date"] == day)
    assert row["market_turnover"] == pytest.approx(amount)
    assert row["n_valid_status"] == n_valid
    assert (row["limit_up_count"], row["limit_down_count"], row["strong_up_count"],
            row["strong_down_count"]) == (lu, ld, strong_up, strong_down)
    assert row["market_breadth_score"] == pytest.approx((lu + strong_up - ld - strong_down) / n_valid)


def test_outcome_join_and_separate_board_layer():
    features = read_parquet(PRIVATE / "entry_context_features_v1.parquet")
    events = read_parquet(ROOT / ".local_research_data/stage1_entry_event_study_v1/entry_event_outcome_v1.parquet")
    trades = read_parquet(ROOT / ".local_research_data/stage1_exit_and_pnl_v1/entry_trade_pnl_v1.parquet")
    joined = _join(features, events, trades)
    assert len(joined) == 505
    assert sum(r["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT" for r in joined) == 263
    assert sum(r["tp5_net"] is not None for r in joined if r["execution_intent"] == "NORMAL_CLOSE_INTENT") == 241
    assert all(r["feature"]["entry_date"] == r["entry_date"] for r in joined)


def test_public_summary_coverage_quality_and_hashes():
    manifest = json.loads((PUBLIC / "run_manifest.json").read_text(encoding="utf-8"))
    quality = json.loads((PUBLIC / "context_discovery_quality_receipt.json").read_text(encoding="utf-8"))
    assert quality["status"] == "PASS" and quality["entry_n"] == 505
    assert manifest["frozen_population"] == {"all": 505, "normal": 242, "board_conditional": 263}
    assert len(manifest["feature_list"]) == 8
    shortlist = json.loads((PUBLIC / "context_hypothesis_candidates.json").read_text(encoding="utf-8"))
    assert len(shortlist["candidates"]) <= 3 and shortlist["no_approved_filter"]
    for name, expected in manifest["public_artifacts"].items():
        assert sha256((PUBLIC / name).read_bytes()).hexdigest() == expected
    market = list(csv.DictReader((PUBLIC / "market_context_univariate_summary.csv").open(encoding="utf-8")))
    style = list(csv.DictReader((PUBLIC / "style_context_summary.csv").open(encoding="utf-8")))
    for feature in PRIMARY_FEATURES:
        rows = [r for r in market + style if r["feature"] == feature and r["population"] == "NORMAL_CLOSE_INTENT"
                and r["period"] == "ALL"]
        assert len(rows) == 4 and sum(int(r["n_events"]) for r in rows) == 242
    exits = list(csv.DictReader((PUBLIC / "context_all_exits_regime_summary.csv").open(encoding="utf-8")))
    assert len({r["exit_contract_id"] for r in exits}) == 8
    for intent, expected in (("NORMAL_CLOSE_INTENT", 242), ("LIMIT_UP_CLOSE_BOARD_INTENT", 263)):
        for exit_id in {r["exit_contract_id"] for r in exits}:
            partition = [r for r in exits if r["population"] == intent and r["period"] == "ALL"
                         and r["dimension"] == "MARKET_RISK_REGIME" and r["exit_contract_id"] == exit_id]
            assert sum(int(r["n_events"]) for r in partition) == expected
