from decimal import Decimal
import csv
from pathlib import Path
import json

import pytest

from research.exit_pnl_v1 import Session, buy_fill, simulate
from scripts.run_exit_and_pnl_v1 import _contracts, _session


T = "2026-09-01"
COSTS = {"buy_commission_bps": "3", "buy_transfer_bps": "0.1",
         "sell_commission_bps": "3", "sell_transfer_bps": "0.1",
         "sell_stamp_duty_bps": "5"}


def bar(day, op="10.00", hi="10.00", lo="10.00", close="10.00", **kw):
    return Session(day, "TRADING", *(Decimal(x) for x in (op, hi, lo, close)), **kw)


def contract(h=5, tp=None, sl=None):
    return {"timeout_exchange_sessions": h, "take_profit": tp, "stop_loss": sl}


def run(sessions, *, h=5, tp=None, sl=None):
    return simulate(entry_date=T, fill_price=Decimal("10.00"), sessions=sessions,
                    contract=contract(h, tp, sl), costs=COSTS)


def test_t_plus_one_and_time_exits():
    path = [bar("2026-09-02", close="10.10", hi="10.10"),
            bar("2026-09-03", close="10.20", hi="10.20"),
            bar("2026-09-04", close="10.30", hi="10.30"),
            bar("2026-09-07"), bar("2026-09-08", close="10.50", hi="10.50")]
    for h, expected in [(1, "10.10"), (3, "10.30"), (5, "10.50")]:
        result = run(path, h=h)
        assert result["exit_date"] == path[h - 1].day
        assert result["exit_price_raw"] == float(expected)
        assert result["exit_reason"] == "TIMEOUT_CLOSE"
        assert result["holding_sessions"] == h
        assert len(result["decision_trace"]) == h
    with pytest.raises(ValueError, match="strictly after"):
        run([bar(T)])


def test_tp_sl_timeout_and_exit_bound_excursions():
    tp = run([bar("2026-09-02", hi="10.60")], tp="0.05")
    assert tp["exit_reason"] == "TP_INTRADAY"
    assert tp["exit_price_raw"] == 10.5
    assert tp["pre_exit_mfe"] == pytest.approx(.05)  # not later 10.60
    stop = run([bar("2026-09-02", lo="9.40")], tp="0.05", sl="0.05")
    assert stop["exit_reason"] == "SL_INTRADAY"
    assert stop["exit_price_raw"] == 9.5
    assert stop["pre_exit_mae"] == pytest.approx(-.05)
    timeout = run([bar("2026-09-02"), bar("2026-09-03"),
                   bar("2026-09-04"), bar("2026-09-07"), bar("2026-09-08")],
                  tp="0.10", sl="0.10")
    assert timeout["exit_reason"] == "TIMEOUT_CLOSE"


def test_gaps_and_same_bar_stop_first():
    up = run([bar("2026-09-02", "10.70", "10.80", "10.60", "10.70")], tp="0.05")
    down = run([bar("2026-09-02", "9.20", "9.30", "9.10", "9.20")], sl="0.05")
    both = run([bar("2026-09-02", hi="10.60", lo="9.40")], tp="0.05", sl="0.05")
    assert (up["exit_reason"], up["exit_price_raw"]) == ("TP_GAP_OPEN", 10.7)
    assert (down["exit_reason"], down["exit_price_raw"]) == ("SL_GAP_OPEN", 9.2)
    assert (both["exit_reason"], both["exit_price_raw"], both["same_bar_ambiguity"]) == (
        "SAME_BAR_AMBIGUOUS_STOP_FIRST", 9.5, True)


def test_suspended_timeout_defers_to_next_open_and_no_forward_fill():
    path = [Session("2026-09-02", "SUSPENDED"),
            bar("2026-09-03", "9.80", "10.10", "9.70", "10.00")]
    result = run(path, h=1)
    assert result["exit_reason"] == "DEFERRED_TIMEOUT_OPEN"
    assert result["exit_price_raw"] == 9.8
    assert result["holding_sessions"] == 2
    assert result["suspended_session_count"] == 1
    assert result["decision_trace"][0]["decision"] == "TIME_EXIT_BLOCKED_SUSPENDED"


def test_one_price_limit_down_carries_stop_and_timeout():
    locked = bar("2026-09-02", "9.00", "9.00", "9.00", "9.00",
                 lower_limit=Decimal("9.00"))
    reopen = bar("2026-09-03", "8.50", "9.00", "8.40", "8.80")
    stop = run([locked, reopen], sl="0.05")
    time = run([locked, reopen], h=1)
    assert stop["exit_reason"] == "DEFERRED_LIMIT_DOWN_OPEN"
    assert stop["exit_price_raw"] == 8.5
    assert stop["limit_down_blocked_count"] == 1
    assert time["exit_reason"] == "DEFERRED_TIMEOUT_OPEN"
    assert time["exit_price_raw"] == 8.5


def test_d8_price_return_exclusion_and_unresolved_path():
    action = bar("2026-09-02", "5.50", "5.60", "5.40", "5.50",
                 d8_single_factor=Decimal("2"), d8_event_kind="STOCK_DIVIDEND")
    result = run([action], h=1)
    assert result["exit_price_comparable"] == 11.0
    assert result["gross_price_return"] == pytest.approx(.1)
    assert result["transaction_cost"] == pytest.approx(.00031 + 1.1 * .00081)
    assert result["net_price_return"] == pytest.approx(.1 - .00031 - 1.1 * .00081)
    excluded = run([Session("2026-09-02", "TRADING", Decimal("10"), Decimal("10"),
                            Decimal("10"), Decimal("10"),
                            d8_exclusion_reason="UNQUALIFIED_CASH_EVENT")], h=1)
    assert excluded["outcome_status"] == "ADJUSTMENT_UNRESOLVED"
    assert excluded["net_price_return"] is None


def test_unknown_and_terminal_are_not_imputed():
    assert run([Session("2026-09-02", "UNKNOWN")], h=1)["outcome_status"] == "PATH_UNRESOLVED"
    assert run([Session("2026-09-02", "TERMINAL")], h=1)["outcome_status"] == "TERMINAL_UNRESOLVED"


def test_fill_slippage_and_no_lookahead():
    assert buy_fill(Decimal("10.00")) == Decimal("10.00")
    assert buy_fill(Decimal("10.00"), fixed_bps=Decimal(10)) == Decimal("10.01")
    assert buy_fill(Decimal("10.00"), fixed_ticks=2) == Decimal("10.02")
    first = bar("2026-09-02", hi="10.60")
    a = run([first, bar("2026-09-03", "9", "9", "9", "9")], tp="0.05")
    b = run([first, bar("2026-09-03", "20", "20", "20", "20")], tp="0.05")
    assert a == b


def test_d8_pit_date_and_unattributed_flat_fall_fail_closed():
    key = ("TEST.SH", "2026-09-02")
    bars = {key: {"open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0}}
    status = {key: {"is_susp_sec": False, "preclose": 10.0, "low_limited": None}}
    assert _session("TEST.SH", key[1], bars, status, {}, {}, None).status == "UNKNOWN"
    status[key]["low_limited"] = 9.0
    same_day = {key: {"known_date": key[1], "source_event_hash": "x" * 64,
                      "single_factor": 2, "event_kind": "DIVIDEND"}}
    path = _session("TEST.SH", key[1], bars, status, same_day, {}, None)
    assert run([path], h=1)["outcome_status"] == "ADJUSTMENT_UNRESOLVED"
    assert path.d8_exclusion_reason == "D8_PIT_OR_SOURCE_UNQUALIFIED"


def test_frozen_registry_public_population_and_board_separation():
    _, registry, _ = _contracts()
    assert registry["T_PLUS_ONE_EXIT_ONLY"] is True
    assert len(registry["exit_contracts"]) == 8
    base = Path(__file__).resolve().parents[1] / "artifacts/stage1_exit_and_pnl_v1"
    population = list(csv.DictReader((base / "execution_population_summary.csv").open(encoding="utf-8")))
    assert [(r["layer"], int(r["entry_n"])) for r in population] == [
        ("SIGNAL_OUTCOME", 505),
        ("EXECUTABLE_PNL_BASELINE_RESEARCH_ASSUMPTION", 242),
        ("CONDITIONAL_ON_FILL_ONLY", 263)]
    assert population[2]["board_fillability"] == "BOARD_FILLABILITY_UNQUALIFIED"
    summary = list(csv.DictReader((base / "exit_contract_summary.csv").open(encoding="utf-8")))
    assert len(summary) == 16
    assert all(int(r["n_total"]) in {242, 263} for r in summary)
    receipt = json.loads((base / "exit_pnl_quality_receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "PASS"
    assert receipt["board_unconditional_trade_rows"] == 0
