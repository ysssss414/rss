from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import csv
import json
from pathlib import Path

import pytest

from research.edge_assessment_v1 import (bin_index, cluster_bootstrap,
                                         outcome_labels, target_first_net)
from research.pre_entry_features_v1 import FEATURES, _AsOfBars, build_features
from scripts.run_exit_robustness_v1 import _period


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "artifacts/stage1_exit_robustness_v1"


def _fixture():
    days = [f"2026-08-{d:02d}" for d in (24, 25, 26, 27, 28, 31)] + ["2026-09-01"]
    bars = {("TEST.SH", day): {"open": 10.0, "high": 10.2, "low": 9.8,
                                "close": 10.0, "amount": 100.0} for day in days}
    entry = {"security_id": "TEST.SH", "entry_date": "2026-08-31",
             "observation_instance_id": "obs", "entry_reasons": "ENTRY_A_PULLBACK_TO_MA5_V1",
             "execution_intent": "NORMAL_CLOSE_INTENT", "rsi14": "75.0", "ma5_raw": "9.80",
             "raw_low": "9.8", "raw_close": "10.0"}
    obs = {("TEST.SH", "obs"): {"trigger_date": "2026-08-27", "rsi14": "72.0",
                                 "v3_lookback_hits": "4", "trigger_regime_qualification_method":
                                 "QUALIFIED_BY_V3_PRICE_PATH", "rsi_replay_mode": "FULL_AVAILABLE_PREFIX"}}
    return [entry], obs, bars, days


def test_pre_entry_builder_ignores_future_mutation_and_has_no_outcome_fields():
    entries, obs, bars, days = _fixture()
    before = build_features(entries, obs, bars, days)
    future = deepcopy(bars)
    future[("TEST.SH", "2026-09-01")] = {"open": 1000.0, "high": 1000.0,
                                           "low": 1000.0, "close": 1000.0, "amount": 1e12}
    after = build_features(entries, obs, future, days)
    assert before == after
    assert before[0]["feature_source_max_date"] == entries[0]["entry_date"]
    assert all(name in before[0] for name in FEATURES)
    assert not any(token in key.lower() for key in before[0]
                   for token in ("outcome", "pnl", "mfe", "mae", "d8"))


def test_pre_entry_builder_rejects_future_trigger_and_d3_contamination():
    entries, obs, bars, days = _fixture()
    obs[("TEST.SH", "obs")]["trigger_date"] = "2026-09-01"
    with pytest.raises(ValueError, match="LOOKAHEAD"):
        build_features(entries, obs, bars, days)
    obs[("TEST.SH", "obs")]["trigger_date"] = "2026-08-27"
    bars[("TEST.SH", "2026-08-31")]["close"] = 10.1
    with pytest.raises(ValueError, match="POPULATION_DRIFT"):
        build_features(entries, obs, bars, days)


def test_asof_guard_rejects_a_future_bar_lookup():
    _, _, bars, _ = _fixture()
    with pytest.raises(ValueError, match="LOOKAHEAD"):
        _AsOfBars(bars, "2026-08-31").get(("TEST.SH", "2026-09-01"))


def test_chronological_split_and_bin_boundaries():
    assert _period("2025-12-31") == "DEVELOPMENT"
    assert _period("2026-01-01") == "TEMPORAL_VALIDATION"
    assert [bin_index(x, [0.0, .1, .2]) for x in (-.01, 0.0, .1, .2, None)] == [0, 1, 2, 3, None]


def test_outcome_labels_are_separate_and_censoring_not_imputed():
    event = {"h5_outcome_status": "AVAILABLE", "h5_signal_forward_close_return": -.21,
             "h5_signal_forward_mfe": .07}
    trade = {"outcome_status": "CLOSED", "exit_reason": "TIMEOUT_CLOSE"}
    labels = outcome_labels(event, trade)
    assert all(labels.values())
    event["h5_outcome_status"] = "RIGHT_CENSORED"
    trade["outcome_status"] = "OPEN_AT_SNAPSHOT_CUTOFF"
    assert all(x is None for x in outcome_labels(event, trade).values())


def test_target_first_is_optimistic_sensitivity_not_primary():
    costs = {"buy_commission_bps": "3.0", "buy_transfer_bps": "0.1",
             "sell_commission_bps": "3.0", "sell_transfer_bps": "0.1",
             "sell_stamp_duty_bps": "5.0"}
    row = {"same_bar_ambiguity": True, "exit_reason": "SAME_BAR_AMBIGUOUS_STOP_FIRST",
           "exit_price_raw": 9.5, "exit_price_comparable": 9.5,
           "fill_price": 10.0, "net_price_return": -.0510795}
    upper = target_first_net(row, tp=Decimal("0.05"), costs=costs)
    assert upper == pytest.approx(.05 - .00031 - 1.05 * .00081)
    assert upper > row["net_price_return"]
    assert row["exit_reason"] == "SAME_BAR_AMBIGUOUS_STOP_FIRST"
    adjusted = {**row, "exit_price_raw": 4.75, "exit_price_comparable": 9.5}
    assert target_first_net(adjusted, tp=Decimal("0.05"), costs=costs) == pytest.approx(upper)
    assert target_first_net({**row, "same_bar_ambiguity": False},
                            tp=Decimal("0.05"), costs=costs) == row["net_price_return"]


def test_security_cluster_bootstrap_keeps_repeated_events_together():
    rows = [{"security_id": "A", "net_price_return": x, "outcome_status": "CLOSED"}
            for x in (.1, -.2)] + [{"security_id": "B", "net_price_return": .05,
                                    "outcome_status": "CLOSED"}]
    a = cluster_bootstrap(rows, layer="NORMAL", exit_id="TIME_H1")
    b = cluster_bootstrap(rows, layer="NORMAL", exit_id="TIME_H1")
    assert a == b
    assert a["security_clusters"] == 2
    assert a["n_closed"] == 3


def test_public_artifacts_freeze_population_eight_exits_and_board_boundary():
    split = json.loads((PUBLIC / "edge_assessment_split_v1.json").read_text(encoding="utf-8"))
    assert split["development"]["entry_n"] == 389
    assert split["temporal_validation"]["entry_n"] == 116
    assert split["validation_grade"] == "TEMPORAL_VALIDATION_DESCRIPTIVE_ONLY"
    normal = list(csv.DictReader((PUBLIC / "normal_close_robustness_summary.csv").open(encoding="utf-8")))
    board = list(csv.DictReader((PUBLIC / "board_conditional_robustness_summary.csv").open(encoding="utf-8")))
    for rows, n in ((normal, 242), (board, 263)):
        overall = [r for r in rows if r["dimension"] == "ALL"]
        assert len(overall) == 8
        assert all(int(r["n_total"]) == n for r in overall)
    quality = json.loads((PUBLIC / "edge_assessment_quality_receipt.json").read_text(encoding="utf-8"))
    assert quality["status"] == "PASS"
    assert quality["feature_outcome_join_n"] == 505
    assert quality["board_unconditional_fill_n"] == 0
    audit = json.loads((PUBLIC / "board_fillability_audit.json").read_text(encoding="utf-8"))
    assert audit["status"] == "BOARD_FILLABILITY_DATA_INSUFFICIENT"
    assert audit["historical_fill_probability"] is None
    assert not audit["frozen_snapshot_intraday_orderbook_or_queue_datasets"]
    sensitivity = list(csv.DictReader((PUBLIC / "cost_sensitivity.csv").open(encoding="utf-8")))
    assert len(sensitivity) == 16 * 3
    for layer in {r["population_layer"] for r in sensitivity}:
        for exit_id in {r["exit_contract_id"] for r in sensitivity}:
            group = {r["scenario"]: float(r["mean"]) for r in sensitivity
                     if r["population_layer"] == layer and r["exit_contract_id"] == exit_id}
            assert group["ZERO_COST_DIAGNOSTIC_ONLY"] >= group["BASELINE_RESEARCH_COST"]
            assert group["BASELINE_RESEARCH_COST"] >= group["TWO_X_RESEARCH_COST_CONSERVATIVE"]


def test_feature_builder_source_is_independent_and_public_hashes_reconcile():
    source = (ROOT / "research/pre_entry_features_v1.py").read_text(encoding="utf-8")
    builder = (ROOT / "scripts/build_pre_entry_features_v1.py").read_text(encoding="utf-8")
    assert "from research.entry_event_outcome" not in source + builder
    assert "from research.exit_pnl" not in source + builder
    assert "load_d8(" not in source + builder
    receipt = json.loads((PUBLIC / "feature_builder_receipt.json").read_text(encoding="utf-8"))
    assert receipt["forbidden_feature_data_loaded"] is False
    manifest = json.loads((PUBLIC / "run_manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["public_artifacts"].items():
        assert sha256((PUBLIC / name).read_bytes()).hexdigest() == expected
