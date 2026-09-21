import hashlib
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from research.data.cache import Snapshot, SnapshotStore
from research.data.contracts import BAR_COLUMNS, SCHEMA_VERSION, DataContractError, DataRequest
from research.runs.legacy import run_with_snapshot, write_research_outputs
from research.runs.manifest import canonical_bytes, digest
from research.runs.regression import compare_records
from three_board_rsi_entry.cli import main
from three_board_rsi_entry.config import StrategyConfig
from three_board_rsi_entry.input_excel import create_input_template
from tests.test_research_data import metadata
from tests.test_stage0_regression import records


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "baselines/stage0"
START = date(2025, 6, 30)
END = date(2025, 8, 15)
INPUT = ROOT / "examples/backtest_smoke_input.xlsx"


def fixture_data():
    # Synthetic prices only: declare a no-corporate-action world, factor=1.
    # These are not recovered historical raw bars or inferred live SDK units.
    legacy = pd.read_csv(ROOT / "examples/backtest_smoke_market_data.csv")
    frame = legacy.rename(columns={p: f"raw_{p}" for p in ("open", "high", "low", "close")})
    frame["volume"] = 1000.0
    frame["trading_status"] = frame.suspended.map({True: "SUSPENDED", False: "TRADING"})
    frame["available_at"] = frame.trade_date + "T15:00:00+08:00"
    frame["source"] = "synthetic-no-corporate-action"
    frame["schema_version"] = SCHEMA_VERSION
    frame["snapshot_id"] = None
    frame["retrieved_at"] = "2026-09-21T00:00:00Z"
    frame["quality_status"] = "PASS"
    frame["volume_unit"], frame["amount_unit"] = "share", "CNY"
    factors = frame[["ts_code", "trade_date", "available_at"]].assign(factor=1.0)
    return frame[BAR_COLUMNS], factors


def make_snapshot(frame=None, factors=None, end=END, **meta):
    default_bars, default_factors = fixture_data()
    frame = default_bars if frame is None else frame
    factors = default_factors if factors is None else factors
    days = sorted(pd.to_datetime(default_bars.trade_date).dt.date.unique())
    return Snapshot.create(request=DataRequest(tuple(sorted(frame.ts_code.unique())), days[0], end),
        bars=frame.loc[frame.trade_date.le(end.isoformat())], calendar=[d for d in days if d <= end],
        factors=factors.loc[factors.trade_date.le(end.isoformat())], metadata=metadata(factor_schema="daily", **meta))


def execute(snapshot=None, **kwargs):
    return run_with_snapshot(snapshot=make_snapshot() if snapshot is None else snapshot,
        input_path=INPUT, start_date=START, decision_as_of=kwargs.pop("decision_as_of", END),
        allow_incomplete=True, **kwargs)


def test_frozen_original_head_full_pipeline_through_snapshot():
    reference_path = BASE / "pipeline_reference.json"
    assert hashlib.sha256(reference_path.read_bytes()).hexdigest() == reference_path.with_suffix(".sha256").read_text().strip()
    reference = json.loads(reference_path.read_bytes())
    for name, expected in reference["input_hashes"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
    result = execute()
    actual = {"candidate_cycles": result.analysis.result.candidate_cycles, "signals": result.analysis.result.signals,
              **{k: getattr(result.backtest, k) for k in ("events", "first_signal_events", "signal_summary", "stratified_summary", "incomplete_samples")}}
    assert len(actual["signals"]) == len(actual["events"]) == 2
    for name, frame in actual.items():
        compare_records(reference["expected"][name], records(frame), name=name)


@pytest.mark.parametrize("mutation", ["bars", "factors", "status"])
def test_full_pipeline_prefix_and_future_mutation_invariance(mutation):
    cutoff = date(2025, 7, 7)
    original = execute(decision_as_of=cutoff, evaluate=False)
    truncated = execute(make_snapshot(end=cutoff), decision_as_of=cutoff, evaluate=False)
    bars, factors = fixture_data()
    future = bars.trade_date.gt(cutoff.isoformat())
    if mutation == "bars":
        bars.loc[future, ["raw_open", "raw_high", "raw_low", "raw_close"]] *= 3
    elif mutation == "factors":
        factors.loc[factors.trade_date.gt(cutoff.isoformat()), "factor"] = 3
    else:
        bars["suspended"] = bars.suspended.astype(object)
        bars.loc[future, ["trading_status", "suspended"]] = ["UNKNOWN", None]
    changed = execute(make_snapshot(bars, factors), decision_as_of=cutoff, evaluate=False)
    assert len(original.analysis.result.signals) == 2
    for other in (truncated, changed):
        pd.testing.assert_frame_equal(original.analysis.indicator_bars, other.analysis.indicator_bars)
        pd.testing.assert_frame_equal(original.analysis.result.candidate_cycles, other.analysis.result.candidate_cycles)
        pd.testing.assert_frame_equal(original.analysis.result.signals, other.analysis.result.signals)
        pd.testing.assert_frame_equal(original.eligibility, other.eligibility)


def test_outcomes_extend_without_changing_decisions_and_outputs_report_cutoffs(tmp_path):
    cutoff = date(2025, 7, 7)
    early = execute(decision_as_of=cutoff)
    late = execute(decision_as_of=cutoff, outcome_as_of=END)
    pd.testing.assert_frame_equal(early.analysis.result.signals, late.analysis.result.signals)
    pd.testing.assert_frame_equal(early.eligibility, late.eligibility)
    assert late.backtest.events.return_5d.notna().sum() > early.backtest.events.return_5d.notna().sum()
    write_research_outputs(late, tmp_path)
    assert json.loads((tmp_path / "backtest_run_summary.json").read_bytes())["run_as_of"] == str(END)
    manifest = json.loads((tmp_path / "research_manifest.json").read_bytes())
    assert manifest["decision_as_of"] == str(cutoff) and manifest["outcome_as_of"] == str(END)
    assert manifest["adjustment_anchor"] == manifest["outcome_adjustment_anchor"] == str(cutoff)
    with pytest.raises(DataContractError, match="OUTPUT_EXISTS"):
        write_research_outputs(late, tmp_path)


def test_manifest_spec_is_stable_and_references_inputs_without_secrets(monkeypatch):
    monkeypatch.setenv("AMAZINGDATA_PASSWORD", "stage0-secret-sentinel")
    one, two = execute(evaluate=False), execute(evaluate=False)
    assert one.manifest["spec_hash"] == two.manifest["spec_hash"]
    assert one.manifest["run_id"] != two.manifest["run_id"]
    spec = {k: v for k, v in one.manifest.items() if k not in {"run_id", "spec_hash", "status", "created_at", "research_eligibility"}}
    assert digest(spec) == one.manifest["spec_hash"]
    for field in ("input_file_hashes", "calendar_hash", "raw_bars_hash", "factor_hash", "effective_config_hash", "decision_as_of", "code_fingerprint"):
        assert digest({**spec, field: "changed"}) != one.manifest["spec_hash"]
    serialized = canonical_bytes(one.manifest).decode()
    assert "stage0-secret-sentinel" not in serialized and str(ROOT) not in serialized
    assert one.manifest["calculation_start"] == str(one.analysis.indicator_bars.trade_date.min())


def test_eligibility_is_explicit_for_missing_warmup_unknown_and_unverified_history():
    bars, factors = fixture_data()
    # Require a small, known prefix to obtain a qualified positive control.
    config = replace(StrategyConfig(), warmup_trading_days=20)
    clean = execute(config=config, decision_as_of=date(2025, 7, 7), evaluate=False)
    assert clean.eligibility.research_eligible.all()
    altered = bars.drop(bars[bars.trade_date.eq("2025-06-27")].index)
    bad = execute(make_snapshot(altered, factors, availability_verified=False), config=config,
                  decision_as_of=date(2025, 7, 7), evaluate=False)
    assert not bad.eligibility.research_eligible.any()
    reasons = "|".join(bad.eligibility.research_exclusion_reason)
    for reason in ("insufficient_warmup", "incomplete_snapshot", "unknown_trading_status", "unverified_historical_availability"):
        assert reason in reasons


def test_empty_manual_candidates_are_valid_and_cli_snapshot_run_is_real(tmp_path):
    empty_input = tmp_path / "empty.xlsx"
    create_input_template(empty_input)
    empty = run_with_snapshot(snapshot=make_snapshot(), input_path=empty_input, start_date=START,
                              decision_as_of=END, allow_incomplete=True)
    assert empty.analysis.result.signals.empty and empty.backtest.events.empty
    store = SnapshotStore(tmp_path / "cache")
    snap = make_snapshot()
    store.save(snap)
    destination = tmp_path / "run"
    assert main(["backtest", "--research-snapshot", snap.snapshot_id, "--research-cache", str(store.root),
                 "--input", str(INPUT), "--start-date", str(START), "--as-of", str(END),
                 "--allow-incomplete", "--output-dir", str(destination)]) == 0
    assert json.loads((destination / "research_manifest.json").read_bytes())["data_snapshot_id"] == snap.snapshot_id
    assert len(pd.read_csv(destination / "backtest_events.csv")) == 2
