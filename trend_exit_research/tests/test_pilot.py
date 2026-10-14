from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from trend_exit_research.src.pilot import (
    FILLED, conditional_execution, execution, future_metrics, segment_counts,
    event_table, cohort_comparison, write_tables, audit_sample,
)
from trend_exit_research.src.risk import apply_risk, load_risk_config


MODULE = Path(__file__).resolve().parents[1]


def source_rows(closes):
    rows = []
    previous = closes[0]
    for day, close in zip(pd.date_range("2024-01-01", periods=len(closes)), closes):
        rows.append(dict(security_id="000858.SZ", trade_date=day.date().isoformat(), raw_open=float(previous),
            raw_close=float(close), raw_high=float(max(previous, close)), raw_low=float(min(previous, close)),
            factor=1., trading_status="TRADING", issue="", is_st_sec=False, is_xr_sec=False, is_wd_sec=False,
            preclose=float(previous), low_limited=round(previous*.9, 2), high_limited=round(previous*1.1, 2),
            price_high_lmt_rate=.1, price_low_lmt_rate=.1))
        previous = close
    return rows


def risk_rows(closes):
    rows = source_rows(closes)
    for row in rows:
        row.update(indicator_close=row["raw_close"], ma28=10., ma55=8., ma28_slope=.01, ma55_slope=.01,
            source_snapshot_id="TEST_SNAPSHOT", snapshot_manifest_sha256="a"*64, factor_source_hash="b"*64,
            decision_at=row["trade_date"]+"T15:30:00+08:00", indicator_status="READY", rolling_high=12.,
            drawdown=row["raw_close"]/12-1, stock_name="五粮液", sample_id="test", row_scope="STUDY")
    return pd.DataFrame(rows)


def test_qualified_lane_never_promotes_vendor_limits():
    source = source_rows([10, 9])
    signal = dict(source[0], reduce_required=True, clear_required=False, rolling_high=10.)
    strict = execution(source, 0, signal, "QUALIFIED_ONLY")
    assert strict["execution_status"] == "EXECUTION_UNRESOLVED"
    assert strict["execution_price"] is None
    assert strict["execution_reason"] == "MORNING_DAILY_CONSTRAINT_UNQUALIFIED"
    assert conditional_execution(source, 0)["execution_status"] == FILLED
    assert "daily_constraint" not in source[1]


def test_next_open_is_not_signal_close_or_execution_close():
    source = source_rows([10, 9, 11])
    source[1]["raw_open"] = 9.5
    result = conditional_execution(source, 0)
    assert result["execution_date"] == source[1]["trade_date"]
    assert result["execution_price"] == 9.5
    assert result["actual_execution_status"] == "NOT_OBSERVED_NO_ORDER_SENT"


def test_lower_limit_open_does_not_assume_fill_or_later_retry():
    source = source_rows([10, 9, 11])
    source[1]["raw_open"] = 9.
    result = conditional_execution(source, 0)
    assert result["execution_status"] == "NO_FILL"
    assert result["execution_date"] is None and result["execution_price"] is None
    assert result["order_model_at"].startswith(source[1]["trade_date"])


def test_known_suspension_waits_for_next_tradable_open():
    source = source_rows([10, 10, 9])
    source[1].update(trading_status="SUSPENDED", issue="SUSPENDED")
    result = conditional_execution(source, 0)
    assert result["execution_status"] == FILLED
    assert result["execution_date"] == source[2]["trade_date"]
    assert result["skipped_suspended_sessions"] == 1


@pytest.mark.parametrize("updates,reason", [
    ({"issue": "MISSING_BAR"}, "MISSING_OR_INVALID_NEXT_TRADING_DATA"),
    ({"trading_status": "UNKNOWN", "issue": "MISSING_OR_UNKNOWN_STATUS"}, "MISSING_OR_INVALID_NEXT_TRADING_DATA"),
    ({"factor": 2.}, "UNQUALIFIED_ACTION_BEFORE_EXECUTION"),
    ({"is_xr_sec": True}, "UNQUALIFIED_ACTION_BEFORE_EXECUTION"),
    ({"is_st_sec": True}, "ST_OR_UNKNOWN_STATUS_NOT_SUPPORTED"),
    ({"preclose": 9.99}, "REFERENCE_PRICE_DISAGREEMENT"),
    ({"low_limited": 8.99}, "CACHED_LIMIT_OR_TICK_DISAGREEMENT"),
    ({"raw_open": 10.001}, "CACHED_LIMIT_OR_TICK_DISAGREEMENT"),
    ({"price_low_lmt_rate": .05}, "UNSUPPORTED_CACHED_RATE"),
    ({"low_limited": None}, "MISSING_OR_INVALID_CACHED_LIMITS"),
])
def test_bad_data_is_unresolved_without_skipping_to_a_good_day(updates, reason):
    source = source_rows([10, 9, 11])
    source[1].update(updates)
    result = conditional_execution(source, 0)
    assert result["execution_status"] == "EXECUTION_UNRESOLVED"
    assert result["execution_reason"] == reason and result["execution_price"] is None


def test_end_of_snapshot_has_no_fabricated_execution():
    result = conditional_execution(source_rows([10]), 0)
    assert result["execution_reason"] == "NO_FOLLOWING_TRADING_SESSION"
    assert result["order_model_at"] is None


def test_ambiguous_sample_audit_never_reports_usable_data():
    snapshot = SimpleNamespace(snapshot_id="TEST", validation=dict(manifest_sha256="a"*64),
        manifest=dict(calendar=dict(start="2024-01-01", end="2024-12-31"),
            datasets={key:dict(fingerprint="b"*64) for key in ("adjustment_factor", "daily_bars", "daily_status")}))
    sample = dict(stock_name="同名股票", resolved_code="", analysis_start="", analysis_end="", resolution_status="AMBIGUOUS")
    audit = audit_sample(sample, pd.DataFrame(), snapshot)
    assert audit["adequacy"] == audit["missing_reasons"] == "AMBIGUOUS"
    assert audit["expected_sessions"] == audit["ma55_ready_sessions"] == 0
    assert not audit["three_day_input_possible"] and not audit["future40_input_possible"]


def test_warmup_cannot_supply_confirmation_days_before_study_start():
    frame = pd.DataFrame(source_rows([10.]*60))
    frame["has_bars"] = frame["has_status"] = frame["has_factor"] = True
    frame["turnover_rate"] = float("nan")
    snapshot = SimpleNamespace(snapshot_id="TEST", validation=dict(manifest_sha256="a"*64),
        manifest=dict(calendar=dict(start=frame.trade_date.iloc[0], end=frame.trade_date.iloc[-1]),
            datasets={key:dict(fingerprint="b"*64) for key in ("adjustment_factor", "daily_bars", "daily_status")}))
    sample = dict(stock_name="五粮液", resolved_code="000858.SZ", analysis_start=frame.trade_date.iloc[-1],
                  analysis_end=frame.trade_date.iloc[-1], resolution_status="RESOLVED")
    one_day = audit_sample(sample, frame, snapshot)
    assert one_day["ma55_ready_at_first_session"] and not one_day["three_day_input_possible"]
    sample["analysis_start"] = frame.trade_date.iloc[-3]
    assert audit_sample(sample, frame, snapshot)["three_day_input_possible"]


def test_future_metrics_are_execution_anchored_and_censored():
    source = source_rows([10, 9, 8, 12])
    source[2]["raw_low"] = 7.
    source[3]["raw_high"] = 13.
    metrics = future_metrics(source, 1, 9., [2, 4])
    assert metrics["post_2_return"] == pytest.approx(12/9-1)
    assert metrics["post_2_mae"] == pytest.approx(7/9-1)
    assert metrics["post_2_max_upside_proxy"] == pytest.approx(13/9-1)
    assert metrics["post_4_status"] == "RIGHT_CENSORED"
    assert metrics["post_4_return"] is None
    source[2]["factor"] = 2.
    assert future_metrics(source, 1, 9., [2])["post_2_status"] == "UNQUALIFIED_ACTION_PATH"


def test_warmup_audit_resets_on_actions_and_gaps_but_pauses_suspension():
    rows = source_rows([10]*7)
    rows[2].update(trading_status="SUSPENDED", issue="SUSPENDED")
    rows[3]["is_xr_sec"] = True
    rows[5]["issue"] = "MISSING_BAR"
    assert segment_counts(pd.DataFrame(rows)) == [1, 2, 2, 0, 1, 0, 1]


def test_study_start_resets_counts_and_future_suffix_cannot_change_confirmation():
    baseline = load_risk_config(MODULE / "config/risk_baseline.json")
    daily = risk_rows([9.]*8)
    study = daily.iloc[2:].copy().reset_index(drop=True)
    result, log = apply_risk(study, baseline)
    assert result.ma28_break_count.iloc[:3].tolist() == [1, 2, 3]
    assert log.loc[log.signal.eq("MA28_BREAK_CONFIRMED"), "trade_date"].tolist() == [daily.iloc[4].trade_date]
    changed = study.copy()
    changed.loc[4:, ["raw_close", "indicator_close", "ma28", "ma55"]] *= 8
    replay, _ = apply_risk(changed, baseline)
    pd.testing.assert_frame_equal(replay.iloc[:4], result.iloc[:4])


def test_paired_schemes_use_common_cohort_and_no_perfect_reentry():
    baseline = load_risk_config(MODULE / "config/risk_baseline.json")
    daily = risk_rows([11., 9., 7., 6., 12., 13.])
    risk, _ = apply_risk(daily, baseline, confirmation_days=1)
    frame = pd.DataFrame(source_rows([11., 9., 7., 6., 12., 13.]))
    config = dict(horizons=[2, 4], remaining_position_scenarios=[.25, .5, .75])
    events = event_table(risk, frame, dict(stock_name="五粮液", sample_id="test"), config)
    summary, path = cohort_comparison(risk, frame, events, config)
    selected = summary.loc[summary.cohort_date.eq("2024-01-02") & summary.horizon.eq(4)
                           & summary.execution_lane.eq("CONDITIONAL_CACHED_LIMITS")]
    assert len(selected) == 6
    assert selected.loc[selected.scheme.eq("D_HOLD"), "return_value"].iloc[0] == pytest.approx(13/9-1)
    assert selected.loc[selected.scheme.eq("A_MA28_FULL"), "return_value"].iloc[0] == 0
    assert selected.loc[selected.scheme.eq("C_MA55_FULL"), "return_value"].iloc[0] == pytest.approx(7/9-1)
    b = selected.loc[selected.scheme.eq("B_MA28_CAP_MA55_FULL")]
    assert b.return_value.tolist() == pytest.approx([.25*(7/9-1), .5*(7/9-1), .75*(7/9-1)])
    strict = summary.loc[summary.execution_lane.eq("QUALIFIED_ONLY") & summary.evaluation_status.eq("AVAILABLE")]
    assert strict.return_gap_vs_hold.eq(0).all()
    a = path.loc[path.cohort_date.eq("2024-01-02") & path.scheme.eq("A_MA28_FULL")
                 & path.execution_lane.eq("CONDITIONAL_CACHED_LIMITS")]
    assert a.remaining_shares.eq(0).all()  # It stays in cash even when price rebounds.


def test_no_fill_keeps_exposure_and_records_followup():
    baseline = load_risk_config(MODULE / "config/risk_baseline.json")
    daily = risk_rows([11., 9., 8., 7., 6., 5.])
    risk, _ = apply_risk(daily, baseline, confirmation_days=1)
    frame = pd.DataFrame(source_rows([11., 9., 8., 7., 6., 5.]))
    frame.loc[2, "raw_open"] = frame.loc[2, "low_limited"]
    config = dict(horizons=[2, 4], remaining_position_scenarios=[.5])
    events = event_table(risk, frame, dict(stock_name="五粮液", sample_id="test"), config)
    _, path = cohort_comparison(risk, frame, events, config)
    a = path.loc[path.cohort_date.eq("2024-01-02") & path.scheme.eq("A_MA28_FULL")
                 & path.execution_lane.eq("CONDITIONAL_CACHED_LIMITS")]
    assert a.remaining_shares.eq(1).all()
    assert a.followup_status.eq("EXPOSURE_RETAINED_REQUIRES_REVIEW").all()
    b = path.loc[path.cohort_date.eq("2024-01-02") & path.scheme.eq("B_MA28_CAP_MA55_FULL")
                 & path.execution_lane.eq("CONDITIONAL_CACHED_LIMITS")]
    exited = b.loc[b.remaining_shares.eq(0)]
    assert len(exited) > 0 and exited.followup_status.eq("MODELLED_EXIT_NO_REENTRY").all()


def test_simultaneous_ma55_confirmation_overrides_b_reduction():
    baseline = load_risk_config(MODULE / "config/risk_baseline.json")
    daily = risk_rows([11., 9., 9., 9., 8., 7., 6., 5.])
    daily["ma55"] = daily.ma28
    risk, _ = apply_risk(daily, baseline)
    frame = pd.DataFrame(source_rows([11., 9., 9., 9., 8., 7., 6., 5.]))
    config = dict(horizons=[2, 4], remaining_position_scenarios=[.5])
    events = event_table(risk, frame, dict(stock_name="五粮液", sample_id="test"), config)
    _, path = cohort_comparison(risk, frame, events, config)
    b = path.loc[path.scheme.eq("B_MA28_CAP_MA55_FULL") & path.execution_lane.eq("CONDITIONAL_CACHED_LIMITS")]
    assert b.modelled_sell_count.eq(1).all() and b.remaining_shares.eq(0).all()


def test_output_repeatability_and_sample_independence(tmp_path):
    table = pd.DataFrame(source_rows([10, 11]))
    provenance = dict(config_sha256="a"*64)
    first = write_tables(tmp_path / "a", {"daily.csv": table}, provenance)
    second = write_tables(tmp_path / "b", {"daily.csv": table}, deepcopy(provenance))
    assert first == second
    assert (tmp_path / "a/run_receipt.json").read_bytes() == (tmp_path / "b/run_receipt.json").read_bytes()
    # Other securities are independent function calls, never joined into price/state windows.
    unrelated = source_rows([30, 20])
    conditional_execution(unrelated, 0)
    assert conditional_execution(table.to_dict("records"), 0) == conditional_execution(source_rows([10, 11]), 0)


def test_generated_report_is_grounded_in_computed_counts_and_audit_dates():
    from trend_exit_research.src.pilot import summary_tables
    from trend_exit_research.src.pilot_report import render_report
    baseline = load_risk_config(MODULE / "config/risk_baseline.json")
    closes = [11., 9., 9., 9.] + [13.]*50
    risk, _ = apply_risk(risk_rows(closes), baseline)
    frame = pd.DataFrame(source_rows(closes))
    config = dict(horizons=[5, 10, 20, 40], remaining_position_scenarios=[.25, .5, .75])
    events = event_table(risk, frame, dict(stock_name="五粮液", sample_id="test"), config)
    cohorts, _ = cohort_comparison(risk, frame, events, config)
    confirmation, schemes = summary_tables(events, cohorts)
    audit = pd.DataFrame([dict(stock_name="药明康德", resolved_code="603259.SH", first_available="2024-01-01",
        last_available="2024-02-23", usable_trading_bars=0, expected_sessions=0, resolution_status="RESOLVED",
        missing_reasons="NO_SESSIONS_IN_SNAPSHOT_INTERVAL", pre_start_event_free_observations=0,
        ma55_ready_at_first_session=False, ma55_ready_sessions=0, three_day_input_possible=False,
        future40_input_possible=False, factor_source_hash="b"*64, observation_id="TEST_FORWARD", observation_start="2026-10-13")])
    tables = {"coverage_audit.csv": audit, "execution_events.csv": events, "confirmation_summary.csv": confirmation,
              "cohort_comparison.csv": cohorts, "scheme_summary.csv": schemes, "daily_signals.csv": risk}
    receipt = dict(source_snapshot_id="TEST", snapshot_validation=dict(manifest_sha256="a"*64),
        risk_config_sha256="b"*64, pilot_config_sha256="c"*64, v01_release_commit="release", git_commit="anchor")
    text = render_report(tables, receipt)
    assert "1个确认事件" in text and "2024-02-23" in text and "TEST_FORWARD" in text
    assert "STRATEGY_EFFECTIVENESS_NOT_YET_ESTABLISHED" in text
