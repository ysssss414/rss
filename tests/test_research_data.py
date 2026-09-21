from datetime import date

import pandas as pd
import pytest

from research.data.adjustment import adjusted_bars
from research.data.cache import Snapshot
from research.data.contracts import BAR_COLUMNS, SCHEMA_VERSION, AnalysisWindow, DataContractError, DataRequest
from research.data.provider import ResearchDataProvider, SnapshotProvider
from research.data.validation import validate_bars, validate_calendar
from research.runs.manifest import canonical_bytes, digest


CODE = "000001.SZ"
DAYS = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
REQUEST = DataRequest((CODE,), DAYS[0], DAYS[-1])


def bars():
    return pd.DataFrame([dict(ts_code=CODE, trade_date=str(day), raw_open=10.0,
        raw_high=12.0, raw_low=9.0, raw_close=11.0, volume=100.0, amount=1100.0,
        trading_status="TRADING", suspended=False, available_at=f"{day}T15:00:00+08:00",
        source="fixture", schema_version=SCHEMA_VERSION, snapshot_id=None,
        retrieved_at="2026-09-21T00:00:00Z", quality_status="PASS",
        volume_unit="share", amount_unit="CNY") for day in DAYS], columns=BAR_COLUMNS)


def metadata(**overrides):
    return {"provider_version": "fixture/1", "sdk_version": "not-used", "factor_schema": "none",
            "calendar_verified": True, "availability_verified": True,
            "source": "fixture", "retrieved_at": "2026-09-21T00:00:00Z", **overrides}


def snapshot(frame=None, factor=None, **meta):
    return Snapshot.create(request=REQUEST, bars=bars() if frame is None else frame, calendar=DAYS,
                           factors=pd.DataFrame() if factor is None else factor, metadata=metadata(**meta))


def test_schema_and_identity():
    assert len(validate_bars(bars(), REQUEST)) == 3
    with pytest.raises(DataContractError, match="SCHEMA_MISMATCH"):
        validate_bars(bars().drop(columns="volume"), REQUEST)
    with pytest.raises(DataContractError, match="SYMBOL_MISMATCH"):
        validate_bars(bars().assign(ts_code="000002.SZ"), REQUEST)


@pytest.mark.parametrize("field,value,code", [
    ("raw_close", None, "MISSING_VALUE"), ("amount", float("inf"), "INVALID_NUMBER"),
    ("raw_high", float("inf"), "INVALID_NUMBER"), ("raw_open", 0, "INVALID_OHLC"),
    ("volume", -1, "INVALID_OHLC"), ("amount", -1, "INVALID_OHLC"),
    ("raw_low", 13, "INVALID_OHLC"), ("trade_date", "bad", "INVALID_DATE"),
    ("trade_date", "2025-01-07", "AS_OF_VIOLATION"),
    ("available_at", "2025-01-07T15:00:00+08:00", "AS_OF_VIOLATION"),
    ("available_at", "2025-01-02T15:00:00", "INVALID_AVAILABILITY"),
    ("retrieved_at", "not-a-time", "INVALID_AVAILABILITY"),
    ("suspended", "false", "INVALID_STATUS"), ("volume_unit", "lot", "UNSUPPORTED_UNIT"),
])
def test_invalid_bars(field, value, code):
    frame = bars()
    frame[field] = frame[field].astype(object)
    frame.loc[0, field] = value
    with pytest.raises(DataContractError, match=code):
        validate_bars(frame, REQUEST)


def test_identical_duplicates_audited_and_conflicts_rejected():
    clean = validate_bars(pd.concat([bars(), bars().iloc[[0]]]), REQUEST)
    assert len(clean) == 3 and clean.attrs["identical_duplicates_removed"] == 1
    with pytest.raises(DataContractError, match="CONFLICTING_DUPLICATE"):
        validate_bars(pd.concat([bars(), bars().iloc[[0]].assign(amount=3)]), REQUEST)


def test_unknown_is_not_false_and_explicit_suspension_is_supported():
    frame = bars().astype({"suspended": object})
    frame.loc[0, ["trading_status", "suspended"]] = ["UNKNOWN", None]
    clean = validate_bars(frame, REQUEST)
    assert clean.iloc[0].suspended is None
    assert not snapshot(frame).coverage_complete
    frame.loc[0, ["trading_status", "suspended"]] = ["SUSPENDED", True]
    frame.loc[0, ["raw_open", "raw_high", "raw_low", "raw_close"]] = None
    assert snapshot(frame).coverage_complete


def test_calendar_and_unsupported_capabilities():
    assert validate_calendar(DAYS, DAYS[0], DAYS[-1]) == DAYS
    with pytest.raises(DataContractError, match="INVALID_CALENDAR"):
        validate_calendar(DAYS[::-1], DAYS[0], DAYS[-1])
    provider = ResearchDataProvider()
    assert not provider.capabilities()["security_status"]
    for operation in (lambda: provider.security_master(DAYS[0]), lambda: provider.security_status(REQUEST, DAYS[0])):
        with pytest.raises(DataContractError, match="UNSUPPORTED_CAPABILITY"):
            operation()


def factors():
    return pd.DataFrame({"ts_code": [CODE, CODE], "trade_date": ["2025-01-01", "2025-01-04"],
                         "factor": [1.0, 2.0], "available_at": ["2025-01-01T15:00:00+08:00", "2025-01-04T15:00:00+08:00"]})


def test_sparse_factor_effective_date_not_lost_and_raw_is_unchanged():
    raw = bars()
    before = raw.copy(deep=True)
    result = adjusted_bars(raw, factors(), mode="qfq", anchor=DAYS[-1], factor_schema="effective_events")
    assert result.close.tolist() == [5.5, 5.5, 11.0]
    pd.testing.assert_frame_equal(raw, before)
    assert result.raw_close.tolist() == [11.0] * 3
    with pytest.raises(DataContractError, match="INVALID_FACTOR"):
        adjusted_bars(raw, factors(), mode="qfq", anchor=DAYS[-1], factor_schema="daily")


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_factors(value):
    frame = factors()
    frame.loc[0, "factor"] = value
    with pytest.raises(DataContractError, match="INVALID_FACTOR"):
        adjusted_bars(bars(), frame, mode="qfq", anchor=DAYS[-1], factor_schema="effective_events")


def test_unknown_factor_schema_never_guessed():
    with pytest.raises(DataContractError, match="UNSUPPORTED_CAPABILITY"):
        adjusted_bars(bars(), factors(), mode="qfq", anchor=DAYS[-1], factor_schema="unknown")


def test_snapshot_identity_and_canonical_serialization():
    first = snapshot()
    assert first.snapshot_id == snapshot(bars().iloc[::-1]).snapshot_id
    changed = bars()
    changed.loc[0, "amount"] = 5
    assert first.snapshot_id != snapshot(changed).snapshot_id
    assert canonical_bytes({"b": 1, "a": None}) == canonical_bytes({"a": None, "b": 1})
    assert digest({"x": 1}) != digest({"x": 2})
    copied = first.bars()
    copied.loc[0, "raw_close"] = 999
    assert first.bars().iloc[0].raw_close == 11


def test_future_bar_factor_and_status_mutations_do_not_change_prefix():
    base = snapshot(factor=factors(), factor_schema="effective_events")
    changed_bars = bars().astype({"suspended": object})
    changed_bars.loc[2, ["trading_status", "suspended", "raw_high", "raw_close"]] = ["UNKNOWN", None, 999, 999]
    changed_factor = factors()
    changed_factor.loc[1, "factor"] = 50.0
    changed = snapshot(changed_bars, changed_factor, factor_schema="effective_events")
    request = DataRequest((CODE,), DAYS[0], DAYS[1])
    left = SnapshotProvider(base).daily_bars((CODE,), request.start, request.end, price_adjustment="qfq")
    right = SnapshotProvider(changed).daily_bars((CODE,), request.start, request.end, price_adjustment="qfq")
    pd.testing.assert_frame_equal(left, right)
    truncated = Snapshot.create(request=request, bars=bars().iloc[:2], calendar=DAYS[:2],
                               factors=factors().iloc[:1], metadata=metadata(factor_schema="effective_events"))
    pd.testing.assert_frame_equal(left, SnapshotProvider(truncated).daily_bars((CODE,), request.start, request.end, price_adjustment="qfq"))


def test_delayed_publication_cannot_enter_earlier_decision():
    frame = bars()
    frame.loc[1, "available_at"] = "2025-01-06T09:00:00+08:00"
    provider = SnapshotProvider(snapshot(frame))
    prefix = provider.daily_bars(DataRequest((CODE,), DAYS[0], DAYS[1]))
    assert prefix.trade_date.tolist() == [str(DAYS[0])]


def test_window_order_rejects_outcome_before_decision():
    with pytest.raises(DataContractError, match="INVALID_ARGUMENT"):
        AnalysisWindow(DAYS[0], DAYS[0], DAYS[1], DAYS[1], DAYS[0])


def test_snapshot_rejects_invalid_factor_before_any_cache_write():
    invalid = factors()
    invalid.loc[0, "factor"] = float("nan")
    with pytest.raises(DataContractError, match="INVALID_FACTOR"):
        snapshot(factor=invalid, factor_schema="effective_events")


def test_outcome_adjustment_uses_decision_anchor_across_corporate_action():
    # A two-for-one split halves raw price but doubles cumulative factor.
    raw = bars()
    raw.loc[2, ["raw_open", "raw_high", "raw_low", "raw_close"]] /= 2
    snap = snapshot(raw, factors(), factor_schema="effective_events")
    provider = SnapshotProvider(snap, price_anchor=DAYS[1])
    decision = provider.daily_bars((CODE,), DAYS[0], DAYS[1], price_adjustment="qfq")
    outcomes = provider.daily_bars((CODE,), DAYS[0], DAYS[-1], price_adjustment="qfq")
    assert decision.close.iloc[-1] == outcomes.close.iloc[-1] == 11
    assert snap.bars().raw_close.iloc[-1] == 5.5


@pytest.mark.parametrize("kind", ["duplicate", "volume_conflict", "missing_status", "nan", "infinite", "ohlc"])
def test_legacy_csv_and_formal_boundary_share_quality_policy(kind):
    from three_board_rsi_entry.market_data import normalize_bars
    raw = bars().rename(columns={f"raw_{p}": p for p in ("open", "high", "low", "close")})
    raw = raw[["ts_code", "trade_date", "open", "high", "low", "close", "amount", "volume", "suspended"]]
    if kind == "duplicate":
        assert len(normalize_bars(pd.concat([raw, raw.iloc[[0]]]))) == len(raw)
        return
    if kind == "volume_conflict":
        raw = pd.concat([raw, raw.iloc[[0]].assign(volume=999)])
        code = "CONFLICTING_DUPLICATE"
    elif kind == "missing_status":
        raw = raw.drop(columns="suspended")
        raw.loc[0, ["open", "high", "low", "close"]] = None
        code = "UNKNOWN_TRADING_STATUS"
    else:
        raw.loc[0, "high"] = {"nan": float("nan"), "infinite": float("inf"), "ohlc": 1.0}[kind]
        code = {"nan": "MISSING_VALUE", "infinite": "INVALID_NUMBER", "ohlc": "INVALID_OHLC"}[kind]
    with pytest.raises(DataContractError, match=code):
        normalize_bars(raw)
