"""Synthetic/offline tests only. No vendor imports, login or real acquisition."""
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from research.data.amazingdata import checked_sdk_bars
from trend_exit_research.src.capture_v0_4 import (AUTHORIZED, OfflineCaptureProbe,
    bounded_request, freeze_capture, load_capture, raw_evidence, require_network_scope, validate_response)
from trend_exit_research.src.v0_4 import file_hash


def fixture_rows(code="603259.SH"):
    bars = pd.DataFrame({"security_id": [code, code], "trade_date": ["2026-09-24", "2026-09-25"],
        "raw_open": [10., 9.], "raw_high": [11., 10.], "raw_low": [9., 8.], "raw_close": [10., 9.],
        "volume": [1000., 1200.], "amount": [10000., 10800.]})
    status = bars[["security_id", "trade_date"]].assign(trading_status="TRADING")
    return bars, status


def metadata():
    return {"source": "SYNTHETIC_TEST_ONLY", "origin": "FAKE_SDK_TEST_ONLY", "api_version": "fake/1",
        "retrieved_at": "2026-10-14T08:00:00+00:00", "units_verified": True,
        "unit_evidence": "SYNTHETIC_FIXTURE_DECLARATION", "calendar_evidence": "SYNTHETIC_FIXTURE",
        "code_sha256": "a"*64, "config_sha256": "b"*64}


def capture(root, bars=None, status=None, code="603259.SH", meta=None):
    b, s = fixture_rows(code)
    return freeze_capture(root, code=code, start="2026-09-24", end="2026-10-13",
        bars=b if bars is None else bars, status=s if status is None else status,
        calendar=[date(2026, 9, 24), date(2026, 9, 25)], metadata=meta or metadata())


@pytest.mark.parametrize("code,start,end,dataset,stage", [
    ("600000.SH", "2026-09-24", "2026-10-13", "bars", "full"),
    ("603259.SH", "2026-09-23", "2026-10-13", "bars", "full"),
    ("603259.SH", "2026-09-24", "2026-10-14", "status", "full"),
    ("300418.SZ", "2022-07-01", "2024-01-01", "bars", "full"),
    ("000858.SZ", "2019-07-01", "2022-04-01", "bars", "full"),
    ("603259.SH", "2026-09-24", "2026-09-25", "factors", "full"),
    ("603259.SH", "2026-09-24", "2026-09-27", "bars", "smoke"),
    ("300418.SZ", "2022-07-01", "2022-07-02", "bars", "smoke"),
    ("603259.SH", "2026-09-24", "2026-09-25", "bars", "all_market"),
])
def test_request_rejects_expansion(code, start, end, dataset, stage):
    with pytest.raises(ValueError):
        bounded_request(code, start, end, dataset, stage)


@pytest.mark.parametrize("code", AUTHORIZED)
def test_exact_authorized_single_security(code):
    start, end = AUTHORIZED[code]
    request = bounded_request(code, start, end, "bars")
    assert request.codes == (code,) and request.start.isoformat() == start and request.end.isoformat() == end


@pytest.mark.parametrize("change", ["security", "date", "duplicate", "null_date"])
def test_response_rejects_foreign_or_out_of_range_rows(change):
    frame = fixture_rows()[0]
    if change == "duplicate":
        frame = pd.concat([frame, frame.iloc[[0]]])
    else:
        frame.loc[0, "security_id" if change == "security" else "trade_date"] = {
            "security": "600000.SH", "date": "2026-10-14", "null_date": None}[change]
    with pytest.raises(ValueError):
        validate_response(frame, bounded_request("603259.SH", "2026-09-24", "2026-10-13", "bars"))


def test_shared_bar_adapter_keeps_request_identity_and_bounds():
    calls = []
    def query(codes, **kwargs):
        calls.append((codes, kwargs))
        frame = fixture_rows()[0].rename(columns={"trade_date": "date", **{f"raw_{p}": p for p in ("open", "high", "low", "close")}})
        return {codes[0]: frame.drop(columns="security_id")}
    provider = SimpleNamespace(_ensure_market=lambda: SimpleNamespace(query_kline=query),
        ad=SimpleNamespace(constant=SimpleNamespace(Period=SimpleNamespace(day=SimpleNamespace(value=1)))))
    result = checked_sdk_bars(provider, "603259.SH", date(2026, 9, 24), date(2026, 9, 25))
    assert result.code.eq("603259.SH").all()
    assert calls == [(["603259.SH"], {"begin_date": 20260924, "end_date": 20260925, "period": 1})]


class FakeSDK:
    offline_probe_only = True
    def __init__(self, always_fail=False):
        self.calls, self.always_fail = [], always_fail

    def query(self, dataset, request):
        self.calls.append((dataset, request))
        same = sum(d == dataset and r == request for d, r in self.calls)
        if same == 1 or self.always_fail:
            raise ConnectionError("Synthetic transport failure")
        return pd.DataFrame({"security_id": [request.codes[0]], "trade_date": [request.start.isoformat()]})


def test_two_attempts_per_interface_and_sixteen_total():
    sdk = FakeSDK()
    probe = OfflineCaptureProbe(sdk)
    for dataset in ("bars", "status"):
        probe.call("smoke", "603259.SH", "2026-09-24", "2026-09-26", dataset)
    for code, (start, end) in AUTHORIZED.items():
        for dataset in ("bars", "status"):
            probe.call("full", code, start, end, dataset)
    assert len(sdk.calls) == len(probe.log) == 16
    assert all(len(r.codes) == 1 for _, r in sdk.calls)
    with pytest.raises(ValueError, match="ALREADY_FINISHED"):
        probe.call("full", "603259.SH", "2026-09-24", "2026-10-13", "bars")
    assert len(sdk.calls) == 16


def test_failed_smoke_stops_full_acquisition_without_retry_loop():
    sdk = FakeSDK(always_fail=True)
    probe = OfflineCaptureProbe(sdk)
    with pytest.raises(ConnectionError):
        probe.call("smoke", "603259.SH", "2026-09-24", "2026-09-26", "bars")
    with pytest.raises(ValueError, match="SMOKE_NOT_PASSED"):
        probe.call("full", "300418.SZ", "2022-07-01", "2023-12-31", "bars")
    assert len(sdk.calls) == 2


def test_real_backend_and_unproven_native_scope_cannot_be_enabled():
    with pytest.raises(ValueError, match="OFFLINE_FAKE_SDK_REQUIRED"):
        OfflineCaptureProbe(SimpleNamespace(query=lambda *_: pytest.fail("must not call")))
    with pytest.raises(ValueError, match="STOP_NETWORK_SCOPE_UNQUALIFIED"):
        require_network_scope({"python_request_bounds_verified": True})
    with pytest.raises(ValueError, match="NO_QUALIFIED_LIVE_BACKEND"):
        require_network_scope({"native_initialization_scope_verified": True, "native_query_scope_verified": True,
            "native_scope_evidence": "unreviewed external assertion", "native_request_upper_bound": 16})


def test_scope_failure_is_not_retried_as_a_transport_error():
    class ForeignSDK:
        offline_probe_only = True
        def query(self, *_):
            return fixture_rows("600000.SH")[0]
    probe = OfflineCaptureProbe(ForeignSDK())
    with pytest.raises(ValueError, match="OUT_OF_SCOPE"):
        probe.call("smoke", "603259.SH", "2026-09-24", "2026-09-26", "bars")
    assert len(probe.log) == 1 and not probe.smoke_pass


def test_known_suspension_and_missing_status_are_distinct():
    bars, status = fixture_rows()
    status.loc[1, "trading_status"] = "SUSPENDED"
    request = bounded_request("603259.SH", "2026-09-24", "2026-10-13", "bars")
    calendar = [date(2026, 9, 24), date(2026, 9, 25)]
    grid, grade = raw_evidence(bars.iloc[[0]], status, request, calendar, units_verified=True)
    assert grid.issue.tolist() == ["", "SUSPENDED"]
    assert grade["exploratory"] == "EXPLORATORY_DATA_QUALIFIED"
    grid, grade = raw_evidence(bars, status.iloc[[0]], request, calendar, units_verified=True)
    assert grid.issue.tolist() == ["", "MISSING_OR_UNKNOWN_STATUS"] and grade["exploratory"] == "UNQUALIFIED"


def test_missing_trading_bar_is_never_a_normal_close():
    bars, status = fixture_rows()
    grid, grade = raw_evidence(bars.iloc[[0]], status,
        bounded_request("603259.SH", "2026-09-24", "2026-10-13", "bars"),
        [date(2026, 9, 24), date(2026, 9, 25)], units_verified=True)
    assert grid.issue.iloc[1] == "MISSING_BAR" and pd.isna(grid.raw_close.iloc[1])
    assert grade["exploratory"] == "UNQUALIFIED"


@pytest.mark.parametrize("field,value", [("raw_high", 1.), ("raw_low", 20.), ("volume", -1.), ("amount", float("inf"))])
def test_invalid_raw_ohlcv_amount_rejected(tmp_path, field, value):
    bars, _ = fixture_rows()
    bars.loc[0, field] = value
    with pytest.raises(ValueError):
        capture(tmp_path, bars=bars)


def test_factor_evidence_insufficient_downgrades_strict_without_blocking_raw_exploration(tmp_path):
    result = capture(tmp_path)
    value = load_capture(result["path"], result["manifest_sha256"])
    assert value["qualification"]["exploratory"] == "EXPLORATORY_DATA_QUALIFIED"
    assert value["qualification"]["strict_pit_execution"] == "UNQUALIFIED"
    assert value["qualification"]["price_basis"] == "RAW_ONLY_NO_CONTINUOUS_ADJUSTED_PRICE"
    assert value["datasets"]["available_factor_event_evidence"] == []
    assert not any("adjusted" in key for row in value["datasets"]["bars"] for key in row)


def test_unknown_units_are_not_qualified_or_truthy_strings(tmp_path):
    meta = metadata()
    meta["units_verified"] = False
    assert capture(tmp_path, meta=meta)["qualification"]["exploratory"] == "UNQUALIFIED"
    meta["units_verified"] = "false"
    with pytest.raises(ValueError, match="BOOLEAN"):
        capture(tmp_path, meta=meta)


def test_unit_flag_without_traceable_evidence_is_rejected(tmp_path):
    meta = metadata()
    del meta["unit_evidence"]
    with pytest.raises(ValueError, match="MISSING_UNIT_EVIDENCE"):
        capture(tmp_path, meta=meta)


def test_snapshot_immutable_repeat_reload_and_corruption(tmp_path):
    first = capture(tmp_path)
    content = Path(first["path"]).read_bytes()
    assert capture(tmp_path) == first
    assert load_capture(first["path"], first["snapshot_id"])["dataset_rows"]["bars"] == 2
    changed, _ = fixture_rows()
    changed.loc[0, "raw_close"] = 10.5
    second = capture(tmp_path, bars=changed)
    assert second["snapshot_id"] != first["snapshot_id"] and Path(first["path"]).read_bytes() == content
    Path(first["path"]).write_bytes(content + b" ")
    with pytest.raises(ValueError, match="CORRUPT"):
        load_capture(first["path"], first["snapshot_id"])
    with pytest.raises(ValueError, match="CORRUPT"):
        capture(tmp_path)


def test_failed_security_does_not_modify_successful_independent_capture(tmp_path):
    success = capture(tmp_path)
    content = Path(success["path"]).read_bytes()
    bad, _ = fixture_rows("600000.SH")
    with pytest.raises(ValueError):
        capture(tmp_path, bars=bad)
    assert Path(success["path"]).read_bytes() == content
    load_capture(success["path"], success["snapshot_id"])


def test_late_backfill_never_claims_same_day_observation(tmp_path):
    original = Path(__file__).resolve().parents[1] / "reports/forward_initial_receipt.json"
    identity = file_hash(original)
    result = capture(tmp_path)
    value = load_capture(result["path"], result["snapshot_id"])
    assert value["observation_id"] == "WUXI_FORWARD_20261013_V0_1"
    assert value["capture_label"] == "LATE_CAPTURE_NOT_SAME_DAY_PROSPECTIVE"
    assert value["metadata"]["retrieved_at"] == "2026-10-14T08:00:00+00:00"
    assert not value["same_day_prospective"] and not value["post_inception_validation"]
    assert file_hash(original) == identity
