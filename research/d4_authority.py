"""PIT-safe Stage 1 D4 regime resolver; no prices, bars, or vendor labels."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone


REQUIRED = ("exchange", "board", "security_type", "listing_kind", "listing_session", "special_status")
REGIMES = {"LIMIT_5", "LIMIT_10", "LIMIT_20", "NO_DAILY_LIMIT", "OTHER_EXPLICIT"}
SHANGHAI = timezone(timedelta(hours=8))


def expand_rule_matrix(matrix: dict) -> list[dict]:
    """Expand frozen, clause-addressed period rows without inferring absent categories."""
    return [
        {"exchange": period["exchange"], "board": period["board"],
         "effective_from": period["effective_from"], "effective_to": period["effective_to"],
         "known_at": period["known_at"], "source_id": period["source_id"],
         "category": category, **spec}
        for period in matrix["periods"]
        for category, spec in period["categories"].items()
    ]


def _cutoff(day: str) -> datetime:
    return datetime.combine(date.fromisoformat(day), time(9, 15), SHANGHAI)


def _result(security_id: str, day: str, state: str, regime: str,
            reasons: list[str], trace: list[str], rule: dict | None = None) -> dict:
    return {
        "security_id": security_id, "trading_date": day,
        "exchange": None, "board": None,
        "limit_regime": regime, "qualification_state": state,
        "reason_codes": reasons, "authority_trace": trace,
        "effective_from": rule["effective_from"] if rule else None,
        "effective_to": rule["effective_to"] if rule else None,
    }


def resolve(security_id: str, trading_date: str, facts: list[dict],
            rules: list[dict], official_source_ids: set[str]) -> dict:
    """Resolve pre-open D4 only. Each state fact must explicitly cover the queried day.

    `known_at` is a timezone-aware disclosure timestamp. `effective_at` and
    `first_applicable_trading_date` are separate: neither implies the other.
    A fact without official source or coverage is not an authoritative fact.
    """
    cutoff = _cutoff(trading_date)
    chosen: dict[str, dict] = {}
    reasons: list[str] = []
    trace: list[str] = []
    for field in (*REQUIRED, "risk_warning"):
        eligible = []
        for fact in facts:
            if fact.get("security_id") != security_id or fact.get("field") != field:
                continue
            if fact.get("source_id") not in official_source_ids:
                continue
            try:
                known = datetime.fromisoformat(fact["known_at"])
                effective = datetime.fromisoformat(fact["effective_at"])
                first = date.fromisoformat(fact["first_applicable_trading_date"])
                last = date.fromisoformat(fact["covered_until"])
                if known.tzinfo is None or effective.tzinfo is None:
                    continue
                if known <= cutoff and effective <= cutoff and first <= cutoff.date() <= last:
                    eligible.append(fact)
            except (KeyError, TypeError, ValueError):
                continue
        if not eligible:
            if field == "risk_warning":
                continue
            reasons.append("MISSING_" + field.upper())
            continue
        latest = max(x["first_applicable_trading_date"] for x in eligible)
        current = [x for x in eligible if x["first_applicable_trading_date"] == latest]
        if len({str(x["value"]) for x in current}) != 1:
            reasons.append("CONFLICT_" + field.upper())
            continue
        chosen[field] = current[0]
        trace.extend(sorted({x["source_id"] for x in current}))

    if any(x.startswith("CONFLICT_") for x in reasons):
        return _result(security_id, trading_date, "CONFLICT", "UNKNOWN", reasons,
                       sorted(set(trace)))
    if reasons:
        return _result(security_id, trading_date, "MISSING", "UNKNOWN", reasons,
                       sorted(set(trace)))

    value = {key: fact["value"] for key, fact in chosen.items()}
    exchange, board = value["exchange"], value["board"]
    if not security_id.endswith({"SSE": ".SH", "SZSE": ".SZ"}.get(exchange, "!")):
        reasons.append("CONFLICT_EXCHANGE_IDENTITY")
    if board not in {"SSE": ("MAIN", "STAR"), "SZSE": ("MAIN", "CHINEXT")}.get(exchange, ()):
        reasons.append("CONFLICT_BOARD_IDENTITY")
    if reasons:
        return _result(security_id, trading_date, "CONFLICT", "UNKNOWN", reasons,
                       sorted(set(trace)))
    if value["security_type"] != "A_SHARE" or value["special_status"] == "OTHER":
        return _result(security_id, trading_date, "INVALID", "UNKNOWN",
                       ["OUT_OF_SUPPORTED_RULE_SCOPE"], sorted(set(trace)))
    if type(value["listing_session"]) is not int or value["listing_session"] < 1:
        return _result(security_id, trading_date, "INVALID", "UNKNOWN",
                       ["INVALID_STATE_VALUE"], sorted(set(trace)))

    if value["listing_kind"] not in ("IPO", "RELIST"):
        return _result(security_id, trading_date, "INVALID", "UNKNOWN",
                       ["OUT_OF_SUPPORTED_LISTING_KIND"], sorted(set(trace)))

    special = value["special_status"]
    if special not in ("NONE", "RELIST_FIRST", "DELIST_FIRST", "DELIST_REST"):
        return _result(security_id, trading_date, "INVALID", "UNKNOWN",
                       ["OUT_OF_SUPPORTED_RULE_SCOPE"], sorted(set(trace)))
    if ((value["listing_kind"] == "RELIST" and value["listing_session"] == 1) !=
            (special == "RELIST_FIRST")):
        return _result(security_id, trading_date, "CONFLICT", "UNKNOWN",
                       ["CONFLICT_LISTING_LIFECYCLE"], sorted(set(trace)))
    if special in ("RELIST_FIRST", "DELIST_FIRST"):
        category = special
    elif special == "DELIST_REST":
        category = special
    elif value["listing_kind"] == "IPO" and value["listing_session"] <= 5:
        category = "IPO_FIRST_FIVE"
    elif "risk_warning" not in value:
        return _result(security_id, trading_date, "MISSING", "UNKNOWN",
                       ["MISSING_RISK_WARNING"], sorted(set(trace)))
    elif type(value["risk_warning"]) is not bool:
        return _result(security_id, trading_date, "INVALID", "UNKNOWN",
                       ["INVALID_STATE_VALUE"], sorted(set(trace)))
    elif value["risk_warning"]:
        category = "RISK_WARNING"
    else:
        category = "NORMAL"

    matching = []
    for rule in rules:
        if (rule["exchange"], rule["board"], rule["category"]) != (exchange, board, category):
            continue
        if rule["source_id"] not in official_source_ids:
            continue
        if not (rule["effective_from"] <= trading_date and
                (rule["effective_to"] is None or trading_date <= rule["effective_to"])):
            continue
        if datetime.fromisoformat(rule["known_at"]) > cutoff:
            continue
        matching.append(rule)
    if not matching:
        result = _result(security_id, trading_date, "MISSING", "UNKNOWN",
                         ["MISSING_RULE_AUTHORITY", "RULE_" + category], sorted(set(trace)))
    elif len({r["limit_regime"] for r in matching}) != 1 or len(matching) != 1:
        result = _result(security_id, trading_date, "CONFLICT", "UNKNOWN",
                         ["CONFLICT_RULE_SOURCES"], sorted(set(trace + [r["source_id"] for r in matching])))
    else:
        rule = matching[0]
        if rule["limit_regime"] not in REGIMES:
            result = _result(security_id, trading_date, "INVALID", "UNKNOWN",
                             ["OUT_OF_SUPPORTED_RULE_SCOPE"], sorted(set(trace)))
        else:
            result = _result(security_id, trading_date, "VALID", rule["limit_regime"],
                             [rule["reason_code"]], sorted(set(trace + [rule["source_id"]])), rule)
    result["exchange"], result["board"] = exchange, board
    return result
