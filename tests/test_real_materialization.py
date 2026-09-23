"""Synthetic contract checks; these do not qualify an uncaptured real universe."""

import ast
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from hashlib import sha256
from pathlib import Path

import pytest

from research.event_study import ForwardEventStudy, PriceBar, StudyEvent
from research.factor_pit import CorporateAction
from research.foundation import TradingCalendar
from research.outcome_path import comparable_forward_path
from research.real_data_qualification import EffectiveIdentity, trading_state
from research.real_materialization import (
    IdentityDay, audit_bar_snapshot, audit_daily_constraints, bar_validation_status,
    compress_identity_days, expand_identity_intervals, factor_prefix_admission,
    freeze_private_blob, max_full_horizon_signal_day,
)
from scripts.verify_stage1_real_materialization_stop import verify


def day(value):
    return date.fromisoformat(value)


def identity(trade_date, **changes):
    row = IdentityDay("S", trade_date, "A_SHARE_COMMON_STOCK", "SSE", True,
                      day("2020-01-01"), day("2020-01-01"), True, False, None,
                      "VERIFIED", "historical-list+exchange", "snapshot")
    return replace(row, **changes)


def test_d1_ipo_delisting_relisting_and_interval_roundtrip():
    sessions = tuple(map(day, ("2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05")))
    rows = (
        identity(sessions[0], listed=False, lifecycle_active=False, identity_status="VERIFIED_NOT_LISTED"),
        identity(sessions[1], listing_date=sessions[1], latest_listing_or_relisting_date=sessions[1]),
        identity(sessions[2], listed=False, lifecycle_active=False, delisted=True,
                 lifecycle_end_date=sessions[2], identity_status="VERIFIED_ENDED"),
        identity(sessions[3], listing_date=sessions[1], latest_listing_or_relisting_date=sessions[3]),
    )
    intervals = compress_identity_days(rows, sessions)
    assert expand_identity_intervals(intervals, sessions) == rows
    assert len(intervals) == 4
    with pytest.raises(ValueError, match="Missing identity"):
        compress_identity_days(rows[:-1], sessions)
    with pytest.raises(ValueError, match="Duplicate identity"):
        compress_identity_days((*rows, rows[-1]), sessions)


def test_d3_bar_validation_and_snapshot_accounting():
    dates = (day("2024-01-02"), day("2024-01-03"), day("2024-01-04"))
    expected = {("S", value) for value in dates}
    good = {"security_id": "S", "trade_date": dates[0], "open": "10", "high": "11",
            "low": "9", "close": "10.5", "volume": "0", "amount": "0"}
    invalid = {**good, "trade_date": dates[1], "high": "9"}
    assert bar_validation_status(good) == "BAR_VALID"
    assert bar_validation_status(invalid) == "BAR_INVALID"
    assert audit_bar_snapshot((good, invalid), expected) == {
        "expected": 3, "valid": 1, "invalid": 1, "missing": 1, "conflict": 0, "unexpected": 0}
    assert audit_bar_snapshot((good, good), {("S", dates[0])})["conflict"] == 1


def test_d4_bulk_constraint_audit_never_promotes_missing_or_adjusted():
    dates = (day("2024-01-02"), day("2024-01-03"), day("2024-01-04"))
    expected = {("S", value) for value in dates}
    valid = {"security_id": "S", "trade_date": dates[0], "validation_status": "VALID",
             "price_basis": "RAW", "source_path": "OFFICIAL_RULE_RECONSTRUCTION",
             "limit_regime": "10_PERCENT"}
    adjusted = {**valid, "trade_date": dates[1], "price_basis": "ADJUSTED"}
    result = audit_daily_constraints((valid, adjusted), expected)
    assert result["VALID"] == result["CONFLICT"] == result["MISSING"] == 1
    assert result["10_PERCENT"] == 1
    assert sum(result[key] for key in ("VALID", "INVALID", "MISSING", "CONFLICT")) == 3


def test_d5_prefix_admission_excludes_unresolved_changed_event():
    changed = (day("2025-06-26"),)
    args = dict(factor_present=True, events_complete=True, source_frozen=True,
                revision_dates_unresolved=changed)
    assert factor_prefix_admission(**args, as_of=day("2025-06-25")) == "QUALIFIED"
    assert factor_prefix_admission(**args, as_of=changed[0]) == "UNRESOLVED_REVISION"
    assert factor_prefix_admission(**{**args, "events_complete": False},
                                   as_of=day("2025-06-25")) == "EVENT_CHRONOLOGY_INCOMPLETE"


def test_d6_status_bar_conflict_and_missing_bar_unknown():
    identities = (EffectiveIdentity("S", "A_SHARE_COMMON_STOCK", "SSE",
                                    day("2024-01-01"), None, day("2024-01-01")),)
    assert trading_state(identities=identities, security_id="S", day=day("2024-01-02"),
                         status="SUSPENDED", bar_present=True) == "CONFLICT"
    assert trading_state(identities=identities, security_id="S", day=day("2024-01-02"),
                         status=None, bar_present=False) == "UNKNOWN"


def test_d7_exclusive_snapshot_freeze_and_replay_hash(tmp_path):
    target = tmp_path / "snapshot.bin"
    digest = freeze_private_blob(target, b"frozen", private_root=tmp_path)
    assert digest == sha256(target.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        freeze_private_blob(target, b"changed", private_root=tmp_path)
    with pytest.raises(ValueError, match="private"):
        freeze_private_blob(tmp_path.parent / "public.bin", b"raw", private_root=tmp_path)
    assert target.read_bytes() == b"frozen"


def test_d8_price_scale_and_signal_import_firewall():
    signal, entry, action = map(day, ("2024-06-17", "2024-06-18", "2024-06-19"))
    calendar = TradingCalendar((signal, entry, action), "calendar")
    event = StudyEvent("E", "S", signal, entry, D(100), ("TRACE",), "snapshot",
                       ("ENTRY_COMPARABLE_FORWARD_PATH_V1",))
    bars = (PriceBar(entry, D(105), D(95), D(100)), PriceBar(action, D(55), D(48), D(50)))
    no_action = comparable_forward_path(security_id="S", snapshot_id="snapshot",
                                        entry_date=entry, bars=bars, actions=(),
                                        action_source_hash="a" * 64,
                                        action_chronology_complete=True)
    assert no_action.bars == bars
    corporate_action = CorporateAction("CA", "DIVIDEND", action, signal, D(2),
                                       "IMPLEMENTED", 1, True)
    path = comparable_forward_path(security_id="S", snapshot_id="snapshot",
                                   entry_date=entry, bars=bars, actions=(corporate_action,),
                                   action_source_hash="a" * 64,
                                   action_chronology_complete=True)
    assert ForwardEventStudy(calendar, (2,)).evaluate(event, path).horizons[0].ret == D(0)
    with pytest.raises(ValueError, match="Incomplete"):
        comparable_forward_path(security_id="S", snapshot_id="snapshot", entry_date=entry,
                                bars=bars, actions=(corporate_action,),
                                action_source_hash="a" * 64, action_chronology_complete=False)
    root = Path(__file__).resolve().parents[1] / "research"
    for name in ("foundation", "indicator_prices", "primitives", "observation_pool", "entry_signals"):
        tree = ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(module and (module.endswith("outcome_path") or module.endswith("event_study"))
                       for module in imports)


def test_full_h20_signal_date_requires_t_plus_20():
    sessions = tuple(day(f"2024-01-{value:02d}") for value in range(1, 23))
    assert max_full_horizon_signal_day(sessions, sessions[-1]) == sessions[1]
    with pytest.raises(ValueError, match="calendar"):
        max_full_horizon_signal_day(sessions[:20], sessions[19])


def test_public_stop_receipt_replays_without_private_market_rows():
    result = verify()
    assert result["status"] == "STAGE1_REAL_DATA_MATERIALIZATION_STOP"
    assert result["max_full_h20_signal_date"] == "2026-08-26"
