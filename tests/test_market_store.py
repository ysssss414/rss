from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import socket
import sys

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

from research.market_store import LocalMarketStore, _copy_atomic, _deduplicate_clean, _quality, materialize
from research.market_update import (common_factor_scale, missing_trading_dates,
                                    update_market_store, _upsert)
from scripts.sync_market_store import call_with_retry, record_ok, validate_cache


def _make_store(root: Path) -> None:
    master = pd.DataFrame({"security_id": ["000001.SZ", "600001.SH"],
        "exchange": ["SZ", "SH"], "board": ["SZSE Main", "SSE Main"],
        "listing_date": pd.to_datetime(["2000-01-01", "2000-01-01"]),
        "delisting_date": pd.to_datetime([None, None]), "security_name": ["A", "B"]})
    calendar = pd.DataFrame({"trade_date": pd.to_datetime(["2026-09-21", "2026-09-22"]),
                             "is_trading_day": [True, True]})
    bars = pd.DataFrame({"security_id": ["000001.SZ", "600001.SH"] * 2,
        "trade_date": pd.to_datetime(["2026-09-21"] * 2 + ["2026-09-22"] * 2),
        "open": [10.0] * 4, "high": [11.0] * 4, "low": [9.0] * 4,
        "close": [10.0] * 4, "volume": [100] * 4, "amount": [1000.0] * 4})
    status = pd.DataFrame({"security_id": bars.security_id, "trade_date": bars.trade_date,
        "preclose": [10.0] * 4, "high_limited": [11.0] * 4, "low_limited": [9.0] * 4,
        "price_high_lmt_rate": [.1] * 4, "price_low_lmt_rate": [.1] * 4,
        "is_st_sec": [False] * 4, "is_susp_sec": [False] * 4,
        "is_wd_sec": [False] * 4, "is_xr_sec": [False] * 4})
    factor = pd.DataFrame({"security_id": bars.security_id, "trade_date": bars.trade_date,
                            "factor": [1.0] * 4})
    for name, frame in (("security_master", master), ("trading_calendar", calendar)):
        path = root / name / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
    for name, frame in (("daily_bars", bars), ("daily_status", status), ("adjustment_factor", factor)):
        path = root / name / "year=2026" / "month=09" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
    metadata = root / "metadata" / "manifest.json"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(json.dumps({"store_schema_version": 1, "source": "AmazingData",
        "dataset_row_counts": {"security_master": 2, "trading_calendar": 2,
        "daily_bars": 4, "daily_status": 4, "adjustment_factor": 4}}), encoding="utf-8")


class FakeFetcher:
    def __init__(self):
        self.remote_calls = 0

    def calendar(self, end):
        self.remote_calls += 1
        return ["2026-09-21", "2026-09-22", "2026-09-23"]

    def security_master(self, end):
        self.remote_calls += 1
        return pd.DataFrame({"security_id": ["000001.SZ", "600001.SH"],
            "exchange": ["SZ", "SH"], "board": ["SZSE Main", "SSE Main"],
            "listing_date": pd.to_datetime(["2000-01-01"] * 2),
            "delisting_date": pd.to_datetime([None, None]), "security_name": ["A", "B"]})

    def bars(self, code, start, end):
        self.remote_calls += 1
        return pd.DataFrame({"code": [code], "date": ["2026-09-23"],
            "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.0],
            "volume": [100], "amount": [1000.0]})

    def status(self, code, start, end):
        self.remote_calls += 1
        return pd.DataFrame({"MARKET_CODE": [code], "TRADE_DATE": [20260923],
            "PRECLOSE": [10.0], "HIGH_LIMITED": [11.0], "LOW_LIMITED": [9.0],
            "PRICE_HIGH_LMT_RATE": [.1], "PRICE_LOW_LMT_RATE": [.1],
            "IS_ST_SEC": [0], "IS_SUSP_SEC": [0], "IS_WD_SEC": [0], "IS_XR_SEC": [0]})

    def factors(self, codes):
        self.remote_calls += 1
        return pd.DataFrame({code: [1.0, 1.0, 1.0] for code in codes},
            index=pd.to_datetime(["2026-09-21", "2026-09-22", "2026-09-23"]))


def test_partition_write_read_and_offline_loader(tmp_path, monkeypatch):
    _make_store(tmp_path)
    monkeypatch.setitem(sys.modules, "AmazingData", None)
    def no_network(*args, **kwargs):
        raise AssertionError("Offline loader attempted network access")
    monkeypatch.setattr(socket, "create_connection", no_network)
    store = LocalMarketStore(tmp_path)
    assert len(store.load_security_master()) == 2
    assert len(store.load_trading_calendar("2026-09-22", "2026-09-22")) == 1
    assert len(store.load_daily_bars(["000001.SZ"])) == 2
    assert len(store.load_daily_status()) == 4
    assert len(store.load_adjustment_factor()) == 4
    store.close()


def test_atomic_partition_replacement_preserves_old_on_failure(tmp_path, monkeypatch):
    _make_store(tmp_path)
    path = tmp_path / "daily_bars" / "year=2026" / "month=09" / "part.parquet"
    before = sha256(path.read_bytes()).hexdigest()
    def fail(*args, **kwargs):
        raise OSError("injected disk failure")
    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail)
    with pytest.raises(OSError, match="injected"):
        _upsert(tmp_path, "daily_bars", pd.DataFrame({"security_id": ["000001.SZ"],
            "trade_date": ["2026-09-23"], "open": [10], "high": [11], "low": [9],
            "close": [10], "volume": [100], "amount": [1000]}))
    assert sha256(path.read_bytes()).hexdigest() == before


def test_duckdb_atomic_copy_and_canonical_key(tmp_path):
    con = duckdb.connect()
    path = tmp_path / "year=2026" / "month=09" / "part.parquet"
    _copy_atomic(con, "SELECT '000001.SZ' security_id, DATE '2026-09-23' trade_date, 1.0 factor", path)
    assert con.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()[0] == 1
    _copy_atomic(con, "SELECT '000001.SZ' security_id, DATE '2026-09-23' trade_date, 2.0 factor", path)
    assert con.execute("SELECT factor FROM read_parquet(?)", [str(path)]).fetchone()[0] == 2.0


def test_canonical_security_date_duplicate_rejected():
    con = duckdb.connect()
    con.execute("""CREATE TEMP TABLE clean AS SELECT '000001.SZ' security_id,
        DATE '2026-09-23' trade_date, 1.0 factor UNION ALL
        SELECT '000001.SZ', DATE '2026-09-23', 1.0""")
    with pytest.raises(ValueError, match="duplicate"):
        _quality(con, "adjustment_factor")
    issues = _deduplicate_clean(con)
    assert issues["exact_duplicate_rows"] == 1
    assert issues["conflicting_duplicate_key_rows"] == 0
    assert con.execute("SELECT count(*) FROM clean").fetchone()[0] == 1


def test_gap_plan_update_and_identical_rerun(tmp_path):
    _make_store(tmp_path)
    assert missing_trading_dates(["2026-09-21", "2026-09-22", "2026-09-23"],
                                 ["2026-09-21", "2026-09-23"], "2026-09-23") == ["2026-09-22"]
    fetcher = FakeFetcher()
    first = update_market_store(tmp_path, "2026-09-23", fetcher)
    assert first["missing_dates"] == {"daily_bars": ["2026-09-23"],
                                      "daily_status": ["2026-09-23"]}
    assert first["remote_calls"] == 6
    assert first["new_rows"]["daily_bars"] == first["new_rows"]["daily_status"] == 2
    store = LocalMarketStore(tmp_path)
    assert len(store.load_daily_bars()) == 6
    assert not store.load_daily_bars().duplicated(["security_id", "trade_date"]).any()
    assert store.manifest["dataset_row_counts"]["daily_bars"] == 6
    store.close()
    second = update_market_store(tmp_path, "2026-09-23", fetcher)
    assert second["status"] == "NO_OP" and second["remote_calls"] == 0
    assert sum(second["new_rows"].values()) == 0 and fetcher.remote_calls == 6


def test_factor_common_normalization_vs_ratio_change():
    index = pd.to_datetime(["2026-09-21", "2026-09-22"])
    old = pd.Series([1.0, 2.0], index=index)
    assert common_factor_scale(old, pd.Series([2.0, 4.0], index=index)) == .5
    assert common_factor_scale(old, pd.Series([2.0, 5.0], index=index)) is None


def test_bounded_factor_refresh_adds_latest_and_preserves_history(tmp_path):
    _make_store(tmp_path)
    fetcher = FakeFetcher()
    update_market_store(tmp_path, "2026-09-23", fetcher)
    before = LocalMarketStore(tmp_path)
    historical = before.load_adjustment_factor(end="2026-09-22")
    before.close()
    receipt = update_market_store(tmp_path, "2026-09-23", fetcher,
                                  refresh_factors=True, factor_trailing_days=3)
    assert receipt["factor_refresh_dates"] == ["2026-09-21", "2026-09-22", "2026-09-23"]
    assert receipt["new_rows"]["adjustment_factor"] == 2
    assert receipt["remote_calls"] == 2  # D1 upsert plus one factor batch; no D3/D4.
    after = LocalMarketStore(tmp_path)
    assert len(after.load_adjustment_factor()) == 6
    pd.testing.assert_frame_equal(after.load_adjustment_factor(end="2026-09-22"), historical)
    after.close()


def test_factor_refresh_common_renormalization_keeps_old_anchor(tmp_path):
    _make_store(tmp_path)
    class Renormalized(FakeFetcher):
        def factors(self, codes):
            self.remote_calls += 1
            return pd.DataFrame({code: [2.0, 2.0, 2.0] for code in codes},
                index=pd.to_datetime(["2026-09-21", "2026-09-22", "2026-09-23"]))
    fetcher = Renormalized()
    update_market_store(tmp_path, "2026-09-23", fetcher)
    receipt = update_market_store(tmp_path, "2026-09-23", fetcher,
                                  refresh_factors=True, factor_trailing_days=3)
    assert receipt["factor_issues"] == []
    store = LocalMarketStore(tmp_path)
    assert store.load_adjustment_factor().factor.tolist() == [1.0] * 6
    store.close()


def test_factor_ratio_change_is_flagged_without_overwrite(tmp_path):
    _make_store(tmp_path)
    class ChangedHistory(FakeFetcher):
        def factors(self, codes):
            self.remote_calls += 1
            return pd.DataFrame({code: [1.0, 2.0, 1.0] for code in codes},
                index=pd.to_datetime(["2026-09-21", "2026-09-22", "2026-09-23"]))
    fetcher = ChangedHistory()
    update_market_store(tmp_path, "2026-09-23", fetcher)
    receipt = update_market_store(tmp_path, "2026-09-23", fetcher,
                                  refresh_factors=True, factor_trailing_days=3)
    assert len(receipt["factor_issues"]) == 2
    assert receipt["new_rows"]["adjustment_factor"] == 0
    store = LocalMarketStore(tmp_path)
    assert len(store.load_adjustment_factor()) == 4
    assert store.manifest["latest_trade_date"]["adjustment_factor"] == "2026-09-22"
    store.close()


def test_targeted_bars_repair_rewrites_only_selected_key(tmp_path):
    _make_store(tmp_path)
    class RepairFetcher(FakeFetcher):
        def bars(self, code, start, end):
            self.remote_calls += 1
            return pd.DataFrame({"code": [code], "date": ["2026-09-22"],
                "open": [10.0], "high": [12.0], "low": [9.0], "close": [11.0],
                "volume": [100], "amount": [1100.0]})
    fetcher = RepairFetcher()
    receipt = update_market_store(tmp_path, "2026-09-22", fetcher,
        refresh_start="2026-09-22", refresh_datasets=("daily_bars",),
        refresh_codes=("000001.SZ",))
    assert receipt["remote_calls"] == 2
    assert receipt["new_rows"]["daily_bars"] == 0
    store = LocalMarketStore(tmp_path)
    repaired = store.load_daily_bars(start="2026-09-22", end="2026-09-22")
    assert repaired.set_index("security_id").loc["000001.SZ", "close"] == 11.0
    assert repaired.set_index("security_id").loc["600001.SH", "close"] == 10.0
    store.close()


def test_nontrading_target_calendar_check_is_idempotent(tmp_path):
    _make_store(tmp_path)
    class HolidayFetcher(FakeFetcher):
        def calendar(self, end):
            self.remote_calls += 1
            return ["2026-09-21", "2026-09-22"]
    fetcher = HolidayFetcher()
    first = update_market_store(tmp_path, "2026-09-23", fetcher)
    second = update_market_store(tmp_path, "2026-09-23", fetcher)
    assert first["status"] == second["status"] == "NO_OP"
    assert first["remote_calls"] == 1 and second["remote_calls"] == 0


def test_checkpoint_and_benchmark_cache_hash(tmp_path):
    path = tmp_path / "cache.csv.gz"
    pd.DataFrame({"code": ["000001.SZ"], "date": ["2026-09-23"],
        "open": [1], "high": [1], "low": [1], "close": [1],
        "volume": [1], "amount": [1]}).to_csv(path, compression="gzip", index=False)
    digest = sha256(path.read_bytes()).hexdigest()
    record = {"status": "COMPLETED", "cache_file": str(path), "sha256": digest}
    assert record_ok(record)
    assert validate_cache(path, digest, {"code", "date", "open"}, code="000001.SZ") == 1
    assert not record_ok({**record, "sha256": "wrong"})
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_cache(path, "wrong", {"code"})


def test_finite_retry_recovers_session_once(monkeypatch):
    from scripts import sync_market_store
    class Adapter:
        _session_provider = object()
    adapter = Adapter()
    replacement = object()
    monkeypatch.setattr(sync_market_store, "session", lambda a: replacement)
    monkeypatch.setattr(sync_market_store.time, "sleep", lambda _: None)
    calls = 0
    def transient_once(provider):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("transient")
        assert provider is replacement
        return "ok"
    record = {"attempts": [], "session_restart_count": 0}
    value, provider = call_with_retry(adapter, object(), transient_once, record)
    assert value == "ok" and provider is replacement
    assert [row["status"] for row in record["attempts"]] == ["FAILED", "SUCCESS"]
    assert record["session_restart_count"] == 1


def test_initial_materialization_quality_and_monthly_order(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    pd.DataFrame({"security_id": ["000001.SZ", "600001.SH"],
        "exchange": ["SZ", "SH"], "board": ["SZSE Main", "SSE Main"],
        "listing_date": ["2000-01-01"] * 2, "delisting_date": [None] * 2,
        "security_name": ["A", "B"]}).to_csv(source / "security_master.csv.gz", index=False, compression="gzip")
    records = {"D1_basic:batch_001": {"dataset": "D1_basic", "status": "COMPLETED",
        "finished_at": "2026-09-23T12:00:00+00:00"}}
    for code, day in (("000001.SZ", "2026-09-21"), ("600001.SH", "2026-09-22")):
        bar = pd.DataFrame({"code": [code], "date": [day], "open": [10.0],
            "high": [11.0], "low": [9.0], "close": [10.0], "volume": [100], "amount": [1000.0]})
        status = pd.DataFrame({"MARKET_CODE": [code, None],
            "TRADE_DATE": [int(day.replace("-", "")), None], "PRECLOSE": [10.0, None],
            "HIGH_LIMITED": [11.0, None], "LOW_LIMITED": [9.0, None],
            "PRICE_HIGH_LMT_RATE": [.1, None], "PRICE_LOW_LMT_RATE": [.1, None],
            "IS_ST_SEC": [0, None], "IS_SUSP_SEC": [0, None],
            "IS_WD_SEC": [0, None], "IS_XR_SEC": [0, None]})
        factor = pd.DataFrame({"security_id": [code], "trade_date": [day], "factor": [1.0]})
        for endpoint, frame in (("D3_bars", bar), ("D4_D6_status", status), ("D5_factor", factor)):
            path = source / f"{endpoint}_{code.replace('.', '_')}.csv.gz"
            frame.to_csv(path, index=False, compression="gzip")
            records[f"{endpoint}:{code}"] = {"dataset": endpoint, "status": "COMPLETED",
                "security_ids": [code], "cache_file": str(path), "finished_at": "2026-09-23T12:00:00+00:00",
                "sha256": sha256(path.read_bytes()).hexdigest()}
    (source / "checkpoint.json").write_text(json.dumps({"records": records}), encoding="utf-8")
    calendar = tmp_path / "calendar.json"
    calendar.write_text(json.dumps({"retrieved_at": "2026-09-23T12:00:00+00:00",
        "sessions": {"SH": ["2026-09-21", "2026-09-22"],
        "SZ": ["2026-09-21", "2026-09-22"]}}), encoding="utf-8")
    root = tmp_path / "store"
    manifest = materialize(root, source, calendar, start="2026-09-21", end="2026-09-22",
                           expected_security_count=2)
    assert manifest["dataset_row_counts"] == {"security_master": 2,
        "trading_calendar": 2, "daily_bars": 2, "daily_status": 2, "adjustment_factor": 2}
    assert manifest["quality"]["daily_status"]["null_key_rows"] == 2
    store = LocalMarketStore(root)
    assert store.load_daily_bars().security_id.tolist() == ["000001.SZ", "600001.SH"]
    assert store.load_daily_status().trade_date.dt.strftime("%Y-%m-%d").tolist() == [
        "2026-09-21", "2026-09-22"]
    store.close()
