"""Synthetic path tests plus replay of the small official-evidence sample."""
import copy
import json
from pathlib import Path

import pytest

from research.d4_authority import expand_rule_matrix, resolve
from scripts.validate_stage1_d4_authority import replay


ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "stage1_d4_authority_closure"
MATRIX = json.loads((ART / "rule_matrix.json").read_text(encoding="utf-8"))
RULES = expand_rule_matrix(MATRIX)
SOURCES = {row["source_id"] for row in RULES} | {"SYNTHETIC_OFFICIAL"}


def fact(security_id, field, value, first="2023-01-01", known="2022-12-31T23:59:00+08:00",
         last="2026-12-31", source="SYNTHETIC_OFFICIAL"):
    return {"security_id": security_id, "field": field, "value": value,
            "known_at": known, "effective_at": first + "T00:00:00+08:00",
            "first_applicable_trading_date": first, "covered_until": last,
            "source_id": source}


def state(security_id="600187.SH", exchange="SSE", board="MAIN", risk=False,
          session=6, special="NONE", listing_kind="IPO"):
    return [fact(security_id, field, value) for field, value in (
        ("exchange", exchange), ("board", board), ("security_type", "A_SHARE"),
        ("listing_kind", listing_kind),
        ("listing_session", session), ("risk_warning", risk),
        ("special_status", special))]


@pytest.mark.parametrize("security_id,exchange,board,risk,day,regime", [
    ("600187.SH", "SSE", "MAIN", False, "2024-06-19", "LIMIT_10"),
    ("600187.SH", "SSE", "MAIN", True, "2026-07-03", "LIMIT_5"),
    ("600187.SH", "SSE", "MAIN", True, "2026-07-06", "LIMIT_10"),
    ("688511.SH", "SSE", "STAR", False, "2024-06-19", "LIMIT_20"),
    ("688511.SH", "SSE", "STAR", True, "2026-07-06", "LIMIT_20"),
    ("300750.SZ", "SZSE", "CHINEXT", False, "2024-09-27", "LIMIT_20"),
    ("300750.SZ", "SZSE", "CHINEXT", True, "2026-07-06", "LIMIT_20"),
    ("000838.SZ", "SZSE", "MAIN", True, "2026-07-03", "LIMIT_5"),
    ("000838.SZ", "SZSE", "MAIN", True, "2026-07-06", "LIMIT_10"),
])
def test_rule_paths_synthetic(security_id, exchange, board, risk, day, regime):
    result = resolve(security_id, day, state(security_id, exchange, board, risk), RULES, SOURCES)
    assert (result["qualification_state"], result["limit_regime"]) == ("VALID", regime)


@pytest.mark.parametrize("session,expected", [(1, "NO_DAILY_LIMIT"), (5, "NO_DAILY_LIMIT"),
                                                 (6, "LIMIT_10")])
def test_ipo_window_and_day_six(session, expected):
    result = resolve("600187.SH", "2024-06-19", state(session=session), RULES, SOURCES)
    assert result["limit_regime"] == expected


@pytest.mark.parametrize("special,expected", [("RELIST_FIRST", "NO_DAILY_LIMIT"),
                                                   ("DELIST_FIRST", "NO_DAILY_LIMIT"),
                                                   ("DELIST_REST", "LIMIT_10")])
def test_szse_lifecycle_windows(special, expected):
    result = resolve("000838.SZ", "2026-07-06",
                     state("000838.SZ", "SZSE", "MAIN", special=special,
                           session=1 if special == "RELIST_FIRST" else 6,
                           listing_kind="RELIST" if special == "RELIST_FIRST" else "IPO"),
                     RULES, SOURCES)
    assert result["limit_regime"] == expected


def test_relisting_second_session_is_not_ipo_window():
    result = resolve("000838.SZ", "2026-07-07",
                     state("000838.SZ", "SZSE", "MAIN", session=2,
                           listing_kind="RELIST"), RULES, SOURCES)
    assert result["limit_regime"] == "LIMIT_10"


def test_relisting_first_session_requires_explicit_special_fact():
    result = resolve("000838.SZ", "2026-07-06",
                     state("000838.SZ", "SZSE", "MAIN", session=1,
                           listing_kind="RELIST", special="NONE"), RULES, SOURCES)
    assert (result["qualification_state"], result["limit_regime"]) == ("CONFLICT", "UNKNOWN")


def test_missing_state_fails_closed():
    facts = [x for x in state() if x["field"] != "risk_warning"]
    result = resolve("600187.SH", "2024-06-19", facts, RULES, SOURCES)
    assert (result["qualification_state"], result["limit_regime"]) == ("MISSING", "UNKNOWN")
    assert "MISSING_RISK_WARNING" in result["reason_codes"]


def test_missing_rule_fails_closed():
    result = resolve("600187.SH", "2024-06-19", state(), [], SOURCES)
    assert result["qualification_state"] == "MISSING"
    assert result["limit_regime"] == "UNKNOWN"


def test_conflicting_state_fails_closed():
    facts = state() + [fact("600187.SH", "risk_warning", True)]
    result = resolve("600187.SH", "2024-06-19", facts, RULES, SOURCES)
    assert result["qualification_state"] == "CONFLICT"
    assert "CONFLICT_RISK_WARNING" in result["reason_codes"]


def test_conflicting_board_fails_closed():
    facts = state() + [fact("600187.SH", "board", "STAR")]
    result = resolve("600187.SH", "2024-06-19", facts, RULES, SOURCES)
    assert result["qualification_state"] == "CONFLICT"


def test_conflicting_rule_fails_closed():
    duplicate = copy.deepcopy(next(r for r in RULES if r["exchange"] == "SSE" and
                                   r["board"] == "MAIN" and r["category"] == "NORMAL" and
                                   r["effective_from"] == "2023-04-10"))
    duplicate["limit_regime"] = "LIMIT_20"
    result = resolve("600187.SH", "2024-06-19", state(), RULES + [duplicate], SOURCES)
    assert result["qualification_state"] == "CONFLICT"
    assert result["limit_regime"] == "UNKNOWN"


def test_future_state_cannot_rewrite_history():
    base = resolve("600187.SH", "2024-06-19", state(), RULES, SOURCES)
    future = fact("600187.SH", "risk_warning", True, "2026-05-06",
                  "2026-04-30T16:30:14+08:00")
    assert resolve("600187.SH", "2024-06-19", state() + [future], RULES, SOURCES) == base


def test_future_rule_cannot_rewrite_history():
    base = resolve("600187.SH", "2024-06-19", state(), RULES, SOURCES)
    future = copy.deepcopy(next(r for r in RULES if r["exchange"] == "SSE" and
                                r["board"] == "MAIN" and r["category"] == "NORMAL" and
                                r["effective_from"] == "2026-07-06"))
    future["effective_from"] = "2026-09-24"
    assert resolve("600187.SH", "2024-06-19", state(), RULES + [future], SOURCES) == base


def test_post_open_disclosure_does_not_apply_same_day():
    facts = state()
    facts = [x for x in facts if x["field"] != "risk_warning"]
    facts.append(fact("600187.SH", "risk_warning", True, "2024-06-19",
                      "2024-06-19T16:30:00+08:00"))
    result = resolve("600187.SH", "2024-06-19", facts, RULES, SOURCES)
    assert result["qualification_state"] == "MISSING"


def test_post_open_effective_at_does_not_apply_same_day():
    facts = state()
    facts = [x for x in facts if x["field"] != "risk_warning"]
    late = fact("600187.SH", "risk_warning", True, "2024-06-19",
                "2024-06-18T23:59:00+08:00")
    late["effective_at"] = "2024-06-19T10:00:00+08:00"
    result = resolve("600187.SH", "2024-06-19", facts + [late], RULES, SOURCES)
    assert result["qualification_state"] == "MISSING"


def test_unknown_source_is_not_authority():
    facts = [dict(x, source_id="VENDOR") if x["field"] == "risk_warning" else x for x in state()]
    assert resolve("600187.SH", "2024-06-19", facts, RULES, SOURCES)["qualification_state"] == "MISSING"


def test_no_bar_or_price_used_and_d6_is_independent():
    facts = state()
    base = resolve("600187.SH", "2024-06-19", facts, RULES, SOURCES)
    facts += [{"security_id": "600187.SH", "field": "suspended", "value": True},
              {"security_id": "600187.SH", "field": "vendor_limit", "value": "LIMIT_20"}]
    assert resolve("600187.SH", "2024-06-19", facts, RULES, SOURCES) == base


def test_real_sample_replay_is_fail_closed():
    result = replay()
    assert result == json.loads((ART / "bounded_sample_summary.json").read_text(encoding="utf-8"))
    assert result["counts"] == {"VALID": 1, "INVALID": 0, "MISSING": 15, "CONFLICT": 0}
