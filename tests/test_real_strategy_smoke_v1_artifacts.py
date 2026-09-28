"""Frozen real-data smoke integrity without forward outcome access."""

import csv
import hashlib
import inspect
import json
from collections import Counter
from pathlib import Path

import duckdb
import pytest

from research.observation_runtime_v2 import advance_episode_v2
from scripts.run_real_strategy_smoke_v1 import _path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/stage1_real_strategy_smoke_v1"


def _rows(name):
    with (OUT / f"{name}.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_manifest_fingerprints_and_source_firewall():
    manifest = json.loads((OUT / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "REAL_STRATEGY_SMOKE_RUN_V1"
    assert manifest["reruns_identical"] and manifest["future_mutation_prefix_invariant"]
    assert len(manifest["prefix_invariance_dates"]) == 2
    assert not manifest["d8_accessed"] and not manifest["outcomes_accessed"]
    assert len(manifest["signal_source_datasets"]) == 5
    with pytest.raises(ValueError):
        _path("d8")
    for record in manifest["files"].values():
        path = OUT / record["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]
    for name, fingerprint in manifest["code_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == fingerprint


def test_formal_event_lineage_and_no_outcome_columns():
    qualification, review = _rows("qualification"), _rows("review")
    observations, lifecycle, entries = _rows("observations"), _rows("lifecycle"), _rows("entries")
    assert len(qualification) == 5540 and len(review) == 21
    assert len(observations) == 993 and len(entries) == 505
    assert len({row["candidate_id"] for row in qualification}) == len(qualification)
    assert len({(row["security_id"], row["trigger_date"]) for row in qualification}) == len(qualification)
    by_candidate = {row["candidate_id"]: row for row in qualification}
    by_episode = {row["observation_instance_id"]: row for row in observations}
    assert len(by_episode) == len(observations)
    assert Counter(row["qualification"] for row in qualification) == {
        "QUALIFIED_BY_V3_PRICE_PATH": 3337, "QUALIFIED_BY_OPERATIONAL_STATUS": 2071,
        "REJECTED_BY_OPERATIONAL_STATUS": 111, "REVIEW_REQUIRED": 21}
    assert {row["candidate_id"] for row in review} == {
        row["candidate_id"] for row in qualification if row["qualification"] == "REVIEW_REQUIRED"}
    for row in observations:
        source = by_candidate[row["candidate_id"]]
        assert source["observation_decision"] == "QUALIFIED"
        assert source["rsi_replay_status"] == "READY"
        assert row["trigger_date"] == source["trigger_date"]
        assert row["v3_lookback_hits"] in {"4", "5"}
        assert row["trigger_regime_qualification_method"] == source["qualification"]
    for row in entries:
        assert row["observation_instance_id"] in by_episode
        assert row["entry_date"] >= by_episode[row["observation_instance_id"]]["trigger_date"]
        assert row["fill_claim"] == "False"
        assert row["execution_intent"] in {"LIMIT_UP_CLOSE_BOARD_INTENT", "NORMAL_CLOSE_INTENT"}
    assert all(row["observation_instance_id"] in by_episode for row in lifecycle)
    assert not any(word in key.lower() for row in (qualification[0], observations[0], entries[0])
                   for key in row for word in ("forward", "return", "pnl", "mfe", "mae", "profit"))


def test_vendor_status_is_exact_frozen_trigger_day_row():
    db = duckdb.connect()
    source = db.execute(f"""
        SELECT c.candidate_id, s.is_st_sec
        FROM read_parquet('{str(ROOT / 'artifacts/stage1_targeted_regime/provisional_observation_candidate_v3.parquet').replace(chr(92), '/')}') c
        LEFT JOIN read_parquet('{_path('daily_status')}') s
          ON c.security_id=s.security_id AND c.trigger_date=s.trade_date
    """).fetchall()
    assert len(source) == 5540
    expected = {identity: value for identity, value in source}
    assert len(expected) == len(source)
    for row in _rows("qualification"):
        actual = {"True": True, "False": False, "": None}[row["vendor_is_st"]]
        assert actual is expected[row["candidate_id"]]
    assert "vendor_is_st" not in inspect.signature(advance_episode_v2).parameters


def test_600518_case_audit_preserves_vendor_conflict():
    cases = json.loads((OUT / "case_audit.json").read_text(encoding="utf-8"))["cases"]
    row = cases["600518_vendor_conflict_override"]
    assert row["vendor_stale_st"] is True and row["vendor_limit_rate"] == 0.1
    assert row["v3_status"] == "TRUE"
    assert row["operational_qualification"] == "QUALIFIED_BY_V3_PRICE_PATH"
