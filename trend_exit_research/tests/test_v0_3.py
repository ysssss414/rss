from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from research.data.amazingdata import checked_sdk_bars
from research.data.contracts import DataContractError

from trend_exit_research.src.cases_v0_3 import read_cases, audit_case
from trend_exit_research.src.holding_v0_3 import associate_holdings
from trend_exit_research.src.indicators import calculate, load_config, config_hash
from trend_exit_research.src.qualification_v0_3 import qualify
from trend_exit_research.src.risk import apply_risk, load_risk_config
from trend_exit_research.src.samples import read_samples
from trend_exit_research.src.synthetic import synthetic_frame
from trend_exit_research.src.v0_3 import load_research_config, paired_waiting_cost


MODULE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def inputs():
    frame = synthetic_frame()
    frame["is_st_sec"] = False
    for i in frame.index:
        previous = Decimal(str(frame.iloc[max(0, i-1)].raw_close))
        frame.loc[i, "preclose"] = float(previous)
        frame.loc[i, "low_limited"] = float((previous*Decimal("0.9")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        frame.loc[i, "high_limited"] = float((previous*Decimal("1.1")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    frame["price_high_lmt_rate"] = frame["price_low_lmt_rate"] = .1
    config = load_research_config(MODULE / "config/v0_3.json")
    daily, _ = calculate(frame, load_config(MODULE / "config/default.json"), actions=())
    return frame, daily, config, load_risk_config(MODULE / "config/risk_baseline.json")


def case(frame, case_id="TEST"):
    return dict(case_id=case_id, stock_name="合成样本", study_start=frame.trade_date.iloc[0],
                study_end=frame.trade_date.iloc[-1])


def manual_risk(inputs, closes, *, qualified=True):
    frame, original, config, baseline = inputs
    daily = original.iloc[60:60+len(closes)].copy().reset_index(drop=True)
    for key in ("raw_open", "raw_close", "indicator_close", "raw_high", "raw_low"):
        daily[key] = closes
    daily["ma28"], daily["ma55"] = 10., 8.
    daily["ma28_slope"] = daily["ma55_slope"] = .01
    risk, _ = apply_risk(daily, baseline)
    risk["trend_qualification"] = ["TREND_ELIGIBLE" if qualified and i == 0 else "TREND_NOT_QUALIFIED" for i in range(len(risk))]
    risk["trend_qualification_reason"] = "FIXTURE"
    risk["trend_qualification_version"] = config["qualification"]["version"]
    risk["trend_config_sha256"] = config_hash(config["qualification"])
    source = frame.iloc[60:60+len(closes)].copy().reset_index(drop=True)
    for key in ("raw_open", "raw_close", "raw_high", "raw_low"):
        source[key] = closes
    for i in source.index:
        price = Decimal(str(closes[max(0, i-1)]))
        source.loc[i, "preclose"] = float(price)
        source.loc[i, "low_limited"] = float((price*Decimal(".9")).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
        source.loc[i, "high_limited"] = float((price*Decimal("1.1")).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
        constraint = source.loc[i, "daily_constraint"].copy()
        constraint.update(limit_down_price=str(source.loc[i, "low_limited"]), limit_up_price=str(source.loc[i, "high_limited"]))
        source.at[i, "daily_constraint"] = constraint
    return source, risk, config


def test_registered_windows_match_target_stories_and_stock_set():
    master = pd.read_csv(MODULE / "inputs/manual_samples.csv").rename(columns={"stock_name":"security_name", "stock_code":"security_id"})
    samples = read_samples(MODULE / "inputs/manual_samples.csv", master)
    cases = read_cases(MODULE / "inputs/target_cases_v0_3.csv", samples)
    assert len(cases) == 17 and len({x["stock_name"] for x in cases}) == 16
    targets = {x["case_id"]: x for x in cases}
    assert targets["KUNLUN_2023_AI"]["study_start"] == "2023-01-01"
    assert targets["WULIANGYE_2020_2021"]["study_end"] == "2021-12-31"
    assert targets["FOXCONN_2025"]["study_end"] == "2025-12-31"
    assert targets["ZHAOYI_2026_ASOF_20260923"]["study_end"] == "2026-09-23"
    assert targets["ZHAOYI_2025_NOV_COUNTEREXAMPLE"]["study_start"] == "2025-11-01"


def test_uncovered_target_never_substitutes_later_price_window(inputs):
    frame = inputs[0].assign(has_bars=True)
    snapshot = SimpleNamespace(snapshot_id="TEST", validation=dict(manifest_sha256="a"*64),
        manifest=dict(calendar=dict(start="2024-01-02", end="2026-09-23")))
    target = dict(case_id="KUNLUN", resolution_status="RESOLVED", study_start="2023-01-01", study_end="2023-12-31",
        warmup_start="2022-07-01", warmup_end="2022-12-31", evaluation_start="2024-01-01", evaluation_end="2024-03-31")
    actual = audit_case(target, frame, snapshot)
    assert actual["target_status"] == "TARGET_CASE_WINDOW_UNCOVERED" and not actual["computed"]
    assert actual["study_start"] == "2023-01-01" and actual["study_known_calendar_sessions"] == 0
    assert actual["outside_calendar_denominator"] == "UNKNOWN"


@pytest.mark.parametrize("cut", [65, 85, 105])
def test_trend_qualification_is_prefix_invariant(inputs, cut):
    _, daily, config, _ = inputs
    original = qualify(daily, config["qualification"])
    changed = daily.copy()
    for key in ("indicator_close", "factor", "ma28", "ma55", "ma28_slope", "ma55_slope", "rolling_high"):
        changed.loc[cut:, key] *= 4
    actual = qualify(changed, config["qualification"])
    prefix = qualify(daily.iloc[:cut], config["qualification"])
    pd.testing.assert_frame_equal(original.iloc[:cut], actual.iloc[:cut])
    pd.testing.assert_frame_equal(original.iloc[:cut], prefix)


def test_default_trend_risk_state_is_not_main_uptrend_qualification(inputs):
    _, original, config, baseline = inputs
    flat = original.copy()
    flat[["ma28_slope", "ma55_slope"]] = 0.
    qualified = qualify(flat, config["qualification"])
    risk, _ = apply_risk(qualified, baseline)
    assert risk.loc[risk.POSITION_RISK_STATE.eq("TREND"), "trend_qualification"].ne("TREND_ELIGIBLE").all()
    assert qualified.loc[:58, "trend_qualification"].eq("TREND_DATA_UNQUALIFIED").all()


def test_no_holding_generates_only_diagnostic_events(inputs):
    frame, risk, config = manual_risk(inputs, [11., 9., 9., 9., 7., 7., 7., 8.], qualified=False)
    daily, links, actions, holdings = associate_holdings(risk, frame, case(frame), config, lane="QUALIFIED_ONLY")
    assert len(links) == 2 and links.reason.eq("NO_QUALIFIED_HOLDING").all()
    assert actions.empty and holdings.empty and daily.actual_sell_generated.eq(False).all()


def test_at_most_one_reduction_and_clear_no_repeat_after_exit(inputs):
    frame, risk, config = manual_risk(inputs, [11., 9., 9., 9., 11., 9., 9., 9., 7., 7., 7., 8., 11., 7., 7., 7., 8.])
    daily, links, actions, holdings = associate_holdings(risk, frame, case(frame), config, lane="QUALIFIED_ONLY")
    assert actions.action.tolist() == ["MA28_REDUCE_CAP_INTENT", "MA55_CLEAR_INTENT"]
    assert links.reason.eq("MA28_INTENT_ALREADY_ISSUED").any()
    assert links.reason.eq("AFTER_EXIT_INTENT_NO_REENTRY").any()
    assert daily.target_position.iloc[-1] == 0
    assert actions.ma28_position_cap.isna().all() and daily.actual_position_quantity.isna().all()
    assert actions.actual_realized_profit.isna().all() and holdings.actual_max_floating_profit.isna().all()


def test_execution_state_is_not_backdated_to_confirmation(inputs):
    frame, risk, config = manual_risk(inputs, [11., 7., 7., 7., 7., 7.])
    daily, links, actions, holdings = associate_holdings(risk, frame, case(frame), config, lane="QUALIFIED_ONLY")
    confirm = risk.loc[risk.clear_required, "trade_date"].iloc[0]
    assert daily.loc[daily.trade_date.eq(confirm), "holding_state"].iloc[0] == "EXIT_REQUIRED_INTENT_ONLY"
    assert holdings.observation_exit_date.iloc[0] > confirm
    assert links.loc[links.ma_period.eq(28), "reason"].iloc[0] == "MA55_OVERRIDES_MA28"
    assert actions.action.tolist() == ["MA55_CLEAR_INTENT"]
    assert not actions.modelled_position_trade.any()


def test_strict_no_qualification_never_creates_exit_or_profit(inputs):
    frame, risk, config = manual_risk(inputs, [11., 7., 7., 7., 7., 7.])
    frame = frame.drop(columns="daily_constraint")
    daily, _, actions, holdings = associate_holdings(risk, frame, case(frame), config, lane="QUALIFIED_ONLY")
    assert actions.execution_status.eq("EXECUTION_UNRESOLVED").all()
    assert actions.execution_price.isna().all() and actions.actual_realized_profit.isna().all()
    assert holdings.observation_exit_date.isna().all()
    assert daily.holding_state.iloc[-1] == "EXIT_REQUIRED_EXECUTION_UNRESOLVED"


def test_new_case_does_not_inherit_closed_holding(inputs):
    frame, risk, config = manual_risk(inputs, [11., 7., 7., 7., 7., 7.])
    first = associate_holdings(risk, frame, case(frame, "FIRST"), config, lane="QUALIFIED_ONLY")
    second = associate_holdings(risk, frame, case(frame, "SECOND"), config, lane="QUALIFIED_ONLY")
    assert len(first[2]) == len(second[2]) == 1
    assert first[3].holding_id.iloc[0] != second[3].holding_id.iloc[0]
    assert second[0].holding_state.iloc[0] == "QUALIFIED_OBSERVATION_NO_ENTRY_EVIDENCE"


def test_unknown_path_invalidates_holding_without_bridging(inputs):
    frame, risk, config = manual_risk(inputs, [11., 11., 9., 9., 9., 7., 7., 7.])
    risk.loc[1, "trend_qualification"] = "TREND_DATA_UNQUALIFIED"
    risk.loc[1, "indicator_status"] = "UNQUALIFIED_ACTION_RESET"
    actual = associate_holdings(risk, frame, case(frame), config, lane="QUALIFIED_ONLY")
    assert actual[2].empty and actual[1].reason.eq("HOLDING_DATA_UNQUALIFIED").all()
    assert actual[0].marked_max_price_gain.iloc[1:].isna().all()


@pytest.mark.parametrize("days", [1, 2, 3])
def test_raw_baseline_confirmations_unchanged_after_trend_fields(inputs, days):
    _, daily, config, baseline = inputs
    original, original_log = apply_risk(daily, baseline, confirmation_days=days)
    actual, actual_log = apply_risk(qualify(daily, config["qualification"]), baseline, confirmation_days=days)
    pd.testing.assert_frame_equal(original_log, actual_log)
    for key in ("ma28_break_count", "ma55_break_count", "reduce_required", "clear_required", "POSITION_RISK_STATE"):
        pd.testing.assert_series_equal(original[key], actual[key])
    assert config_hash(baseline) == "f1826b0b4df15afcc03108cc9f9bf91af48e21a5aa58f47752b1ed8af55263fa"


def test_reentry_is_candidate_only_and_no_perfect_buyback(inputs):
    frame, risk, config = manual_risk(inputs, [11., 7., 7., 7., 7., 11., 11.])
    risk.loc[5:, "trend_qualification"] = "TREND_ELIGIBLE"
    daily, _, actions, holdings = associate_holdings(risk, frame, case(frame), config, lane="QUALIFIED_ONLY")
    assert daily.reentry_candidate.iloc[5:].all() and len(holdings) == 1
    assert holdings.first_reentry_candidate.iloc[0] == risk.trade_date.iloc[5]
    assert holdings.reentry_delay_from_observation_exit.iloc[0] == 1
    assert len(actions) == 1 and daily.holding_state.iloc[-1] == "EXITED_OBSERVATION_MODEL_ONLY"


def test_actual_profit_and_unknown_cap_stay_null_even_with_price_models(inputs):
    frame, risk, config = manual_risk(inputs, [11., 9., 9., 9., 7., 7., 7., 7.])
    _, _, actions, holdings = associate_holdings(risk, frame, case(frame), config, lane="QUALIFIED_ONLY")
    assert actions.actual_realized_profit.isna().all() and actions.actual_sell_generated.eq(False).all()
    assert holdings.actual_entry_price.isna().all() and holdings.position_quantity.isna().all()


def test_same_inputs_have_identical_association_output_bytes(inputs):
    frame, risk, config = manual_risk(inputs, [11., 7., 7., 7., 7., 7.])
    first = associate_holdings(risk, frame, case(frame), config, lane="QUALIFIED_ONLY")
    second = associate_holdings(frame=frame.copy(), daily=risk.copy(), case=case(frame), config=deepcopy(config), lane="QUALIFIED_ONLY")
    assert all(a.to_csv(index=False) == b.to_csv(index=False) for a, b in zip(first, second))


def test_waiting_cost_preserves_unconfirmed_or_censored_episode():
    events = pd.DataFrame([dict(case_id="A", security_id="TEST", ma_period=28, first_break_date="2024-01-01",
        execution_lane="CONDITIONAL_CACHED_LIMITS", confirmation_days=1, confirmation_date="2024-01-01",
        execution_date="2024-01-02", execution_price=10.)])
    actual = paired_waiting_cost(events).iloc[0]
    assert actual.confirmation_3 == "NOT_CONFIRMED_OR_CENSORED"
    assert actual.execution_price_change_1_to_3 is None


def test_future_execution_or_qualification_cannot_change_current_holding_state(inputs):
    frame, risk, config = manual_risk(inputs, [11., 9., 9., 9., 7., 7., 7., 7.])
    original = associate_holdings(risk, frame, case(frame), config, lane="QUALIFIED_ONLY")[0]
    changed_frame, changed_risk = frame.copy(), risk.copy()
    changed_frame.loc[4:, ["raw_open", "raw_close", "raw_high", "raw_low"]] *= 4
    changed_frame.loc[4:, "issue"] = "MISSING_BAR"
    changed_risk.loc[4:, "trend_qualification"] = "TREND_ELIGIBLE"
    actual = associate_holdings(changed_risk, changed_frame, case(frame), config, lane="QUALIFIED_ONLY")[0]
    pd.testing.assert_frame_equal(original.iloc[:4], actual.iloc[:4])


def test_no_fill_reduction_is_not_retried_and_later_clear_stays_separate(inputs):
    frame, risk, config = manual_risk(inputs, [11., 9., 9., 9., 7., 7., 7., 7.])
    frame.loc[4, "raw_open"] = frame.loc[4, "low_limited"]
    frame.loc[4, "raw_high"] = 9.
    daily, _, actions, holdings = associate_holdings(risk, frame, case(frame), config, lane="CONDITIONAL_CACHED_LIMITS")
    assert actions.action.tolist() == ["MA28_REDUCE_CAP_INTENT", "MA55_CLEAR_INTENT"]
    assert actions.execution_status.iloc[0] == "NO_FILL"
    assert pd.isna(actions.execution_price.iloc[0])
    assert holdings.observation_exit_date.iloc[0] == frame.trade_date.iloc[7]
    assert daily.holding_state.iloc[-1] == "EXITED_OBSERVATION_MODEL_ONLY"


def test_analyst_peak_notes_never_enter_holding_signals(inputs):
    frame, risk, config = manual_risk(inputs, [11., 7., 7., 7., 7., 7.])
    a, b = case(frame), case(frame)
    a["notes"], b["notes"] = "人工高点332.23", "事后最低点与未来收益"
    first = associate_holdings(risk, frame, a, config, lane="QUALIFIED_ONLY")
    second = associate_holdings(risk, frame, b, config, lane="QUALIFIED_ONLY")
    assert all(x.to_csv(index=False) == y.to_csv(index=False) for x,y in zip(first,second))


def test_unknown_position_cap_is_not_replaced_by_full_sizing(inputs, tmp_path):
    import json
    config = deepcopy(inputs[2])
    config["lifecycle"]["ma28_position_cap"] = 1.
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="no authorized sizing"):
        load_research_config(path)


def test_overlapping_warmup_and_study_intervals_rejected(tmp_path):
    master = pd.read_csv(MODULE / "inputs/manual_samples.csv").rename(columns={"stock_name":"security_name", "stock_code":"security_id"})
    samples = read_samples(MODULE / "inputs/manual_samples.csv", master)
    content = (MODULE / "inputs/target_cases_v0_3.csv").read_text(encoding="utf-8")
    path = tmp_path / "cases.csv"
    path.write_text(content.replace("2022-07-01,2022-12-31", "2023-01-01,2023-01-02"), encoding="utf-8")
    with pytest.raises(ValueError, match="must not overlap"):
        read_cases(path, samples)


@pytest.mark.parametrize("code,start,end", [("300418.SZ",date(2022,7,1),date(2023,12,31)),
    ("000858.SZ",date(2019,7,1),date(2022,3,31)), ("603259.SH",date(2026,9,24),date(2026,10,13))])
def test_existing_bar_wrapper_has_single_code_and_bounded_date_request(code, start, end):
    class FakeProvider:
        ad = SimpleNamespace(constant=SimpleNamespace(Period=SimpleNamespace(day=SimpleNamespace(value="DAY"))))
        def _ensure_market(self):
            return self
        def query_kline(self, codes, **kwargs):
            self.calls = [(codes, kwargs)]
            return {code:[dict(code=code,date=start.isoformat(),open=10.,high=11.,low=9.,close=10.,volume=10.,amount=100.)]}
    provider = FakeProvider()
    result = checked_sdk_bars(provider, code, start, end)
    assert len(provider.calls) == 1
    assert provider.calls[0][0] == [code]
    assert provider.calls[0][1]["begin_date"] == int(start.strftime("%Y%m%d"))
    assert provider.calls[0][1]["end_date"] == int(end.strftime("%Y%m%d"))
    assert result.code.eq(code).all()


def test_existing_bar_wrapper_rejects_foreign_symbol_without_relabeling():
    provider = SimpleNamespace(ad=SimpleNamespace(constant=SimpleNamespace(Period=SimpleNamespace(day=SimpleNamespace(value="DAY")))))
    provider._ensure_market = lambda: provider
    provider.query_kline = lambda *args, **kwargs: {"999999.SZ":[]}
    with pytest.raises(DataContractError, match="SYMBOL_MISMATCH"):
        checked_sdk_bars(provider,"300418.SZ",date(2023,1,1),date(2023,12,31))
