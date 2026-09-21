import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from research.runs.regression import ABS_TOLERANCE, REL_TOLERANCE, compare_records, quality_correction
from three_board_rsi_entry.backtest import run_event_backtest
from three_board_rsi_entry.config import StrategyConfig
from three_board_rsi_entry.market_data import AmazingDataMarketDataProvider
from three_board_rsi_entry.pipeline import run_analysis
from three_board_rsi_entry.replay import run_replay


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "baselines/stage0"
REFERENCE = json.loads((BASE / "reference.json").read_text(encoding="utf-8"))


def records(frame):
    return json.loads(frame.to_json(orient="table", index=False, date_format="iso", double_precision=15))["data"]


def compare_outputs(expected, result, bars, days, config):
    backtest = run_event_backtest(signals=result.signals, candidate_cycles=result.candidate_cycles,
        indicator_bars=bars, trading_days=days, as_of_date=days[-1], config=config)
    tables = {"candidate_cycles": result.candidate_cycles, "signals": result.signals,
              **{key: getattr(backtest, key) for key in ("events", "first_signal_events", "signal_summary", "stratified_summary", "incomplete_samples")}}
    for name, frame in tables.items():
        compare_records(expected[name], records(frame), name=name)


def test_frozen_reference_integrity_and_fixed_tolerance():
    hashes = json.loads((BASE / "checksums.json").read_text())
    for name, expected in hashes.items():
        assert hashlib.sha256((BASE / name).read_bytes()).hexdigest() == expected
    identity = REFERENCE["identity"]
    assert identity["atol"] == ABS_TOLERANCE and identity["rtol"] == REL_TOLERANCE
    assert identity["head_input_sha256"] != identity["current_user_input_sha256"]
    assert not identity["head_input_runnable"]


@pytest.mark.parametrize("case", REFERENCE["semantic_cases"], ids=lambda case: case["name"])
def test_frozen_semantic_candidate_signal_event_and_metrics(case):
    config = StrategyConfig(**REFERENCE["identity"]["config"])
    days = [date.fromisoformat(d) for d in case["days"]]
    bars = pd.DataFrame(case["bars"])
    result = run_replay(boards=pd.DataFrame(case["boards"]), indicator_bars=bars,
                       trading_days=days, start_date=days[0], as_of_date=days[-1], config=config)
    compare_outputs(case["expected"], result, bars, days, config)


def test_frozen_historical_legacy_results_and_new_quality_rejection():
    from research.data.amazingdata import canonical_legacy_bars
    from research.data.contracts import DataContractError
    historical = REFERENCE["historical"]
    raw = pd.read_csv(BASE / "historical_raw.csv")
    raw.trade_date = pd.to_datetime(raw.trade_date).dt.date
    factor = pd.read_csv(BASE / "historical_factors.csv", index_col=0)
    factor.index = pd.to_datetime(factor.index)
    days = [date.fromisoformat(d) for d in historical["calendar"]]
    adjusted = AmazingDataMarketDataProvider._apply_forward_adjustment(raw, factor, "000020.SZ")
    class FrozenProvider:
        def trading_days(self, first, last):
            return [d for d in days if first <= d <= last]
        def daily_bars(self, codes, first, last, **kwargs):
            return adjusted.loc[adjusted.ts_code.isin(codes) & adjusted.trade_date.between(first, last)].copy()
    config = StrategyConfig(**REFERENCE["identity"]["config"])
    run = run_analysis(input_path=BASE / "historical_input.xlsx", start_date=date.fromisoformat(historical["start"]),
                       as_of_date=date.fromisoformat(historical["end"]), provider=FrozenProvider(), config=config, allow_incomplete=True)
    compare_outputs(historical["expected"], run.result, run.indicator_bars, list(run.trading_days), config)
    with pytest.raises(DataContractError) as caught:
        canonical_legacy_bars(raw.rename(columns={"ts_code": "code", "trade_date": "date"}), "000020.SZ",
                              retrieved_at="2026-09-21T00:00:00Z", units_verified=False)
    assert quality_correction(caught.value) == {"before": "LEGACY_ACCEPTED", "after": "NEW_REJECTED", "reason": "SCHEMA_MISMATCH"}
