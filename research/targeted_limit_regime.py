"""Conservative, dependency-date-only price-limit regime decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .trading_regime_rulebook import resolve_rule


@dataclass(frozen=True)
class RegimeDecision:
    status: str
    method: str
    reason: str | None
    rule_id: str | None = None
    source_id: str | None = None


def qualify_dependency(*, rulebook: dict, exchange: str, board: str,
                       security_id: str, day: date, listing_phase: str,
                       official_case: dict | None = None,
                       exception_evidence: dict | None = None) -> RegimeDecision:
    """Use official rule plus qualified day facts; never accept vendor labels.

    `exception_evidence` is an independent dated official day fact excluding
    first delisting-arrangement/relisting and other no-limit exceptions. V1's
    current master and vendor status do not supply that fact.
    """
    if board not in {"SSE Main", "SZSE Main", "ChiNext", "STAR"}:
        return RegimeDecision("UNRESOLVED", "NONE", "UNSUPPORTED_BOARD")
    rule_exchange = {"SH": "SSE", "SZ": "SZSE"}.get(exchange)
    rule_board = {"SSE Main": "MAIN", "SZSE Main": "MAIN",
                  "ChiNext": "CHINEXT", "STAR": "STAR"}[board]
    if rule_exchange is None:
        return RegimeDecision("UNRESOLVED", "NONE", "UNQUALIFIED_EXCHANGE")
    if listing_phase.startswith("IPO_DAY_"):
        rule = resolve_rule(rulebook, rule_exchange, rule_board, day,
                            listing_phase, "NORMAL")
        if rule["has_price_limit"] is not False:
            return RegimeDecision("UNRESOLVED", "NONE", "IPO_RULE_CONFLICT")
        return RegimeDecision("REJECTED_NON_10PCT", "DIRECT_OFFICIAL_RULE_IPO",
                              None, rule["rule_id"])
    if listing_phase != "NORMAL_LISTED":
        return RegimeDecision("UNRESOLVED", "NONE", "UNQUALIFIED_LISTING_PHASE")
    if board in {"ChiNext", "STAR"}:
        rule = resolve_rule(rulebook, rule_exchange, rule_board, day,
                            listing_phase, "NORMAL")
        return RegimeDecision("REJECTED_NON_10PCT", "DIRECT_OFFICIAL_BOARD_RULE",
                              None, rule["rule_id"])
    status, method, source_id = None, None, None
    exception_excluded = False
    if official_case is not None:
        if (official_case["security_id"] != security_id
                or official_case["effective_session"] != day.isoformat()
                or official_case["source_publish_date"] >= day.isoformat()
                or official_case["transition"] not in {
                    "NORMAL_TO_RISK_WARNING_REGIME", "RISK_WARNING_TO_NORMAL_REGIME"
                }
                or not official_case.get("source_document_id")
                or not official_case.get("source_url", "").startswith((
                    "https://static.cninfo.com.cn/", "https://disc.static.szse.cn/",
                    "https://www.sse.com.cn/"
                ))):
            return RegimeDecision("UNRESOLVED", "NONE", "OFFICIAL_CASE_NOT_PIT_APPLICABLE")
        status = ("RISK_WARNING" if official_case["transition"] ==
                  "NORMAL_TO_RISK_WARNING_REGIME" else "NORMAL")
        method = "OFFICIAL_EFFECTIVE_DAY"
        source_id = official_case["source_document_id"]
        exception_excluded = True
    elif exception_evidence is not None:
        if (exception_evidence.get("security_id") != security_id
                or exception_evidence.get("trade_date") != day.isoformat()
                or exception_evidence.get("source_publish_date", "9999") >= day.isoformat()
                or exception_evidence.get("no_special_exception") is not True
                or not exception_evidence.get("source_document_id")
                or not exception_evidence.get("source_url", "").startswith((
                    "https://www.sse.com.cn/", "https://www.szse.cn/"
                ))):
            return RegimeDecision("UNRESOLVED", "NONE", "EXCEPTION_EVIDENCE_INVALID")
        exception_excluded = True
    if not exception_excluded:
        reason = ("HISTORICAL_RISK_STATE_AND_EXCEPTION_UNQUALIFIED"
                  if day < date(2026, 7, 6) else "SPECIAL_EXCEPTION_NOT_EXCLUDED")
        return RegimeDecision("UNRESOLVED", "NONE", reason)
    if status is None:
        # After 2026-07-06, both ordinary and risk-warning main-board
        # normal-listing rules have 10%, but an independent exception check
        # remains mandatory. Before then, the status must also be qualified.
        if day < date(2026, 7, 6):
            return RegimeDecision("UNRESOLVED", "NONE", "HISTORICAL_RISK_STATE_UNQUALIFIED")
        status, method = "NORMAL", "DIRECT_OFFICIAL_POST_2026_RULE"
    rule = resolve_rule(rulebook, rule_exchange, rule_board, day, listing_phase, status)
    rate = rule["price_limit_up_rate"]
    if official_case is not None and rate != official_case["official_rate_after"]:
        return RegimeDecision("UNRESOLVED", "NONE", "OFFICIAL_CASE_RULE_CONFLICT")
    return RegimeDecision(
        "QUALIFIED_10PCT" if rate == "0.10" else "REJECTED_NON_10PCT",
        method, None, rule["rule_id"], source_id,
    )


def candidate_disposition(dependencies: list[RegimeDecision]) -> str:
    if not dependencies:
        raise ValueError("A candidate must have dependency dates")
    if any(row.status == "REJECTED_NON_10PCT" for row in dependencies):
        return "REJECTED_NON_10PCT"
    if any(row.status == "UNRESOLVED" for row in dependencies):
        return "UNRESOLVED"
    return "QUALIFIED_10PCT"
