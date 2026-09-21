import pytest

from research.data.cache import SnapshotStore
from research.data.contracts import DataContractError
from research.runs.manifest import digest
from tests.test_research_data import bars, snapshot


def test_cold_warm_and_refresh_isolation(tmp_path):
    store = SnapshotStore(tmp_path)
    key = digest({"request": "fixture"})
    assert store.lookup(key) is None
    first = snapshot()
    store.save(first)
    store.commit_coverage(key, first.snapshot_id)
    assert store.lookup(key).content == first.content
    updated = bars()
    updated.loc[0, "amount"] = 99
    second = snapshot(updated)
    store.save(second)
    store.commit_coverage(key, second.snapshot_id)
    assert store.lookup(key).snapshot_id == second.snapshot_id
    assert store.load(first.snapshot_id).content == first.content


def test_coverage_requires_durable_object_and_complete_status(tmp_path):
    store = SnapshotStore(tmp_path)
    key = digest("request")
    with pytest.raises(DataContractError, match="SNAPSHOT_NOT_FOUND"):
        store.commit_coverage(key, "0" * 64)
    partial = snapshot(bars().iloc[:1])
    store.save(partial)
    with pytest.raises(DataContractError, match="INCOMPLETE_RESPONSE"):
        store.commit_coverage(key, partial.snapshot_id)
    assert store.lookup(key) is None


def test_interruption_between_data_and_coverage_keeps_old_reference(tmp_path, monkeypatch):
    store = SnapshotStore(tmp_path)
    key = digest("request")
    old = snapshot()
    store.save(old)
    store.commit_coverage(key, old.snapshot_id)
    new = snapshot(bars().assign(amount=100))
    store.save(new)
    original = store._atomic_write
    def interrupted(path, content, *, immutable):
        if not immutable:
            raise OSError("simulated interruption before coverage replacement")
        return original(path, content, immutable=immutable)
    monkeypatch.setattr(store, "_atomic_write", interrupted)
    with pytest.raises(OSError):
        store.commit_coverage(key, new.snapshot_id)
    assert store.lookup(key).snapshot_id == old.snapshot_id
    assert store.load(new.snapshot_id).content == new.content


def test_data_write_interruption_never_commits_coverage(tmp_path, monkeypatch):
    store = SnapshotStore(tmp_path)
    import research.data.cache as cache
    monkeypatch.setattr(cache.os, "fsync", lambda handle: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        store.save(snapshot())
    assert not list(tmp_path.rglob("*.json"))
    assert not list(tmp_path.rglob(".pending-*"))


def test_corruption_and_path_traversal_rejected(tmp_path):
    store = SnapshotStore(tmp_path)
    snap = snapshot()
    store.save(snap)
    (tmp_path / "objects" / f"{snap.snapshot_id}.json").write_bytes(b"bad")
    with pytest.raises(DataContractError, match="SNAPSHOT_CORRUPT"):
        store.load(snap.snapshot_id)
    with pytest.raises(DataContractError, match="INVALID_ARGUMENT"):
        store.load("../outside")


def test_legacy_cache_interruption_and_empty_refresh_do_not_claim_data(tmp_path, monkeypatch):
    from three_board_rsi_entry.market_data import AmazingDataMarketDataProvider, BAR_COLUMNS
    from tests.test_research_data import CODE, DAYS
    import pandas as pd
    provider = AmazingDataMarketDataProvider(tmp_path, legacy_provider_root=tmp_path)
    raw = bars().rename(columns={"trade_date": "date", **{f"raw_{p}": p for p in ("open", "high", "low", "close")}})
    monkeypatch.setattr(provider._adapter, "get_daily_bars", lambda *args: raw.copy())
    original = provider._cache.upsert
    monkeypatch.setattr(provider._cache, "upsert", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted before data")))
    with pytest.raises(OSError):
        provider._fetch_raw_code(CODE, DAYS, False)
    assert provider._cache.read_coverage(CODE) == set()
    monkeypatch.setattr(provider._cache, "upsert", original)
    first = provider._fetch_raw_code(CODE, DAYS, False)
    assert len(first) == 3
    pd.testing.assert_frame_equal(first, provider._fetch_raw_code(CODE, DAYS, False))
    monkeypatch.setattr(provider._adapter, "get_daily_bars", lambda *args: pd.DataFrame(columns=BAR_COLUMNS))
    assert provider._fetch_raw_code(CODE, DAYS, True).empty
    assert provider._cache.read_coverage(CODE) == set()
    assert provider._cache.read(CODE).empty
