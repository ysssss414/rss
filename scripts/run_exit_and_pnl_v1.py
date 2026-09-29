"""Frozen-Entry, sequential exit simulation; public aggregates, private trade rows."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import platform
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb
import pandas as pd

from research.entry_event_outcome_v1 import event_id
from research.exit_pnl_v1 import Session, buy_fill, money, on_tick, simulate
from research.snapshot import FrozenResearchSnapshot
from scripts.run_entry_event_study_v1 import (_date, _records, digest, distribution,
                                               json_bytes, validate_population, write_csv, write_json)


SMOKE = ROOT / "artifacts/stage1_real_strategy_smoke_v1"
EVENT_STUDY = ROOT / "artifacts/stage1_entry_event_study_v1"
EVENT_PRIVATE = ROOT / ".local_research_data/stage1_entry_event_study_v1"
SNAPSHOT = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
PUBLIC = ROOT / "artifacts/stage1_exit_and_pnl_v1"
PRIVATE = ROOT / ".local_research_data/stage1_exit_and_pnl_v1"
ENTRY_SHA = "82d16ddf0f3c1af3c5015dd34ae08b9bbe83e23ed90c03b2c8b7ade7ec4bb0f1"
SNAPSHOT_SHA = "a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67"
EVENT_MANIFEST_SHA = "c6afdbaf7575fce8526d4c56725ae8c9caba3fc57218a05b2c40bfe59c962d33"
CUTOFF = "2026-09-23"
CONTRACTS = ("execution_contract_v1.json", "exit_contract_registry_v1.json",
             "transaction_cost_contract_v1.json")
EXIT_IDS = ("TIME_H1", "TIME_H3", "TIME_H5", "TP5_H5", "TP10_H5",
            "TP5_SL5_H5", "TP10_SL10_H5", "TP10_SL5_H5")


def _contracts() -> tuple[dict, dict, dict]:
    execution, registry, costs = [json.loads((PUBLIC / p).read_text(encoding="utf-8"))
                                  for p in CONTRACTS]
    if (execution["id"] != "ENTRY_EXECUTION_CONTRACT_V1"
            or execution["formal_entry_population"] != 505
            or execution["normal_close_intent"]["population"] != 242
            or execution["limit_up_close_board_intent"]["population"] != 263
            or execution["limit_up_close_board_intent"]["unconditional_executable_population"] != 0
            or execution["limit_up_close_board_intent"]["board_fillability"] != "BOARD_FILLABILITY_UNQUALIFIED"
            or registry["id"] != "EXIT_CONTRACT_REGISTRY_V1"
            or registry["T_PLUS_ONE_EXIT_ONLY"] is not True
            or [x["id"] for x in registry["exit_contracts"]] != list(EXIT_IDS)
            or registry["same_bar_dual_hit"] != "SAME_BAR_DUAL_HIT_V1_STOP_FIRST"
            or costs["id"] != "TRANSACTION_COST_CONTRACT_V1"
            or costs["cash_dividend_income"] != "EXCLUDED_PRICE_RETURN_ONLY"
            or [costs[x] for x in ("buy_commission_bps", "sell_commission_bps",
                                    "buy_transfer_bps", "sell_transfer_bps", "sell_stamp_duty_bps")]
            != ["3.0", "3.0", "0.1", "0.1", "5.0"]):
        raise ValueError("BLOCKED_EXIT_CONTRACT_MISMATCH")
    return execution, registry, costs


def _validate_parent() -> dict:
    if digest(EVENT_STUDY / "run_manifest.json") != EVENT_MANIFEST_SHA:
        raise ValueError("BLOCKED_EXIT_SIGNAL_CONTAMINATION: event study manifest")
    manifest = json.loads((EVENT_STUDY / "run_manifest.json").read_text(encoding="utf-8"))
    if (manifest["run_id"] != "ENTRY_EVENT_STUDY_RUN_V1"
            or manifest["entry_population_sha256"] != ENTRY_SHA
            or manifest["entry_count"] != 505 or manifest["cutoff"] != CUTOFF
            or manifest["snapshot_manifest_sha256"] != SNAPSHOT_SHA):
        raise ValueError("BLOCKED_EXIT_SIGNAL_CONTAMINATION: event study identity")
    for name, expected in manifest["public_artifacts"].items():
        if digest(EVENT_STUDY / name) != expected:
            raise ValueError(f"BLOCKED_EXIT_SIGNAL_CONTAMINATION: parent public {name}")
    for name, expected in manifest["private_artifacts"].items():
        if digest(EVENT_PRIVATE / name) != expected:
            raise ValueError(f"BLOCKED_EXIT_SIGNAL_CONTAMINATION: parent private {name}")
    for name, expected in manifest["code_sha256"].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f"BLOCKED_EXIT_SIGNAL_CONTAMINATION: parent code {name}")
    return manifest


def _price(value: object) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    try:
        price = money(value)
    except (ValueError, ArithmeticError):
        return None
    return price if on_tick(price) else None


def _session(code: str, day: str, bars: dict, statuses: dict, actions: dict,
             exclusions: dict, delisted: str | None) -> Session:
    action = actions.get((code, day))
    exclusion = exclusions.get((code, day))
    factor = Decimal(1)
    kind = None
    if action:
        if (action["known_date"] >= day or len(str(action["source_event_hash"])) != 64):
            exclusion = "D8_PIT_OR_SOURCE_UNQUALIFIED"
        else:
            factor = money(action["single_factor"])
            kind = str(action["event_kind"])
    kwargs = {"d8_single_factor": factor,
              "d8_exclusion_reason": exclusion, "d8_event_kind": kind}
    if delisted and day >= delisted:
        return Session(day, "TERMINAL", **kwargs)
    state = statuses.get((code, day))
    if state is None:
        return Session(day, "UNKNOWN", **kwargs)
    if state["is_susp_sec"] is True:
        return Session(day, "SUSPENDED", **kwargs)
    if state["is_susp_sec"] is not False:
        return Session(day, "UNKNOWN", **kwargs)
    bar = bars.get((code, day))
    prices = [_price(bar.get(k)) for k in ("open", "high", "low", "close")] if bar else []
    if not prices or any(p is None for p in prices):
        return Session(day, "UNKNOWN", **kwargs)
    op, high, low, close = prices
    if not (low <= op <= high and low <= close <= high):
        return Session(day, "UNKNOWN", **kwargs)
    lower_limit = _price(state["low_limited"])
    # A flat falling bar without an attributable lower bound is not a safe sell fill.
    preclose = _price(state["preclose"])
    if op == high == low == close and preclose and op < preclose and lower_limit != op:
        return Session(day, "UNKNOWN", **kwargs)
    return Session(day, "TRADING", op, high, low, close,
                   lower_limit=lower_limit, **kwargs)


def _summary(rows: list[dict]) -> dict:
    closed = [r for r in rows if r["outcome_status"] == "CLOSED"]
    gross = [r["gross_price_return"] for r in closed]
    net = [r["net_price_return"] for r in closed]
    reasons = Counter(r["exit_reason"] for r in closed)
    statuses = Counter(r["outcome_status"] for r in rows)
    return {"n_total": len(rows), "n_closed": len(closed),
            "n_open_censored": statuses["OPEN_AT_SNAPSHOT_CUTOFF"],
            "n_adjustment_unresolved": statuses["ADJUSTMENT_UNRESOLVED"],
            "n_path_unresolved": statuses["PATH_UNRESOLVED"],
            "n_terminal_unresolved": statuses["TERMINAL_UNRESOLVED"],
            **{f"gross_{k}": v for k, v in distribution(gross).items()},
            **{f"net_{k}": v for k, v in distribution(net).items()},
            "net_positive_fraction": sum(x > 0 for x in net) / len(net) if net else None,
            "net_zero_fraction": sum(x == 0 for x in net) / len(net) if net else None,
            "net_negative_fraction": sum(x < 0 for x in net) / len(net) if net else None,
            "holding_mean": statistics.fmean(r["holding_sessions"] for r in closed) if closed else None,
            "holding_median": statistics.median(r["holding_sessions"] for r in closed) if closed else None,
            "tp_exits": sum(n for reason, n in reasons.items() if reason.startswith("TP_")),
            "sl_exits": sum(n for reason, n in reasons.items() if reason.startswith("SL_")
                            or "STOP_FIRST" in reason or reason == "DEFERRED_LIMIT_DOWN_OPEN"),
            "timeout_exits": sum(n for reason, n in reasons.items() if "TIMEOUT" in reason),
            "gap_exits": sum(r["gap_handling"] is not None for r in closed),
            "same_bar_exits": sum(r["same_bar_ambiguity"] for r in closed),
            "limit_down_blocked_events": sum(r["limit_down_blocked_count"] > 0 for r in rows),
            "suspended_events": sum(r["suspended_session_count"] > 0 for r in rows),
            "small_sample_flag": "SMALL_SAMPLE_DESCRIPTIVE_ONLY" if len(rows) < 20 else ""}


def _case_audit(rows: list[dict], traces: dict[tuple[str, str], list]) -> dict:
    ordered = sorted(rows, key=lambda r: (r["entry_event_id"], r["exit_contract_id"]))
    filters = {
        "normal_close_fill": lambda r: r["execution_intent"] == "NORMAL_CLOSE_INTENT",
        "board_intent_conditional_fill": lambda r: r["conditional_on_fill"],
        "t_plus_one_tp": lambda r: r["holding_sessions"] == 1 and r["exit_reason"] in {"TP_INTRADAY", "TP_GAP_OPEN"},
        "t_plus_one_sl": lambda r: r["holding_sessions"] == 1 and r["exit_reason"] in {"SL_INTRADAY", "SL_GAP_OPEN", "SAME_BAR_AMBIGUOUS_STOP_FIRST"},
        "gap_up_tp": lambda r: r["exit_reason"] == "TP_GAP_OPEN",
        "gap_down_sl": lambda r: r["exit_reason"] == "SL_GAP_OPEN",
        "same_bar_dual_hit": lambda r: r["same_bar_ambiguity"],
        "suspended_timeout": lambda r: any(x["decision"] == "TIME_EXIT_BLOCKED_SUSPENDED" for x in traces[(r["entry_event_id"], r["exit_contract_id"])]),
        "limit_down_blocked_stop": lambda r: any(x["decision"] == "EXIT_BLOCKED_BY_LIMIT_DOWN" for x in traces[(r["entry_event_id"], r["exit_contract_id"])]) and r["exit_reason"] == "DEFERRED_LIMIT_DOWN_OPEN",
        "timeout_exit": lambda r: r["exit_reason"] == "TIMEOUT_CLOSE",
        "corporate_action_holding": lambda r: any(x["d8_event_kind"] for x in traces[(r["entry_event_id"], r["exit_contract_id"])]),
        "entry_a": lambda r: r["entry_type"] == "ENTRY_A_PULLBACK_TO_MA5_V1",
        "entry_b": lambda r: r["entry_type"] == "ENTRY_B_RSI_RECROSS_70_V1",
    }
    cases = {}
    for name, predicate in filters.items():
        found = next((r for r in ordered if predicate(r)), None)
        cases[name] = ({"status": "OBSERVED", "trade": found,
                        "decision_trace": traces[(found["entry_event_id"], found["exit_contract_id"])]}
                       if found else {"status": "NOT_OBSERVED_IN_FROZEN_POPULATION"})
    return {"selection": "first sorted event/exit identity per category; no outcome ranking",
            "cases": cases}


def _quality(rows: list[dict]) -> dict:
    keys = [(r["entry_event_id"], r["exit_contract_id"]) for r in rows]
    failures = []
    if len(rows) != 505 * 8 or len(set(keys)) != len(rows):
        failures.append("TRADE_GRAIN")
    if Counter(r["population_layer"] for r in rows) != {
            "EXECUTABLE_PNL_BASELINE_RESEARCH_ASSUMPTION": 242 * 8,
            "CONDITIONAL_ON_FILL_ONLY": 263 * 8}:
        failures.append("POPULATION_LAYER")
    for r in rows:
        if (r["fill_date"] != r["entry_date"] or r["fill_price"] <= 0
                or r["exit_date"] is not None and not r["entry_date"] < r["exit_date"] <= CUTOFF
                or r["conditional_on_fill"] != (r["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT")
                or r["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT" and r["population_layer"] != "CONDITIONAL_ON_FILL_ONLY"):
            failures.append("T_PLUS_ONE_FILL_OR_LAYER")
        closed = r["outcome_status"] == "CLOSED"
        if closed != (r["gross_price_return"] is not None and r["net_price_return"] is not None
                      and r["exit_date"] is not None):
            failures.append("PNL_STATUS")
        if closed and (r["transaction_cost"] < 0 or r["pre_exit_mfe"] < r["gross_price_return"] - 1e-12
                       or r["pre_exit_mae"] > r["gross_price_return"] + 1e-12):
            failures.append("COST_OR_EXCURSION")
    return {"status": "PASS" if not failures else "FAIL", "failures": sorted(set(failures)),
            "trade_rows": len(rows), "unique_event_exit_keys": len(set(keys)),
            "entry_events": len(set(r["entry_event_id"] for r in rows)),
            "board_unconditional_trade_rows": sum(r["conditional_on_fill"] and
                                                    r["population_layer"] != "CONDITIONAL_ON_FILL_ONLY" for r in rows),
            "signal_firewall": "frozen Entry CSV only; outcome and D8 read after Entry; no Entry module invoked"}


def main(*, focused_tests: str = "NOT_RUN", full_suite: str = "NOT_RUN") -> dict:
    smoke, entries, observations = validate_population()
    parent = _validate_parent()
    execution, registry, costs = _contracts()
    with FrozenResearchSnapshot(SNAPSHOT) as snapshot:
        if (snapshot.validation["manifest_sha256"] != SNAPSHOT_SHA
                or snapshot.manifest["cutoff_date"] != CUTOFF
                or snapshot.manifest["pit_policy"]["d8_contract"] != "ENTRY_COMPARABLE_FORWARD_PATH_V1"):
            raise ValueError("BLOCKED_EXIT_PNL_COMPARABILITY")
        codes = sorted({e["security_id"] for e in entries})
        calendar = tuple(_date(x) for x in snapshot.load_calendar().trade_date)
        bars = _records(snapshot.load_daily_bars(codes), "trade_date")
        statuses = _records(snapshot.load_daily_status(codes), "trade_date")
        actions = _records(snapshot.load_d8(codes), "effective_date")
        universe = {str(x["security_id"]): x for x in snapshot.load_universe(codes).to_dict("records")}
        files = [str(snapshot.root / x["path"]) for x in
                 snapshot.manifest["datasets"]["outcome_adjustment_exclusions"]["files"]]
        excluded = snapshot.con.execute(
            "SELECT security_id,effective_date,reason FROM read_parquet(?) "
            "WHERE security_id IN (SELECT UNNEST(?)) ORDER BY security_id,effective_date",
            [files, codes]).df()
        exclusions = {(str(x["security_id"]), _date(x["effective_date"])): str(x["reason"])
                      for x in excluded.to_dict("records")}
        actions = {key: {**action, "known_date": _date(action["known_date"])}
                   for key, action in actions.items()}
        counts = Counter(x["security_id"] for x in entries)
        rows, traces = [], {}
        for entry in sorted(entries, key=lambda e: (e["security_id"], e["entry_date"], e["observation_instance_id"])):
            code, day = entry["security_id"], entry["entry_date"]
            anchor = bars.get((code, day))
            if anchor is None or str(anchor["close"]) != entry["raw_close"]:
                raise ValueError("BLOCKED_EXIT_SIGNAL_CONTAMINATION: D3 anchor")
            source = observations[(code, entry["observation_instance_id"])]
            delisted = universe[code]["delisting_date"]
            delisted = None if pd.isna(delisted) else _date(delisted)
            future = [_session(code, d, bars, statuses, actions, exclusions, delisted)
                      for d in calendar if day < d <= CUTOFF]
            fill = buy_fill(money(entry["raw_close"]))
            board = entry["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT"
            if not board and entry["execution_intent"] != "NORMAL_CLOSE_INTENT":
                raise ValueError("BLOCKED_BOARD_FILL_ASSUMPTION: unknown intent")
            layer = "CONDITIONAL_ON_FILL_ONLY" if board else "EXECUTABLE_PNL_BASELINE_RESEARCH_ASSUMPTION"
            fill_contract = (execution["limit_up_close_board_intent"]["contract"] if board
                             else execution["normal_close_intent"]["contract"])
            identity = event_id(code, day, entry["observation_instance_id"])
            for contract in registry["exit_contracts"]:
                result = simulate(entry_date=day, fill_price=fill, sessions=future,
                                  contract=contract, costs=costs)
                trace = result.pop("decision_trace")
                record = {"entry_event_id": identity, "security_id": code, "entry_date": day,
                          "observation_instance_id": entry["observation_instance_id"],
                          "entry_type": entry["entry_reasons"], "execution_intent": entry["execution_intent"],
                          "trigger_qualification_method": source["trigger_regime_qualification_method"],
                          "observation_pattern": f'{source["v3_lookback_hits"]}/5',
                          "rsi_provenance": source["rsi_replay_mode"],
                          "repeated_event_flag": counts[code] > 1,
                          "exit_contract_id": contract["id"], "population_layer": layer,
                          "fill_status": "ASSUMED_CONDITIONAL_FILL" if board else "SIMULATED_CLOSE_FILL",
                          "fill_date": day, "signal_close": float(money(entry["raw_close"])),
                          "fill_price": float(fill), "fill_contract": fill_contract,
                          "conditional_on_fill": board, "snapshot_id": snapshot.snapshot_id,
                          "smoke_run_id": smoke["run_id"], "entry_population_sha256": ENTRY_SHA,
                          "exit_contract_version": registry["id"], **result}
                rows.append(record)
                traces[(identity, contract["id"])] = trace
    if digest(SMOKE / "entries.csv") != ENTRY_SHA:
        raise ValueError("BLOCKED_EXIT_SIGNAL_CONTAMINATION: frozen CSV mutated during run")
    quality = _quality(rows)
    exposed = [r for r in rows if any(x["d8_event_kind"] for x in
                                    traces[(r["entry_event_id"], r["exit_contract_id"])])]
    quality["d8_exposed_trade_rows"] = len(exposed)
    quality["d8_exposed_entry_events"] = len({r["entry_event_id"] for r in exposed})
    quality["d8_event_kind_in_traces"] = dict(sorted(Counter(
        x["d8_event_kind"] for trace in traces.values() for x in trace
        if x["d8_event_kind"]).items()))
    quality["cash_income_scope"] = "NOT_MODELLED; D8 price-scale exposure is not account-level total return"
    if quality["status"] != "PASS":
        raise ValueError(f"BLOCKED_EXIT_PNL_QUALITY: {quality['failures']}")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    parquet = PRIVATE / "entry_trade_pnl_v1.parquet"
    code_sha = {name: digest(ROOT / name) for name in (
        "research/exit_pnl_v1.py", "scripts/run_exit_and_pnl_v1.py")}
    old = json.loads((PUBLIC / "run_manifest.json").read_text(encoding="utf-8")) if (PUBLIC / "run_manifest.json").exists() else {}
    prior = digest(parquet) if parquet.exists() and old.get("code_sha256") == code_sha else None
    con = duckdb.connect()
    try:
        con.register("trades", pd.DataFrame(rows))
        con.execute("COPY (SELECT * FROM trades) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(parquet)])
    finally:
        con.close()
    if prior is not None and digest(parquet) != prior:
        raise ValueError("BLOCKED_EXIT_PNL_NONDETERMINISTIC: private parquet hash")
    layers = (("EXECUTABLE_PNL_BASELINE_RESEARCH_ASSUMPTION", "NORMAL_CLOSE_INTENT"),
              ("CONDITIONAL_ON_FILL_ONLY", "LIMIT_UP_CLOSE_BOARD_INTENT"))
    summary, reason_rows, efficiency, subgroup = [], [], [], []
    for layer, intent in layers:
        for exit_id in EXIT_IDS:
            selected = [r for r in rows if r["population_layer"] == layer and r["exit_contract_id"] == exit_id]
            summary.append({"population_layer": layer, "execution_intent": intent,
                            "exit_contract_id": exit_id, **_summary(selected)})
            for reason, n in sorted(Counter(r["exit_reason"] or r["outcome_status"] for r in selected).items()):
                reason_rows.append({"population_layer": layer, "exit_contract_id": exit_id,
                                    "reason_or_status": reason, "n": n})
            closed = [r for r in selected if r["outcome_status"] == "CLOSED"]
            efficiency.append({"population_layer": layer, "exit_contract_id": exit_id,
                               "n_closed": len(closed),
                               **{f"mfe_{k}": v for k, v in distribution([r["pre_exit_mfe"] for r in closed]).items()},
                               **{f"mae_{k}": v for k, v in distribution([r["pre_exit_mae"] for r in closed]).items()},
                               **{f"capture_{k}": v for k, v in distribution([r["mfe_capture_ratio"] for r in closed
                                                                               if r["mfe_capture_ratio"] is not None]).items()},
                               "excursion_basis_counts": json.dumps(dict(sorted(Counter(r["pre_exit_excursion_basis"]
                                                                              for r in closed).items())), sort_keys=True)})
            for dimension, values in (
                    ("ENTRY_TYPE", ("ENTRY_A_PULLBACK_TO_MA5_V1", "ENTRY_B_RSI_RECROSS_70_V1")),
                    ("TRIGGER_QUALIFICATION", ("QUALIFIED_BY_V3_PRICE_PATH", "QUALIFIED_BY_OPERATIONAL_STATUS"))):
                field = "entry_type" if dimension == "ENTRY_TYPE" else "trigger_qualification_method"
                for value in values:
                    group = [r for r in selected if r[field] == value]
                    subgroup.append({"population_layer": layer, "execution_intent": intent,
                                     "exit_contract_id": exit_id, "dimension": dimension,
                                     "value": value, **_summary(group)})
    for layer, intent in layers:
        for exit_id in EXIT_IDS:
            if sum(r["n_total"] for r in subgroup if r["population_layer"] == layer
                   and r["exit_contract_id"] == exit_id and r["dimension"] == "ENTRY_TYPE") != (263 if intent.startswith("LIMIT") else 242):
                raise ValueError("BLOCKED_EXIT_PNL_QUALITY: Entry subgroup coverage")
    write_csv(PUBLIC / "exit_contract_summary.csv", summary, list(summary[0]))
    write_csv(PUBLIC / "exit_reason_summary.csv", reason_rows, list(reason_rows[0]))
    write_csv(PUBLIC / "exit_efficiency_summary.csv", efficiency, list(efficiency[0]))
    write_csv(PUBLIC / "exit_pnl_subgroup_summary.csv", subgroup, list(subgroup[0]))
    populations = [
        {"layer": "SIGNAL_OUTCOME", "execution_intent": "ALL_FORMAL_ENTRIES", "entry_n": 505,
         "trade_rows_per_contract": None, "fill_claim": "NO_FILL_SIGNAL_EVENT_STUDY", "board_fillability": "NOT_APPLICABLE"},
        {"layer": "EXECUTABLE_PNL_BASELINE_RESEARCH_ASSUMPTION", "execution_intent": "NORMAL_CLOSE_INTENT",
         "entry_n": 242, "trade_rows_per_contract": 242,
         "fill_claim": "SIMULATED_CLOSE_FILL_NOT_HISTORICAL_FILL", "board_fillability": "NOT_APPLICABLE"},
        {"layer": "CONDITIONAL_ON_FILL_ONLY", "execution_intent": "LIMIT_UP_CLOSE_BOARD_INTENT",
         "entry_n": 263, "trade_rows_per_contract": 263,
         "fill_claim": "IF_FILLED_AT_SIGNAL_CLOSE_ONLY", "board_fillability": "BOARD_FILLABILITY_UNQUALIFIED"},
    ]
    write_csv(PUBLIC / "execution_population_summary.csv", populations, list(populations[0]))
    write_json(PUBLIC / "exit_pnl_quality_receipt.json", quality)
    write_json(PUBLIC / "exit_pnl_case_audit.json", _case_audit(rows, traces))
    semantic_sha = sha256(json_bytes({"rows": rows, "traces": [traces[(r["entry_event_id"], r["exit_contract_id"])]
                                                               for r in rows]})).hexdigest()
    write_json(PUBLIC / "exit_pnl_reproducibility.json", {
        "status": "PASS" if prior is None or prior == digest(parquet) else "FAIL",
        "semantic_rows_and_trace_sha256": semantic_sha,
        "private_parquet_sha256": digest(parquet),
        "determinism": "sorted frozen Entry and fixed registry; rerun rejects a changed private parquet hash; public hashes checked by replay"})
    public = sorted(p for p in PUBLIC.iterdir() if p.is_file() and p.name != "run_manifest.json")
    manifest = {"run_id": "EXIT_AND_PNL_RUN_V1", "status": "MATERIALIZED",
                "parent_run_id": parent["run_id"], "parent_manifest_sha256": EVENT_MANIFEST_SHA,
                "snapshot_id": snapshot.snapshot_id, "snapshot_manifest_sha256": SNAPSHOT_SHA,
                "entry_population_sha256": ENTRY_SHA, "entry_count": 505,
                "execution_contract": execution["id"], "exit_registry": registry["id"],
                "cost_contract": costs["id"], "contract_sha256": {p: digest(PUBLIC / p) for p in CONTRACTS},
                "exit_contract_ids": list(EXIT_IDS), "cutoff": CUTOFF,
                "population": {"normal_research_executable": 242, "board_conditional_only": 263,
                               "board_unconditional_executable": 0},
                "private_dataset": "ENTRY_TRADE_PNL_V1", "trade_rows": len(rows),
                "price_return_only": True, "total_return_qualified": False,
                "code_sha256": code_sha,
                "runtime": {"python": platform.python_version(), "duckdb": duckdb.__version__,
                            "pandas": pd.__version__},
                "tests": {"focused": focused_tests, "full_suite": full_suite},
                "public_artifacts": {p.name: digest(p) for p in public},
                "private_artifacts": {parquet.name: digest(parquet)}}
    write_json(PUBLIC / "run_manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    print(json.dumps(main(focused_tests="PASS" if "--focused-pass" in sys.argv else "NOT_RUN",
                          full_suite="ONE_PREEXISTING_FAILURE" if "--full-preexisting" in sys.argv else "NOT_RUN"),
                     ensure_ascii=False, sort_keys=True, indent=2))
