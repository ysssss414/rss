"""Focused BaoStock processing/transport guards. All test traffic is fake."""
from datetime import date
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from trend_exit_research.src.baostock_v0_5 import CaptureSession, FIELDS
from trend_exit_research.src.baostock_data_v0_5 import causal_grid, validate_bars
from trend_exit_research.src.capture_v0_4 import bounded_request, freeze_capture, load_capture


def fixture():
    return pd.DataFrame([
        ["2026-09-24", "sh.603259", "10", "11", "9", "10", "10", "1000", "10000", "3", "1", "1", "0", "0"],
        ["2026-09-25", "sh.603259", "10", "10", "10", "10", "10", "0", "0", "3", "", "0", "0", "0"],
        ["2026-09-28", "sh.603259", "9", "10", "8", "9", "9", "1000", "9000", "3", "1", "1", "0", "0"],
        ["2026-09-29", "sh.603259", "9", "10", "8", "9", "9", "1000", "9000", "3", "1", "1", "0", "0"],
    ], columns=FIELDS.split(","))


@pytest.mark.parametrize("change", ["foreign_code", "outside_date", "duplicate", "nan_price", "suspended_volume"])
def test_raw_response_quality_rejects_invalid_input(change):
    raw = fixture()
    if change == "foreign_code": raw.loc[0, "code"] = "sh.600000"
    elif change == "outside_date": raw.loc[0, "date"] = "2026-10-14"
    elif change == "duplicate": raw = pd.concat([raw, raw.iloc[[0]]])
    elif change == "nan_price": raw.loc[0, "close"] = ""
    else: raw.loc[1, "volume"] = "1000"
    with pytest.raises(ValueError):
        validate_bars(raw, "603259.SH", "2026-09-24", "2026-10-13")


def test_suspension_and_corporate_action_gap_are_causal_and_not_trade_observations():
    raw = fixture()
    data, grade = validate_bars(raw, "603259.SH", "2026-09-24", "2026-10-13")
    assert grade["valid_bars"] == 3 and grade["suspended"] == 1
    calendar = pd.DataFrame({"calendar_date": data.date, "is_trading_day": "1"})
    all_rows = causal_grid(data, calendar, "603259.SH")
    assert all_rows.issue.tolist() == ["INITIAL_PREDECESSOR_UNVERIFIED", "SUSPENDED", "CORPORATE_ACTION_EVIDENCE_REQUIRED", ""]
    # Future changes cannot alter any earlier quality/action annotation.
    prefix = causal_grid(data.iloc[:2], calendar.iloc[:2], "603259.SH")
    pd.testing.assert_frame_equal(all_rows.iloc[:2].reset_index(drop=True), prefix, check_dtype=False)
    assert raw.equals(fixture())


def test_vendor_fields_survive_v04_immutable_raw_contract_and_late_label(tmp_path):
    raw = fixture().iloc[[0]]
    bars = raw.assign(security_id="603259.SH", trade_date=raw.date,
        **{f"raw_{p}": raw[p] for p in ("open", "high", "low", "close")})
    status = bars[["security_id", "trade_date"]].assign(trading_status="TRADING")
    kwargs = dict(code="603259.SH", start="2026-09-24", end="2026-10-13", bars=bars, status=status,
        calendar=[date(2026, 9, 24)], metadata={"source": "SYNTHETIC_BAOSTOCK_TEST_NOT_LIVE", "origin": "SYNTHETIC_TEST",
        "api_version": "baostock/0.9.4", "retrieved_at": "2026-10-14T08:00:00+00:00", "units_verified": True,
        "unit_evidence": "SYNTHETIC_FIXTURE", "code_sha256": "a"*64, "config_sha256": "b"*64})
    first = freeze_capture(tmp_path, **kwargs)
    assert freeze_capture(tmp_path, **kwargs) == first
    value = load_capture(first["path"], first["manifest_sha256"])
    assert {k: value["datasets"]["bars"][0][k] for k in raw.columns} == raw.iloc[0].to_dict()
    assert value["capture_label"] == "LATE_CAPTURE_NOT_SAME_DAY_PROSPECTIVE" and not value["same_day_prospective"]
    assert value["qualification"]["strict_pit_execution"] == "UNQUALIFIED"


def fake_session(tmp_path, fail=True):
    # Unit exercise of the actual query loop with a fake, no-session data API.
    session = object.__new__(CaptureSession)
    result = SimpleNamespace(error_code="10002007" if fail else "0", error_msg="fixture", fields=FIELDS.split(","),
        next=lambda: False, get_row_data=lambda: [])
    session.bs = SimpleNamespace(query_history_k_data_plus=lambda *_, **kw: result)
    session.config = {"max_high_level_data_attempts": 16, "query_timeout_seconds": 1,
        "total_timeout_seconds": 900, "max_rows_per_query": 1200}
    session.output, session.calls, session.wire, session.finished = tmp_path, [], [], set()
    session.logged_in = True
    session.login = lambda: True
    session.began = time.monotonic()
    return session


def test_failed_query_retry_limit_and_request_cannot_be_reissued(tmp_path):
    session = fake_session(tmp_path)
    raw, result = session.query("fixture", "bars", "603259.SH", "2026-09-24", "2026-09-25")
    assert raw is None and result["status"] == "REQUEST_FAILED" and len(session.calls) == 2
    with pytest.raises(ValueError, match="ALREADY_USED"):
        session.query("different_label", "bars", "603259.SH", "2026-09-24", "2026-09-25")
    assert len(session.calls) == 2


def test_global_query_budget_and_forbidden_scope_are_checked_before_calls(tmp_path):
    session = fake_session(tmp_path)
    session.calls = [{}] * 16
    with pytest.raises(ValueError, match="BUDGET"):
        session.query("fixture", "bars", "603259.SH", "2026-09-24", "2026-09-25")
    for code, start, end in [("600000.SH", "2026-09-24", "2026-09-25"), ("300418.SZ", "2022-07-01", "2024-01-01")]:
        with pytest.raises(ValueError): bounded_request(code, start, end, "bars")
    assert len(session.calls) == 16


def test_completed_real_smoke_is_not_repeated(tmp_path):
    (tmp_path/"smoke_receipt.json").write_text("{}")
    with pytest.raises(ValueError, match="ALREADY_FINISHED"):
        CaptureSession({}, tmp_path)
