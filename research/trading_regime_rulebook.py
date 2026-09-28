"""Official, effective-dated price-limit rules; no supplier fields are inputs."""

from __future__ import annotations

from datetime import date


SOURCES = {
    "SSE_2023": {
        "authority": "上海证券交易所",
        "title": "上海证券交易所交易规则（2023年修订）",
        "url": "https://www.sse.com.cn/lawandrules/sselawsrules2025/repeal/rules/c/c_20250612_10824490.shtml",
        "published": "2023-02-17",
        "effective": "2023-04-10",
        "effective_date_support_url": "https://big5.sse.com.cn/site/cht/www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20230404_5719112.shtml",
    },
    "SSE_2026": {
        "authority": "上海证券交易所",
        "title": "上海证券交易所交易规则（2026年修订）",
        "url": "https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml",
        "published": "2026-04-24",
        "effective": "2026-07-06",
    },
    "SZSE_2023": {
        "authority": "深圳证券交易所",
        "title": "深圳证券交易所交易规则（2023年修订）",
        "url": "https://docs.static.szse.cn/www/lawrules/rule/trade/W020230217564423808793.pdf",
        "published": "2023-02-17",
        "effective": "2023-04-10",
        "effective_date_support_url": "https://www.szse.cn/www/marketServices/technicalservice/doc/P020230724535729507237.pdf",
    },
    "SZSE_2026": {
        "authority": "深圳证券交易所",
        "title": "深圳证券交易所交易规则（2026年修订）",
        "url": "https://docs.static.szse.cn/www/lawrules/rule/trade/W020260424690713155663.pdf",
        "published": "2026-04-24",
        "effective": "2026-07-06",
    },
    "BSE_2021": {
        "authority": "北京证券交易所",
        "title": "北京证券交易所交易规则（试行）",
        "url": "https://www.bse.cn/jygl_list/200010919.html",
        "published": "2021-11-02",
        "effective": "2021-11-15",
    },
    "BSE_2026": {
        "authority": "北京证券交易所",
        "title": "北京证券交易所交易规则",
        "url": "https://www.bse.cn/jygl_list/200028217.html",
        "published": "2026-04-24",
        "effective": "2026-07-06",
    },
}

# Each epoch is (exchange, board, source, start, end, base rate,
# base clause, no-limit clause, risk clause). The only dated risk-rate
# overrides are the two main boards before 2026-07-06.
EPOCHS = (
    ("SSE", "MAIN", "SSE_2023", "2024-01-01", "2026-07-05", "0.10", "3.3.13", "3.3.13", "4.4.10"),
    ("SSE", "MAIN", "SSE_2026", "2026-07-06", "2026-09-23", "0.10", "3.3.13", "3.3.13", None),
    ("SSE", "STAR", "SSE_2023", "2024-01-01", "2026-07-05", "0.20", "6.1.6", "6.1.6", None),
    ("SSE", "STAR", "SSE_2026", "2026-07-06", "2026-09-23", "0.20", "6.6", "6.6", None),
    ("SZSE", "MAIN", "SZSE_2023", "2024-01-01", "2026-07-05", "0.10", "3.3.13", "3.3.15", "4.5.5"),
    ("SZSE", "MAIN", "SZSE_2026", "2026-07-06", "2026-09-23", "0.10", "3.3.13", "3.3.15", None),
    ("SZSE", "CHINEXT", "SZSE_2023", "2024-01-01", "2026-07-05", "0.20", "3.3.13", "3.3.15", None),
    ("SZSE", "CHINEXT", "SZSE_2026", "2026-07-06", "2026-09-23", "0.20", "3.3.13", "3.3.15", None),
    ("BSE", "BSE", "BSE_2021", "2024-01-01", "2026-07-05", "0.30", "3.3.11", "3.3.12", None),
    ("BSE", "BSE", "BSE_2026", "2026-07-06", "2026-09-23", "0.30", "3.3.11", "3.3.12", None),
)


def build_rulebook() -> dict:
    """Build the source-qualified rule matrix, not a qualified daily dataset."""
    rules = []

    def add(exchange, board, source_id, start, end, status, phase, rate, clause, priority):
        source = SOURCES[source_id]
        rules.append({
            "rule_id": f"{exchange}-{board}-{status}-{phase}-{start}",
            "exchange": exchange, "board": board,
            "security_type": "A_SHARE_COMMON_STOCK",
            "special_status": status, "listing_phase": phase,
            "effective_from": start, "effective_to": end,
            "price_limit_up_rate": rate, "price_limit_down_rate": rate,
            "has_price_limit": rate is not None,
            "is_symmetric": True if rate is not None else None,
            "rule_source_type": "OFFICIAL_EXCHANGE_RULE",
            "rule_source_title": source["title"],
            "rule_source_url": source["url"],
            "rule_source_publish_date": source["published"],
            "rule_effective_date": source["effective"],
            "source_quote_or_clause_reference": clause,
            "qualification_status": "SOURCE_QUALIFIED_ONLY",
            "priority": priority,
            "notes": "No supplier limit-rate or ST field defines this rule.",
        })

    for exchange, board, source, start, end, rate, base_clause, exception_clause, risk_clause in EPOCHS:
        add(exchange, board, source, start, end, "ANY", "NORMAL_LISTED", rate, base_clause, 10)
        if risk_clause:
            add(exchange, board, source, start, end, "RISK_WARNING", "NORMAL_LISTED", "0.05", risk_clause, 50)
        # The first delisting-arrangement day overrides the otherwise applicable
        # risk-warning or ordinary rate; subsequent days use the board rate.
        if risk_clause:
            add(exchange, board, source, start, end, "DELISTING_ARRANGEMENT", "NORMAL_LISTED", rate,
                "4.4.10" if exchange == "SSE" else "4.5.5", 60)
        add(exchange, board, source, start, end, "ANY", "DELISTING_DAY_1", None, exception_clause, 100)
        ipo_phase = "IPO_DAY_1" if exchange == "BSE" else "IPO_DAY_1_TO_5"
        add(exchange, board, source, start, end, "ANY", ipo_phase, None, exception_clause, 100)
        if board == "MAIN":
            add(exchange, board, source, start, end, "ANY", "RELISTING_DAY_1", None,
                "3.3.13" if exchange == "SSE" else "3.3.15", 100)

    return {
        "rulebook_id": "TRADING_REGIME_RULEBOOK_V1",
        "research_window": ["2024-01-01", "2026-09-23"],
        "qualification_status": "SOURCE_QUALIFIED_DAILY_FACTS_BLOCKED",
        "rate_semantics": "A positive symmetric magnitude; down direction is negative. Null means no daily price limit.",
        "precedence": "Highest explicit priority after exact exchange/board/date/phase/status matching; a same-priority tie fails closed.",
        "risk_status_semantics": "ST and *ST both map to RISK_WARNING for rate purposes; issuer/exchange effective events are still required per security-day.",
        "rules": rules,
    }


def resolve_rule(rulebook: dict, exchange: str, board: str, day: date,
                 phase: str, special_status: str) -> dict:
    """Resolve only when the caller already has qualified PIT classification."""
    valid_phases = {"NORMAL_LISTED", "DELISTING_DAY_1", "RELISTING_DAY_1"}
    valid_phases.update(f"IPO_DAY_{number}" for number in range(1, 6))
    if phase not in valid_phases or special_status not in {
        "NORMAL", "RISK_WARNING", "DELISTING_ARRANGEMENT"
    }:
        raise ValueError("UNQUALIFIED_TRADING_STATUS_OR_PHASE")
    if exchange == "BSE" and phase in {f"IPO_DAY_{number}" for number in range(2, 6)}:
        raise ValueError("UNQUALIFIED_TRADING_STATUS_OR_PHASE")

    def phase_matches(rule_phase: str) -> bool:
        return (rule_phase == phase or rule_phase == "NORMAL_LISTED"
                or (rule_phase == "IPO_DAY_1_TO_5" and phase in
                    {f"IPO_DAY_{number}" for number in range(1, 6)}))

    matches = [rule for rule in rulebook["rules"]
               if rule["exchange"] == exchange and rule["board"] == board
               and rule["effective_from"] <= day.isoformat() <= rule["effective_to"]
               and phase_matches(rule["listing_phase"])
               and rule["special_status"] in (special_status, "ANY")]
    if not matches:
        raise ValueError("NO_APPLICABLE_TRADING_RULE")
    priority = max(rule["priority"] for rule in matches)
    winners = [rule for rule in matches if rule["priority"] == priority]
    if len(winners) != 1:
        raise ValueError("BLOCKED_TRADING_RULE_PRECEDENCE_UNRESOLVED")
    return winners[0]
