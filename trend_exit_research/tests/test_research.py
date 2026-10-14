from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pandas as pd
import pytest

from research.factor_pit import CorporateAction
from trend_exit_research.src.cli import main, write_outputs
from trend_exit_research.src.data import stock_frame, coverage_row
from trend_exit_research.src.indicators import calculate, load_config, METRICS
from trend_exit_research.src.samples import read_samples
from trend_exit_research.src.synthetic import synthetic_frame


MODULE = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    return load_config(MODULE / "config/default.json")


@pytest.fixture
def frame():
    return synthetic_frame()


def test_all_four_candidate_rules_and_reasons(frame, config):
    daily, log = calculate(frame, config, actions=())
    assert log.signal.tolist() == ["A", "D", "C", "B"]
    assert log.trade_date.tolist() == ["2024-05-02", "2024-05-03", "2024-05-07", "2024-05-15"]
    assert not daily.loc[:58, ["A", "B", "C", "D"]].any().any()
    d = daily.loc[daily.D].iloc[0]
    assert d.indicator_close >= d.ma55 and d.indicator_close < d.support_prior * .99
    assert all(json.loads(row.reason)["signal"] == row.signal for row in log.itertuples())
    assert log.latest_input_date.equals(log.trade_date)
    assert log.source_snapshot_id.notna().all() and log.config_sha256.str.len().eq(64).all()


@pytest.mark.parametrize("cut", [60, 85, 88, 95, 104])
def test_future_suffix_and_prefix_replay_invariance(frame, config, cut):
    baseline, baseline_log = calculate(frame, config, actions=())
    changed = frame.copy()
    for column in ("raw_open", "raw_high", "raw_low", "raw_close", "factor", "amount"):
        changed.loc[cut:, column] *= 3
    changed.loc[cut:, "is_xr_sec"] = True
    actual, log = calculate(changed, config, actions=())
    prefix, prefix_log = calculate(frame.iloc[:cut], config, actions=())
    pd.testing.assert_frame_equal(actual.iloc[:cut], baseline.iloc[:cut])
    pd.testing.assert_frame_equal(prefix, baseline.iloc[:cut])
    day = frame.iloc[cut - 1].trade_date
    pd.testing.assert_frame_equal(log.loc[log.trade_date.le(day)], baseline_log.loc[baseline_log.trade_date.le(day)])
    pd.testing.assert_frame_equal(prefix_log, baseline_log.loc[baseline_log.trade_date.le(day)])


def test_ex_rights_reanchor_and_slope_use_same_t_scale(frame, config):
    changed = frame.copy()
    cut = 75
    changed.loc[cut:, "factor"] = 2.
    for column in ("raw_open", "raw_high", "raw_low", "raw_close"):
        changed.loc[cut:, column] /= 2
    changed.loc[cut, "is_xr_sec"] = True
    event = CorporateAction("split", "TRANSFER", date.fromisoformat(frame.iloc[cut].trade_date),
                            date.fromisoformat(frame.iloc[cut-1].trade_date), Decimal("2"), "IMPLEMENTED", 1, True)
    original, _ = calculate(frame, config, actions=())
    daily, logs = calculate(changed, config, actions=(event,))
    assert daily.iloc[cut].ma55 == pytest.approx(original.iloc[cut].ma55 / 2)
    assert daily.iloc[cut].ma55_slope == pytest.approx(original.iloc[cut].ma55_slope)
    assert daily.iloc[cut].ma55 != pytest.approx(changed.raw_close.iloc[cut-54:cut+1].mean())
    for column in ["ma13_slope", "ma28_slope", "ma55_slope", "close_vs_ma28", "close_vs_ma55", "drawdown", "A", "B", "C", "D"]:
        pd.testing.assert_series_equal(daily[column], original[column])
    assert set(logs.factor_policy) == {"VERIFIED_EVENT_RATIOS"}


def test_unqualified_action_resets_and_no_signal_until_rewarmup(frame, config):
    frame.loc[75:, "factor"] = 2.
    frame.loc[75, "is_xr_sec"] = True
    daily, log = calculate(frame, config)
    assert daily.iloc[75].indicator_status == "UNQUALIFIED_ACTION_RESET"
    assert daily.iloc[75].ma55 is None or pd.isna(daily.iloc[75].ma55)
    assert not daily.iloc[75:].signal_eligible.any()
    assert log.empty


def test_non_common_factor_revision_rejected(frame, config):
    frame.loc[70:, "factor"] = 1.2
    daily, _ = calculate(frame, config, actions=())
    assert daily.iloc[70].indicator_status == "UNQUALIFIED_FACTOR_RATIO"
    assert not daily.iloc[70][["A", "B", "C", "D"]].any()


def test_common_factor_normalization_preserves_output(frame, config):
    daily, log = calculate(frame, config, actions=())
    frame.factor *= 5
    revised, revised_log = calculate(frame, config, actions=())
    pd.testing.assert_frame_equal(revised[METRICS + ["A", "B", "C", "D"]], daily[METRICS + ["A", "B", "C", "D"]])
    assert revised_log[["trade_date", "signal"]].equals(log[["trade_date", "signal"]])


@pytest.mark.parametrize("issue", ["MISSING_BAR", "MISSING_OR_UNKNOWN_STATUS", "MISSING_OR_INVALID_FACTOR"])
def test_missing_session_never_bridged(frame, config, issue):
    frame.loc[85, "issue"] = issue
    daily, _ = calculate(frame, config)
    assert daily.iloc[85].indicator_status == issue
    assert daily.iloc[86].valid_observations == 1
    assert not daily.iloc[85:].signal_eligible.any()


@pytest.mark.parametrize("field,value,reason", [("raw_available_at", "2024-04-30T15:31:00+08:00", "INPUT_UNAVAILABLE_AT_T_CLOSE"),
    ("raw_available_at", "2024-04-30T14:00:00+08:00", "INPUT_UNAVAILABLE_AT_T_CLOSE"),
    ("raw_high", 1., "INVALID_BAR"), ("factor", None, "MISSING_OR_INVALID_FACTOR")])
def test_invalid_or_late_input_suppresses_features(frame, config, field, value, reason):
    frame.loc[85, field] = value
    daily, _ = calculate(frame, config)
    assert daily.iloc[85].indicator_status == reason
    assert pd.isna(daily.iloc[85].ma28)


def test_suspension_does_not_fabricate_a_price_or_count_confirmations(frame, config):
    frame.loc[85, "issue"] = "SUSPENDED"
    frame.loc[85, "trading_status"] = "SUSPENDED"
    frame.loc[85, ["raw_open", "raw_high", "raw_low", "raw_close"]] = float("nan")
    daily, _ = calculate(frame, config)
    assert pd.isna(daily.iloc[85].indicator_close)
    assert not daily.iloc[85][["A", "B", "C", "D"]].any()
    closes = frame.raw_close.iloc[:87].dropna()
    assert daily.iloc[86].ma55 == pytest.approx(closes.iloc[-55:].mean())


def test_swing_extrema_only_appear_when_known(frame, config):
    daily, _ = calculate(frame, config, actions=())
    high_rows = daily.loc[daily.swing_high_known_at.notna()]
    assert (high_rows.swing_high_known_at <= high_rows.trade_date).all()
    first = high_rows.iloc[0]
    assert first.swing_high_date < first.swing_high_known_at
    assert daily.loc[daily.trade_date.eq(first.swing_high_date), "swing_high"].isna().all()
    pd.testing.assert_frame_equal(calculate(frame.iloc[:88], config, actions=())[0], daily.iloc[:88])


def write_samples(tmp_path, rows):
    path = tmp_path / "samples.csv"
    pd.DataFrame(rows, columns=["stock_name", "stock_code", "analysis_start", "analysis_end", "notes"]).to_csv(path, index=False)
    return path


def test_ambiguous_codes_and_explicit_disambiguation(tmp_path):
    master = pd.DataFrame({"security_name": ["同名", "同名", "唯一"], "security_id": ["000001.SZ", "600001.SH", "000002.SZ"]})
    rows = [["同名", "", "", "", ""], ["同名", "600001.SH", "", "", ""], ["未知", "", "", "", ""],
            ["唯一", "000001.SZ", "", "", ""]]
    samples = read_samples(write_samples(tmp_path, rows), master)
    assert [row["resolution_status"] for row in samples] == ["AMBIGUOUS", "RESOLVED", "NAME_NOT_FOUND", "CODE_NAME_MISMATCH"]
    assert samples[0]["resolved_code"] == ""


def test_append_and_multiple_intervals_preserve_existing_results(tmp_path, frame, config):
    master = pd.DataFrame({"security_name": ["甲", "乙"], "security_id": ["000001.SZ", "000002.SZ"]})
    rows = [["甲", "", "2024-04-01", "2024-05-31", "人工顶部仅备注"]]
    initial = read_samples(write_samples(tmp_path, rows), master)
    baseline = calculate(frame, config, actions=())[0]
    rows += [["甲", "", "2024-06-01", "2024-06-30", ""], ["乙", "", "", "", ""]]
    appended = read_samples(write_samples(tmp_path, rows), master)
    assert appended[0] == initial[0] and len(set(row["sample_id"] for row in appended)) == 3
    # Each sample owns a separate replay, independent of CSV order and other sample state.
    results = [calculate(frame.assign(security_id=row["resolved_code"]), config, actions=())[0] for row in appended]
    pd.testing.assert_frame_equal(results[0], baseline)


@pytest.mark.parametrize("rows", [[["甲", "000001", "", "", ""]], [["甲", "", "2024-06-01", "2024-05-01", ""]],
                                [["甲", "", "", "", ""], ["甲", "", "", "", ""]]])
def test_invalid_sample_registration_rejected(tmp_path, rows):
    master = pd.DataFrame({"security_name": ["甲"], "security_id": ["000001.SZ"]})
    with pytest.raises(ValueError):
        read_samples(write_samples(tmp_path, rows), master)


def test_byte_reproducibility(frame, config, tmp_path):
    daily, logs = calculate(frame, config, actions=())
    first = write_outputs(tmp_path / "first", coverage=[], daily=[daily], logs=[logs], config=config, provenance={"source": "test"})
    second_daily, second_log = calculate(frame, config, actions=())
    second = write_outputs(tmp_path / "second", coverage=[], daily=[second_daily], logs=[second_log], config=config, provenance={"source": "test"})
    assert first == second
    for path in (tmp_path / "first").iterdir():
        assert path.read_bytes() == (tmp_path / "second" / path.name).read_bytes()


def test_repair_cancels_failed_rebound_episode(frame, config):
    frame.loc[94, ["raw_open", "raw_close"]] = 80.
    frame.loc[94, "raw_high"] = 80.5
    frame.loc[94, "raw_low"] = 79.5
    daily, log = calculate(frame, config, actions=())
    assert daily.iloc[94].indicator_close >= max(daily.iloc[94].ma13, daily.iloc[94].ma28)
    assert not log.signal.eq("B").any()


def test_amount_and_prior_support_match_past_observations(frame, config):
    daily, _ = calculate(frame, config, actions=())
    row = daily.iloc[90]
    assert row.support_prior == pytest.approx(frame.raw_low.iloc[70:90].min())
    assert row.amount_sum10 == pytest.approx(frame.amount.iloc[81:91].sum())
    assert row.amount_change10 == pytest.approx(frame.amount.iloc[81:91].sum() / frame.amount.iloc[71:81].sum() - 1)


def test_zero_amount_windows_leave_relative_change_undefined(frame, config):
    frame.amount = 0.
    daily, _ = calculate(frame, config, actions=())
    assert daily.amount_change20.isna().all()
    assert daily.amount_ratio5_20.isna().all()


def test_config_rejects_missing_and_invalid_parameters(tmp_path):
    config = load_config(MODULE / "config/default.json")
    config["confirm_days"] = 0
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="Positive integer"):
        load_config(path)


class FakeSnapshot:
    snapshot_id = "fixture"
    validation = {"manifest_sha256": "a" * 64}
    manifest = {"calendar": {"start": "2024-01-02", "end": "2024-07-08"},
                "datasets": {"adjustment_factor": {"fingerprint": "b" * 64}}}

    def __init__(self, frame):
        self.frame = frame

    def load_calendar(self):
        return self.frame[["trade_date"]].assign(is_trading_day=True)

    def load_daily_bars(self, codes):
        return self.frame[["security_id", "trade_date", "raw_open", "raw_high", "raw_low", "raw_close", "volume", "amount"]].rename(
            columns={f"raw_{key}": key for key in ("open", "high", "low", "close")})

    def load_daily_status(self, codes):
        return self.frame[["security_id", "trade_date", "is_xr_sec", "is_wd_sec"]].assign(is_susp_sec=False)

    def load_adjustment_factor(self, codes):
        return self.frame[["security_id", "trade_date", "factor"]]


@pytest.mark.parametrize("dataset,reason", [("bars", "MISSING_BAR"), ("status", "MISSING_OR_UNKNOWN_STATUS"),
                                          ("factor", "MISSING_OR_INVALID_FACTOR")])
def test_adapter_missing_joins_preserve_calendar_and_report_dates(frame, dataset, reason):
    snapshot = FakeSnapshot(frame)
    method = getattr(snapshot, f"load_daily_{dataset}" if dataset != "factor" else "load_adjustment_factor")
    setattr(snapshot, f"load_daily_{dataset}" if dataset != "factor" else "load_adjustment_factor", lambda codes: method(codes).drop(index=85))
    master = pd.DataFrame({"security_id": ["000001.SZ"], "listing_date": ["2020-01-01"], "delisting_date": [None]})
    sample = {"resolved_code": "000001.SZ", "resolution_status": "RESOLVED", "analysis_start": "", "analysis_end": ""}
    result = stock_frame(snapshot, sample, master)
    assert len(result) == len(frame) and result.iloc[85].issue == reason
    coverage = coverage_row(sample, result, snapshot)
    assert coverage["data_coverage"] == pytest.approx(134 / 135)
    assert coverage["missing_dates"] == frame.iloc[85].trade_date


def test_adapter_duplicate_keys_rejected(frame):
    snapshot = FakeSnapshot(pd.concat([frame, frame.iloc[[85]]], ignore_index=True))
    master = pd.DataFrame({"security_id": ["000001.SZ"], "listing_date": ["2020-01-01"], "delisting_date": [None]})
    with pytest.raises(ValueError, match="Duplicate"):
        stock_frame(snapshot, {"resolved_code": "000001.SZ"}, master)


def test_cli_caps_real_scope_and_protects_output_paths(tmp_path):
    with pytest.raises(SystemExit):
        main(["run", "--max-real", "3"])
    with pytest.raises(SystemExit):
        main(["synthetic", "--out", str(tmp_path)])
