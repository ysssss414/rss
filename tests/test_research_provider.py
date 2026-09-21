from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from research.data.amazingdata import AmazingDataBridge, SessionInvalidated, checked_sdk_bars
from research.data.cache import SnapshotStore
from research.data.contracts import DataContractError
from three_board_rsi_entry.market_data import AmazingDataAdapter
from tests.test_research_data import CODE, DAYS, REQUEST


class FakeSdk:
    def __init__(self, response=None):
        self.response = response
        self.calls = 0
        self.ad = SimpleNamespace(constant=SimpleNamespace(Period=SimpleNamespace(day=SimpleNamespace(value="day"))))

    def _ensure_market(self):
        return self

    def query_kline(self, codes, **kwargs):
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def raw():
    return pd.DataFrame({"date": [str(d) for d in DAYS], "open": [10.] * 3,
                         "high": [12.] * 3, "low": [9.] * 3, "close": [11.] * 3,
                         "volume": [100.] * 3, "amount": [1100.] * 3})


def adapter(tmp_path, sdk):
    result = AmazingDataAdapter(legacy_provider_root=tmp_path, cache_dir=tmp_path,
                                retry_count=2, retry_delay_seconds=0)
    @contextmanager
    def provider():
        yield sdk
    result._provider = provider
    result.get_trade_calendar = lambda: [int(d.strftime("%Y%m%d")) for d in DAYS]
    return result


def test_wrong_key_and_embedded_wrong_symbol_hard_fail_without_retry(tmp_path):
    for response in ({"000002.SZ": raw()}, {CODE: raw().assign(code="000002.SZ")}):
        sdk = FakeSdk(response)
        bridge = AmazingDataBridge(adapter(tmp_path, sdk), SnapshotStore(tmp_path), units_verified=True)
        with pytest.raises(DataContractError, match="SYMBOL_MISMATCH"):
            bridge.capture(REQUEST)
        assert sdk.calls == 1
        assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("kind", ["valid", "empty", "partial", "suspended", "unknown"])
def test_fake_response_states_remain_distinct(tmp_path, kind):
    frame = raw()
    if kind == "empty":
        frame = frame.iloc[:0]
    if kind == "partial":
        frame = frame.iloc[:1]
    if kind in {"suspended", "unknown"}:
        frame["trading_status"] = "TRADING"
        frame.loc[0, "trading_status"] = "SUSPENDED" if kind == "suspended" else "UNKNOWN"
        frame.loc[0, ["open", "high", "low", "close"]] = None
        frame.loc[0, ["volume", "amount"]] = 0
    sdk = FakeSdk({CODE: frame})
    bridge = AmazingDataBridge(adapter(tmp_path, sdk), SnapshotStore(tmp_path), units_verified=True)
    result = bridge.capture(REQUEST)
    assert result.coverage_complete == (kind in {"valid", "suspended"})
    assert len(result.bars()) == len(frame)
    if kind == "unknown":
        assert result.bars().iloc[0].suspended is None
    if result.coverage_complete:
        assert bridge.capture(REQUEST).snapshot_id == result.snapshot_id
        assert sdk.calls == 1
        refreshed = bridge.capture(REQUEST, refresh=True)
        assert refreshed.snapshot_id != result.snapshot_id
        assert bridge.store.load(result.snapshot_id).content == result.content


def test_empty_valid_calendar_request_is_success(tmp_path):
    sdk = FakeSdk({})
    api = adapter(tmp_path, sdk)
    api.get_trade_calendar = lambda: []
    bridge = AmazingDataBridge(api, SnapshotStore(tmp_path), units_verified=True)
    assert bridge.capture(REQUEST).coverage_complete


def test_provider_failure_is_not_empty_and_redacts_exception(tmp_path, caplog):
    sdk = FakeSdk(RuntimeError("PASSWORD=secret credential path"))
    api = adapter(tmp_path, sdk)
    with pytest.raises(DataContractError, match="PROVIDER_FAILURE") as caught:
        api.get_daily_bars(CODE, DAYS[0], DAYS[-1])
    assert sdk.calls == 1
    assert "secret" not in str(caught.value) + caplog.text


def test_future_response_and_schema_errors_not_retried(tmp_path):
    for frame, error in ((raw().assign(date="2025-01-07"), "AS_OF_VIOLATION"),
                         (raw().drop(columns="volume"), "SCHEMA_MISMATCH")):
        sdk = FakeSdk({CODE: frame})
        bridge = AmazingDataBridge(adapter(tmp_path, sdk), SnapshotStore(tmp_path), units_verified=True)
        with pytest.raises(DataContractError, match=error):
            bridge.capture(REQUEST)
        assert sdk.calls == 1


def test_session_invalidated_reconnects_without_global_numba_patch(tmp_path):
    api = AmazingDataAdapter(legacy_provider_root=tmp_path, cache_dir=tmp_path,
                             retry_count=2, retry_delay_seconds=0)
    instances = []
    class Session:
        def __init__(self):
            instances.append(self)
        def login(self):
            pass
        def logout(self):
            pass
    api._load_legacy_module = lambda: SimpleNamespace(AmazingDataProvider=Session)
    def operation(provider):
        if len(instances) == 1:
            raise SessionInvalidated("expired")
        return "ok"
    assert api._call_with_retry(operation) == "ok"
    assert len(instances) == 2
    assert not api.use_numba_compat
    api.close()
    api.close()


def test_bridge_does_not_guess_units_or_factor_schema(tmp_path):
    bridge = AmazingDataBridge(adapter(tmp_path, FakeSdk({CODE: raw()})), SnapshotStore(tmp_path))
    with pytest.raises(DataContractError, match="UNSUPPORTED_CAPABILITY"):
        bridge.capture(REQUEST)
    bridge.units_verified = True
    with pytest.raises(DataContractError, match="UNSUPPORTED_CAPABILITY"):
        bridge.capture(REQUEST, include_factors=True)


def test_unknown_volume_is_not_tradable_in_legacy_adapter(tmp_path):
    sdk = FakeSdk({CODE: raw().assign(volume=0)})
    with pytest.raises(DataContractError, match="UNKNOWN_TRADING_STATUS"):
        adapter(tmp_path, sdk).get_daily_bars(CODE, DAYS[0], DAYS[-1])


def test_invalid_factor_does_not_retry_or_commit_snapshot(tmp_path):
    sdk = FakeSdk({CODE: raw()})
    api = adapter(tmp_path, sdk)
    api.get_backward_factor = lambda *args, **kwargs: pd.DataFrame({CODE: [1, -1, 1]}, index=pd.to_datetime(DAYS))
    bridge = AmazingDataBridge(api, SnapshotStore(tmp_path), units_verified=True, factor_schema="daily")
    with pytest.raises(DataContractError, match="INVALID_FACTOR"):
        bridge.capture(REQUEST, include_factors=True)
    assert sdk.calls == 1 and not list(tmp_path.rglob("*.json"))
