from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from research.indicator_prices import IndicatorPrice, IndicatorPriceSeries, PRICE_BASIS
from research.primitives import rsi14
from research.provisional_candidates import (
    RsiPrefix, candidate_id, limit_up_like, provisional_window,
)
from research.targeted_limit_regime import candidate_disposition, qualify_dependency
from research.trading_regime_rulebook import build_rulebook, resolve_rule
from scripts.build_provisional_observation_candidates import _phase


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/stage1_targeted_regime"
BOOK = build_rulebook()


def test_broad_detector_includes_ten_percent_move_and_vendor_supplement():
    hit, methods = limit_up_like(
        close="11.00", vendor_upper=None, vendor_preclose="10.00",
        previous_raw_close="10.00",
    )
    assert hit and "VENDOR_PRECLOSE_MOVE_GE_5PCT" in methods
    hit, methods = limit_up_like(
        close="10.10", vendor_upper="10.10", vendor_preclose="10.00",
        previous_raw_close="11.00",
    )
    assert hit and methods == ("VENDOR_UPPER_MATCH",)
    # A 5% risk-warning hit is intentionally a provisional false positive.
    assert limit_up_like(close="10.50", vendor_upper="10.50",
                         vendor_preclose="10", previous_raw_close="10")[0]
    assert not limit_up_like(close="9", vendor_upper="11",
                             vendor_preclose="10", previous_raw_close="10")[0]


def test_four_of_five_rsi_threshold_and_stable_candidate_identity():
    assert provisional_window([True, True, False, True, True], Decimal("70.0001"))
    assert provisional_window([True] * 5, Decimal("71"))
    assert not provisional_window([True] * 5, Decimal("70"))
    assert not provisional_window([True, False, False, True, True], Decimal("90"))
    assert candidate_id("600000.SH", "2024-01-08") == candidate_id(
        "600000.SH", "2024-01-08"
    )


def test_rsi_stream_matches_frozen_primitive_and_future_rows_do_not_mutate_prefix():
    prices = ["10", "11", "10", "12", "11.5", "12.5"]
    factors = ["1", "1", "1", "1.1", "1.1", "1.1"]
    start = date(2025, 1, 6)
    zone = timezone(timedelta(hours=8))
    stream = RsiPrefix()
    observations = []
    for n, (price, factor) in enumerate(zip(prices, factors)):
        day = start + timedelta(days=n)
        available = datetime(day.year, day.month, day.day, 15, tzinfo=zone)
        value = stream.update(price, factor)
        observations.append(IndicatorPrice(
            "600000.SH", day, Decimal(price) * Decimal(factor), PRICE_BASIS,
            available, "synthetic", True, "TRADING",
        ))
        if n > 0:
            series = IndicatorPriceSeries(
                "600000.SH", day, start, PRICE_BASIS, "synthetic",
                "synthetic-calendar", "daily", tuple(observations),
            )
            decision_at = available + timedelta(minutes=10)
            canonical = rsi14(series, decision_at=decision_at)
            assert canonical.ready and abs(value - canonical.value) < Decimal("1e-23")
    prior = value
    stream.update("13", "1.1")
    assert prior == canonical.value or abs(prior - canonical.value) < Decimal("1e-23")


def test_ipo_pre_window_and_board_prefilter():
    sessions = tuple(date(2024, 1, day) for day in (2, 3, 4, 5))
    prefix = {
        "SH": tuple(date(2023, 12, day) for day in (27, 28, 29)),
        "SZ": tuple(date(2023, 12, day) for day in (26, 27, 28, 29)),
    }
    assert _phase(date(2023, 12, 26), sessions[0], "SZ", sessions, prefix) == "IPO_DAY_5"
    assert _phase(date(2023, 12, 27), sessions[0], "SH", sessions, prefix) == "IPO_DAY_4"
    assert _phase(date(2020, 1, 2), sessions[0], "SH", sessions, prefix) == "NORMAL_LISTED"
    assert qualify_dependency(rulebook=BOOK, exchange="SZ", board="ChiNext",
                              security_id="300001.SZ",
                              day=sessions[0], listing_phase="NORMAL_LISTED").status == "REJECTED_NON_10PCT"
    assert qualify_dependency(rulebook=BOOK, exchange="SH", board="STAR",
                              security_id="688001.SH",
                              day=sessions[0], listing_phase="NORMAL_LISTED").status == "REJECTED_NON_10PCT"


def test_official_cases_and_vendor_independence():
    cases = json.loads((ROOT / "artifacts/stage1_limit_regime/limit_regime_official_evidence_inventory.json")
                       .read_text(encoding="utf-8"))["documents"]
    removal = next(row for row in cases if row["security_id"] == "600518.SH")
    result = qualify_dependency(
        rulebook=BOOK, exchange="SH", board="SSE Main", security_id="600518.SH",
        day=date(2024, 7, 4),
        listing_phase="NORMAL_LISTED", official_case=removal,
    )
    assert result.status == "QUALIFIED_10PCT" and result.method == "OFFICIAL_EFFECTIVE_DAY"
    introduction = next(row for row in cases if row["security_id"] == "000070.SZ"
                        and row["transition"] == "NORMAL_TO_RISK_WARNING_REGIME")
    result = qualify_dependency(
        rulebook=BOOK, exchange="SZ", board="SZSE Main", security_id="000070.SZ",
        day=date(2024, 5, 14),
        listing_phase="NORMAL_LISTED", official_case=introduction,
    )
    assert result.status == "REJECTED_NON_10PCT"
    # Without a dated, independently checked exception fact, the 2026
    # board rule alone does not silently turn a real V1 day into qualified.
    unresolved = qualify_dependency(
        rulebook=BOOK, exchange="SH", board="SSE Main", security_id="600187.SH",
        day=date(2026, 7, 6),
        listing_phase="NORMAL_LISTED",
    )
    assert unresolved.status == "UNRESOLVED"
    assert resolve_rule(BOOK, "SSE", "MAIN", date(2026, 7, 6),
                        "NORMAL_LISTED", "RISK_WARNING")["price_limit_up_rate"] == "0.10"
    db = duckdb.connect()
    vendor_rate = db.execute(
        "SELECT price_high_lmt_rate FROM read_parquet(?) "
        "WHERE security_id = '600187.SH' AND trade_date = DATE '2026-07-06'",
        [str(ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1/daily_status/**/*.parquet")],
    ).fetchone()[0]
    assert vendor_rate == 0.05
    future_announcement = deepcopy(removal)
    future_announcement["source_publish_date"] = "2024-07-05"
    assert qualify_dependency(
        rulebook=BOOK, exchange="SH", board="SSE Main", security_id="600518.SH",
        day=date(2024, 7, 4),
        listing_phase="NORMAL_LISTED", official_case=future_announcement,
    ).status == "UNRESOLVED"
    assert qualify_dependency(
        rulebook=BOOK, exchange="SH", board="SSE Main", security_id="600289.SH",
        day=date(2024, 7, 4),
        listing_phase="NORMAL_LISTED", official_case=removal,
    ).status == "UNRESOLVED"


def test_fail_closed_candidate_disposition_and_ipo_rejection():
    unknown = qualify_dependency(rulebook=BOOK, exchange="SH", board="SSE Main",
                                 security_id="600000.SH",
                                 day=date(2025, 1, 6), listing_phase="NORMAL_LISTED")
    ipo = qualify_dependency(rulebook=BOOK, exchange="SH", board="SSE Main",
                             security_id="600000.SH",
                             day=date(2025, 1, 6), listing_phase="IPO_DAY_1")
    assert unknown.status == "UNRESOLVED"
    assert ipo.status == "REJECTED_NON_10PCT"
    assert candidate_disposition([unknown] * 5) == "UNRESOLVED"
    assert candidate_disposition([unknown] * 4 + [ipo]) == "REJECTED_NON_10PCT"


def test_generated_artifacts_reconcile_and_remain_provisional():
    provisional = json.loads((OUT / "provisional_candidate_inventory.json").read_text())
    triage = json.loads((OUT / "targeted_qualification_receipt.json").read_text())
    assert provisional["status"] == "REGIME_UNVERIFIED_NOT_FORMAL_OBSERVATION"
    assert provisional["candidate_count"] == sum(provisional["quality"][name]
                                                  for name in ("four_of_five", "five_of_five"))
    assert triage["provisional_candidates"] == provisional["candidate_count"]
    assert triage["dependency_security_dates"] == provisional["dependency_security_dates"]
    assert sum(triage["qualification_status_counts"].values()) == triage["dependency_security_dates"]
    assert sum(triage["candidate_disposition_counts"].values()) == provisional["candidate_count"]
    assert triage["candidate_formal_upgrade_status"].startswith("NOT_RUN_")
    assert not (OUT / "qualified_observation_event_v1.parquet").exists()
    assert not (OUT / "qualified_entry_event_v1.parquet").exists()
    assert "outcome_adjustment" not in (
        ROOT / "scripts/build_provisional_observation_candidates.py"
    ).read_text(encoding="utf-8")
    assert "outcome_adjustment" not in (
        ROOT / "scripts/build_targeted_regime_triage.py"
    ).read_text(encoding="utf-8")
    assert hashlib.sha256((OUT / "provisional_observation_candidate_v1.parquet").read_bytes()).hexdigest() == provisional["candidate_parquet_sha256"]
    db = duckdb.connect()
    row = db.execute("SELECT count(*), count(DISTINCT candidate_id), "
                     "count(*) FILTER (WHERE regime_status != 'UNVERIFIED' "
                     "OR qualification_status != 'PENDING') "
                     "FROM read_parquet(?)", [str(OUT / "provisional_observation_candidate_v1.parquet")]).fetchone()
    assert row == (provisional["candidate_count"], provisional["candidate_count"], 0)


def test_cleanup_retains_audit_evidence_and_active_path_skips_old_backfill():
    cleanup = json.loads((OUT / "stage1_strategy_path_cleanup_receipt.json").read_text())
    assert cleanup["history_preserved"] is True
    assert cleanup["historical_blocker_commits"] == ["0af305a", "00d4c1d"]
    assert not cleanup["deleted_local_only_files"]
    assert cleanup["default_active_command_calls_full_market_crawler"] is False
    assert (ROOT / "artifacts/stage1_trading_regime/trading_regime_rule_sources.json").exists()
    assert (ROOT / "artifacts/stage1_limit_regime/limit_regime_official_evidence_inventory.json").exists()
    active = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in (
        "scripts/build_provisional_observation_candidates.py",
        "scripts/build_targeted_regime_triage.py",
    ))
    assert "hisAnnouncement" not in active
    assert "build_limit_regime_candidate_inventory" not in active
