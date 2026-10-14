from copy import deepcopy
from pathlib import Path
import json
import os
import subprocess
import sys

import pandas as pd
import pytest

from trend_exit_research.src.evaluation import evaluate, comparison_rows
from trend_exit_research.src.forward import _freeze
from trend_exit_research.src.indicators import calculate, load_config
from trend_exit_research.src.risk import apply_risk, combine_risk_caps, load_risk_config
from trend_exit_research.src.samples import read_samples
from trend_exit_research.src.synthetic import synthetic_frame


MODULE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def inputs():
    frame = synthetic_frame()
    daily, _ = calculate(frame, load_config(MODULE / "config/default.json"), actions=())
    return frame, daily, load_risk_config(MODULE / "config/risk_baseline.json")


def simple_daily(inputs, closes):
    _, original, _ = inputs
    result = original.iloc[60:60+len(closes)].copy().reset_index(drop=True)
    for key in ("indicator_close", "raw_open", "raw_high", "raw_low", "raw_close"):
        result[key] = closes
    result["ma28"] = result["ma55"] = 10.
    result["ma28_slope"] = result["ma55_slope"] = .01
    return result


def test_three_day_confirmation_is_not_backdated_and_ma55_has_priority(inputs):
    daily = simple_daily(inputs, [9., 9., 9., 8.])
    risk, events = apply_risk(daily, inputs[2])
    assert risk.ma28_break_count.tolist() == [1, 2, 3, 4]
    assert risk.ma55_break_state.tolist() == ["MA55_BREAK_PENDING", "MA55_BREAK_PENDING", "MA55_BREAK_CONFIRMED", "MA55_BREAK_CONFIRMED"]
    assert risk.clear_required.tolist() == [False, False, True, False]
    assert risk.preferred_action.iloc[2] == "CLEAR" and risk.position_cap.iloc[2] == 0
    assert risk.POSITION_RISK_STATE.tolist() == ["EXIT_PENDING", "EXIT_PENDING", "EXIT_REQUIRED", "EXIT_REQUIRED"]
    assert not risk.add_allowed.any()
    confirmed = events.loc[events.signal.str.endswith("CONFIRMED")]
    assert confirmed.trade_date.eq(daily.trade_date.iloc[2]).all()


def test_any_valid_recovery_including_equal_ma_resets_count(inputs):
    risk, _ = apply_risk(simple_daily(inputs, [9., 9., 10., 9., 9., 9.]), inputs[2])
    assert risk.ma28_break_count.tolist() == [1, 2, 0, 1, 2, 3]
    assert risk.clear_required.tolist() == [False, False, False, False, False, True]


@pytest.mark.parametrize("days", [1, 2, 3])
def test_explicit_comparison_scenarios(inputs, days):
    risk, _ = apply_risk(simple_daily(inputs, [9., 9., 9.]), inputs[2], confirmation_days=days)
    assert risk.clear_required.tolist() == [i == days-1 for i in range(3)]
    assert risk.risk_scenario.eq(f"TREND_EXIT_BASELINE_V0_1/CONFIRM_{days}").all()


def test_suspension_pauses_valid_observations_and_gap_resets(inputs):
    daily = simple_daily(inputs, [9.] * 5)
    daily.loc[1, "trading_status"] = "SUSPENDED"
    daily.loc[1, "indicator_status"] = "SUSPENDED"
    daily.loc[1, ["indicator_close", "ma28", "ma55"]] = float("nan")
    risk, _ = apply_risk(daily, inputs[2])
    assert risk.ma55_break_count.tolist() == [1, 1, 2, 3, 4]
    assert risk.clear_required.iloc[3] and not risk.clear_required.iloc[1]
    daily.loc[1, "indicator_status"] = "MISSING_BAR"
    risk, _ = apply_risk(daily, inputs[2])
    assert risk.ma55_break_count.tolist() == [1, 0, 1, 2, 3]


def test_ma28_defensive_repair_and_reentry_never_auto_buy(inputs):
    daily = simple_daily(inputs, [9., 9., 9., 11., 11., 11.])
    daily.ma55 = 8.
    risk, _ = apply_risk(daily, inputs[2])
    assert risk.POSITION_RISK_STATE.iloc[2] == "DEFENSIVE"
    assert risk.POSITION_RISK_STATE.iloc[-1] == "TREND"
    daily.ma55 = 10.
    risk, _ = apply_risk(daily, inputs[2])
    assert risk.POSITION_RISK_STATE.iloc[-1] == "REENTRY_CANDIDATE"
    assert risk.target_trend_position.iloc[-1] == 0 and not risk.add_allowed.iloc[-1]


def test_risk_states_are_prefix_invariant(inputs):
    _, daily, config = inputs
    complete, _ = apply_risk(daily, config)
    for cut in (60, 90, 100):
        prefix, _ = apply_risk(daily.iloc[:cut], config)
        pd.testing.assert_frame_equal(prefix, complete.iloc[:cut])


def test_future_outcomes_cannot_change_states(inputs):
    frame, daily, config = inputs
    risk, _ = apply_risk(daily, config)
    altered = frame.copy()
    altered.loc[100:, "raw_close"] *= 2
    before = evaluate(risk, frame)
    after = evaluate(risk, altered)
    assert not before.forward_return_20.equals(after.forward_return_20)
    repeated, _ = apply_risk(daily, config)
    pd.testing.assert_frame_equal(risk, repeated)
    assert not any(name.startswith("forward_") or name.startswith("execution_") for name in risk.columns)


def test_execution_model_fill_no_fill_and_unqualified_constraint(inputs):
    frame, daily, config = inputs
    risk, _ = apply_risk(daily, config)
    day = risk.loc[risk.clear_required, "trade_date"].iloc[0]
    position = frame.index[frame.trade_date.eq(day)][0] + 1
    outcomes = evaluate(risk, frame)
    assert outcomes.loc[outcomes.trade_date.eq(day), "execution_status"].iloc[0] == "MODELLED_FILL"
    changed = deepcopy(frame)
    changed.loc[position, "raw_open"] = float(changed.iloc[position].daily_constraint["limit_down_price"])
    outcomes = evaluate(risk, changed)
    assert outcomes.loc[outcomes.trade_date.eq(day), "execution_status"].iloc[0] == "NO_FILL"
    changed = frame.drop(columns="daily_constraint")
    outcomes = evaluate(risk, changed)
    row = outcomes.loc[outcomes.trade_date.eq(day)].iloc[0]
    assert row.execution_status == "EXECUTION_UNRESOLVED" and row.actual_execution_status == "NOT_OBSERVED_NO_ORDER_SENT"


def test_outcome_right_censor_and_action_path_are_explicit(inputs):
    frame, daily, config = inputs
    risk, _ = apply_risk(daily, config)
    outcomes = evaluate(risk, frame)
    assert outcomes.iloc[-1].forward_20_status == "RIGHT_CENSORED"
    changed = frame.copy()
    changed.loc[90, "is_xr_sec"] = True
    outcomes = evaluate(risk, changed)
    assert outcomes.iloc[85].forward_10_status == "UNQUALIFIED_ACTION_PATH"
    assert pd.isna(outcomes.iloc[85].forward_return_10)


@pytest.mark.parametrize("caps,expected,complete", [({}, None, False), ({"stock": .4}, .4, False),
    ({"stock": 0}, 0, True), ({"stock": .8, "market": .3, "sector": .5, "account": .2, "total_budget": .6}, .2, True)])
def test_budget_minimum_zero_and_missing_components(caps, expected, complete):
    value = combine_risk_caps(**caps)
    assert value["upper_bound"] == expected and value["complete"] == complete


@pytest.mark.parametrize("cap", [-.1, 1.1, float("nan")])
def test_invalid_budget_rejected(cap):
    with pytest.raises(ValueError):
        combine_risk_caps(stock=cap)


def test_forward_identity_and_immutable_freeze(tmp_path):
    master = pd.DataFrame({"security_name": ["药明康德"], "security_id": ["603259.SH"]})
    samples = read_samples(MODULE / "inputs/manual_samples.csv", master)
    forward = [x for x in samples if x["sample_type"] == "FORWARD_OBSERVATION"][0]
    assert forward["observation_start"] == "2026-10-13" and forward["observation_id"] == "WUXI_FORWARD_20261013_V0_1"
    root = tmp_path / "forward"
    root.mkdir()
    path = root / "initial.json"
    _freeze(path, b'{}', root)
    _freeze(path, b'{}', root)
    with pytest.raises(ValueError, match="Immutable"):
        _freeze(path, b'{"changed":1}', root)


def test_box_is_candidate_only_by_default_and_comparison_scope(inputs):
    frame, daily, config = inputs
    risk, _ = apply_risk(daily, config)
    assert not risk.POSITION_RISK_STATE.eq("BOX_LOW_EXPOSURE").any()
    table = comparison_rows(daily, frame, config, start=frame.iloc[80].trade_date, end=frame.iloc[110].trade_date)
    assert len(table) == 3 and [row["confirmation_days"] for row in table] == [1, 2, 3]
    assert all(row["interpretation"].startswith("DESCRIPTIVE") for row in table)


def test_execution_skips_known_suspension_without_skipping_unknown_gap(inputs):
    frame, daily, config = inputs
    risk, _ = apply_risk(daily, config)
    day = risk.loc[risk.clear_required, "trade_date"].iloc[0]
    position = frame.index[frame.trade_date.eq(day)][0] + 1
    changed = frame.copy()
    changed.loc[position, "trading_status"] = "SUSPENDED"
    changed.loc[position, "issue"] = "SUSPENDED"
    outcome = evaluate(risk, changed).loc[lambda table: table.trade_date.eq(day)].iloc[0]
    assert outcome.skipped_suspended_sessions == 1
    assert outcome.next_observed_trading_date == frame.iloc[position+1].trade_date
    changed.loc[position, "trading_status"] = "UNKNOWN"
    changed.loc[position, "issue"] = "MISSING_OR_UNKNOWN_STATUS"
    outcome = evaluate(risk, changed).loc[lambda table: table.trade_date.eq(day)].iloc[0]
    assert outcome.execution_status == "EXECUTION_UNRESOLVED" and outcome.skipped_suspended_sessions == 0


def test_sample_csv_column_order_survives_independent_python_hash_seeds():
    code = ("import pandas as pd; from pathlib import Path; from trend_exit_research.src.samples import read_samples; "
            + f"path=Path({str(MODULE / 'inputs/manual_samples.csv')!r}); "
            + "raw=pd.read_csv(path,keep_default_na=False); master=raw[['stock_name','stock_code']].rename(columns={'stock_name':'security_name','stock_code':'security_id'}); "
            + "print(pd.DataFrame(read_samples(path,master)).to_csv(index=False))")
    outputs = [subprocess.check_output([sys.executable, "-c", code], env=dict(os.environ, PYTHONHASHSEED=str(seed)),
                                      cwd=MODULE.parent) for seed in (11, 29)]
    assert outputs[0] == outputs[1]
