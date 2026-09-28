from copy import deepcopy
from datetime import date

import pytest

from research.trading_regime_rulebook import build_rulebook, resolve_rule


BOOK = build_rulebook()


@pytest.mark.parametrize("exchange,board,day,status,rate", [
    ("SSE", "MAIN", "2026-07-03", "NORMAL", "0.10"),
    ("SSE", "MAIN", "2026-07-03", "RISK_WARNING", "0.05"),
    ("SSE", "MAIN", "2026-07-06", "RISK_WARNING", "0.10"),
    ("SZSE", "MAIN", "2026-07-03", "RISK_WARNING", "0.05"),
    ("SZSE", "MAIN", "2026-07-06", "RISK_WARNING", "0.10"),
    ("SZSE", "CHINEXT", "2025-06-03", "RISK_WARNING", "0.20"),
    ("SSE", "STAR", "2025-06-03", "RISK_WARNING", "0.20"),
    ("BSE", "BSE", "2025-06-03", "RISK_WARNING", "0.30"),
])
def test_official_board_and_risk_rates(exchange, board, day, status, rate):
    rule = resolve_rule(BOOK, exchange, board, date.fromisoformat(day), "NORMAL_LISTED", status)
    assert rule["price_limit_up_rate"] == rate
    assert rule["price_limit_down_rate"] == rate
    assert rule["has_price_limit"] is True


@pytest.mark.parametrize("exchange,board,ipo_days", [
    ("SSE", "MAIN", 5),
    ("SZSE", "MAIN", 5),
    ("SZSE", "CHINEXT", 5),
    ("SSE", "STAR", 5),
    ("BSE", "BSE", 1),
])
def test_ipo_no_limit_phase(exchange, board, ipo_days):
    for n in range(1, ipo_days + 1):
        rule = resolve_rule(BOOK, exchange, board, date(2025, 6, 3), f"IPO_DAY_{n}", "NORMAL")
        assert rule["has_price_limit"] is False
        assert rule["price_limit_up_rate"] is None
    if exchange == "BSE":
        assert resolve_rule(BOOK, exchange, board, date(2025, 6, 4), "NORMAL_LISTED", "NORMAL")["price_limit_up_rate"] == "0.30"


def test_delisting_first_day_overrides_risk_warning():
    rule = resolve_rule(BOOK, "SSE", "MAIN", date(2025, 6, 3),
                        "DELISTING_DAY_1", "RISK_WARNING")
    assert rule["has_price_limit"] is False
    subsequent = resolve_rule(BOOK, "SSE", "MAIN", date(2025, 6, 4),
                              "NORMAL_LISTED", "DELISTING_ARRANGEMENT")
    assert subsequent["price_limit_up_rate"] == "0.10"


def test_same_priority_ambiguity_fails_closed():
    book = deepcopy(BOOK)
    book["rules"].append(deepcopy(book["rules"][0]))
    with pytest.raises(ValueError, match="BLOCKED_TRADING_RULE_PRECEDENCE_UNRESOLVED"):
        resolve_rule(book, "SSE", "MAIN", date(2025, 6, 3), "NORMAL_LISTED", "NORMAL")


def test_rulebook_does_not_consume_vendor_fields():
    assert all("vendor" not in key for rule in BOOK["rules"] for key in rule)
    assert all(rule["rule_source_url"].startswith("https://") for rule in BOOK["rules"])


def test_exact_2026_effective_date_and_unqualified_phase():
    before = resolve_rule(BOOK, "SZSE", "MAIN", date(2026, 7, 5),
                          "NORMAL_LISTED", "RISK_WARNING")
    after = resolve_rule(BOOK, "SZSE", "MAIN", date(2026, 7, 6),
                         "NORMAL_LISTED", "RISK_WARNING")
    assert before["rule_effective_date"] == "2023-04-10"
    assert after["rule_effective_date"] == "2026-07-06"
    with pytest.raises(ValueError, match="UNQUALIFIED_TRADING_STATUS_OR_PHASE"):
        resolve_rule(BOOK, "BSE", "BSE", date(2025, 6, 3), "IPO_DAY_2", "NORMAL")
    with pytest.raises(ValueError, match="UNQUALIFIED_TRADING_STATUS_OR_PHASE"):
        resolve_rule(BOOK, "SSE", "MAIN", date(2025, 6, 3), "NORMAL_LISTED", "UNKNOWN")
