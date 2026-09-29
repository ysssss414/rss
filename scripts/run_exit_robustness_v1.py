"""Post-outcome exploratory robustness of frozen Stage 1 Entry/Exit results."""

from __future__ import annotations

import csv
from collections import Counter
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

from research.edge_assessment_v1 import (BOOTSTRAP_DRAWS, BOOTSTRAP_SEED, EXIT_IDS,
                                         LAYERS, bin_index, cluster_bootstrap,
                                         diagnostic_summary, finite, outcome_labels,
                                         target_first_net, trade_summary)
from research.snapshot import FrozenResearchSnapshot
from scripts.build_pre_entry_features_v1 import digest as digest
from scripts.run_entry_event_study_v1 import validate_population


PUBLIC = ROOT / "artifacts/stage1_exit_robustness_v1"
FEATURE_PRIVATE = ROOT / ".local_research_data/stage1_exit_robustness_v1/entry_pre_signal_features_v1.parquet"
EXIT_PUBLIC = ROOT / "artifacts/stage1_exit_and_pnl_v1"
EXIT_PRIVATE = ROOT / ".local_research_data/stage1_exit_and_pnl_v1/entry_trade_pnl_v1.parquet"
EVENT_PRIVATE = ROOT / ".local_research_data/stage1_entry_event_study_v1/entry_event_outcome_v1.parquet"
SNAPSHOT = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
ENTRY_SHA = "82d16ddf0f3c1af3c5015dd34ae08b9bbe83e23ed90c03b2c8b7ade7ec4bb0f1"
EXIT_MANIFEST_SHA = "3d7f825551a08872a487f86c5891e741f8503976433ff25ea83b6ada314265b9"
SNAPSHOT_SHA = "a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67"


def _json(path: Path, value: object) -> None:
    path.write_bytes((json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                 allow_nan=False) + "\n").encode("utf-8"))


def _csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"No rows for {path.name}")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _read_parquet(path: Path) -> list[dict]:
    con = duckdb.connect()
    try:
        records = con.execute("SELECT * FROM read_parquet(?)", [str(path)]).df().to_dict("records")
        return [{key: None if pd.isna(value) else value for key, value in row.items()}
                for row in records]
    finally:
        con.close()


def _validate_lineage() -> tuple[dict, dict, dict, dict, dict]:
    _, entries, _ = validate_population()
    if len(entries) != 505 or digest(ROOT / "artifacts/stage1_real_strategy_smoke_v1/entries.csv") != ENTRY_SHA:
        raise ValueError("BLOCKED_EDGE_POPULATION_DRIFT")
    if digest(EXIT_PUBLIC / "run_manifest.json") != EXIT_MANIFEST_SHA:
        raise ValueError("BLOCKED_EDGE_POPULATION_DRIFT: exit run manifest")
    parent = json.loads((EXIT_PUBLIC / "run_manifest.json").read_text(encoding="utf-8"))
    if (parent["run_id"] != "EXIT_AND_PNL_RUN_V1" or parent["entry_count"] != 505
            or parent["entry_population_sha256"] != ENTRY_SHA
            or parent["trade_rows"] != 4040 or tuple(parent["exit_contract_ids"]) != EXIT_IDS
            or parent["population"] != {"normal_research_executable": 242,
                                         "board_conditional_only": 263,
                                         "board_unconditional_executable": 0}):
        raise ValueError("BLOCKED_EDGE_POPULATION_DRIFT: exit parent identity")
    for name, expected in parent["code_sha256"].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f"BLOCKED_EDGE_POPULATION_DRIFT: exit parent code {name}")
    for name, expected in parent["public_artifacts"].items():
        if digest(EXIT_PUBLIC / name) != expected:
            raise ValueError(f"BLOCKED_EDGE_POPULATION_DRIFT: exit parent public {name}")
    if digest(EXIT_PRIVATE) != parent["private_artifacts"][EXIT_PRIVATE.name]:
        raise ValueError("BLOCKED_EDGE_POPULATION_DRIFT: private trade dataset")
    event_manifest = json.loads((ROOT / "artifacts/stage1_entry_event_study_v1/run_manifest.json").read_text(encoding="utf-8"))
    if digest(EVENT_PRIVATE) != event_manifest["private_artifacts"][EVENT_PRIVATE.name]:
        raise ValueError("BLOCKED_EDGE_POPULATION_DRIFT: event outcome dataset")
    receipt = json.loads((PUBLIC / "feature_builder_receipt.json").read_text(encoding="utf-8"))
    feature_contract = json.loads((PUBLIC / "pre_entry_feature_contract_v1.json").read_text(encoding="utf-8"))
    split = json.loads((PUBLIC / "edge_assessment_split_v1.json").read_text(encoding="utf-8"))
    if (receipt["status"] != "PASS" or receipt["entry_n"] != 505
            or receipt["source_entry_sha256"] != ENTRY_SHA
            or receipt["feature_contract_sha256"] != digest(PUBLIC / "pre_entry_feature_contract_v1.json")
            or receipt["private_parquet_sha256"] != digest(FEATURE_PRIVATE)
            or receipt["inventory_sha256"] != digest(PUBLIC / "pre_entry_feature_inventory.csv")
            or split["id"] != "EDGE_ASSESSMENT_SPLIT_V1"
            or split["validation_grade"] != "TEMPORAL_VALIDATION_DESCRIPTIVE_ONLY"):
        raise ValueError("BLOCKED_EDGE_FEATURE_LOOKAHEAD: feature/split identity")
    for name, expected in receipt["code_sha256"].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f"BLOCKED_EDGE_FEATURE_LOOKAHEAD: builder code {name}")
    costs = json.loads((EXIT_PUBLIC / "transaction_cost_contract_v1.json").read_text(encoding="utf-8"))
    registry = json.loads((EXIT_PUBLIC / "exit_contract_registry_v1.json").read_text(encoding="utf-8"))
    return parent, feature_contract, split, costs, registry


def _period(day: str) -> str:
    return "DEVELOPMENT" if day < "2026-01-01" else "TEMPORAL_VALIDATION"


def _repeat_group(feature: dict) -> str:
    if feature["security_entry_count"] == 1:
        return "SINGLE_ENTRY_SECURITY"
    return ("REPEATED_SECURITY_FIRST_ENTRY" if feature["entry_ordinal_for_security"] == 1
            else "REPEATED_SECURITY_SUBSEQUENT_ENTRY")


def _selected_groups(rows: list[dict], features: dict[str, dict]) -> list[tuple[str, str, list[dict]]]:
    groups = [("ALL", "ALL", rows)]
    selectors = (
        ("YEAR", ("2024", "2025", "2026"), lambda r: r["entry_date"][:4]),
        ("SPLIT", ("DEVELOPMENT", "TEMPORAL_VALIDATION"), lambda r: _period(r["entry_date"])),
        ("ENTRY_TYPE", ("ENTRY_A_PULLBACK_TO_MA5_V1", "ENTRY_B_RSI_RECROSS_70_V1"),
         lambda r: r["entry_type"]),
        ("OBSERVATION_STRENGTH", ("4/5", "5/5"), lambda r: r["observation_pattern"]),
        ("ENTRY_ORDINAL", ("FIRST_ENTRY", "SUBSEQUENT_ENTRY"),
         lambda r: "FIRST_ENTRY" if features[r["entry_event_id"]]["entry_ordinal_for_security"] == 1
         else "SUBSEQUENT_ENTRY"),
        ("REPEATED_SECURITY", ("SINGLE_ENTRY_SECURITY", "REPEATED_SECURITY"),
         lambda r: "SINGLE_ENTRY_SECURITY" if features[r["entry_event_id"]]["security_entry_count"] == 1
         else "REPEATED_SECURITY"),
        ("REENTRY_STRUCTURE", ("SINGLE_ENTRY_SECURITY", "REPEATED_SECURITY_FIRST_ENTRY",
                               "REPEATED_SECURITY_SUBSEQUENT_ENTRY"),
         lambda r: _repeat_group(features[r["entry_event_id"]])),
        ("TRIGGER_PROVENANCE", ("QUALIFIED_BY_V3_PRICE_PATH", "QUALIFIED_BY_OPERATIONAL_STATUS"),
         lambda r: r["trigger_qualification_method"]),
    )
    for dimension, values, selector in selectors:
        for value in values:
            groups.append((dimension, value, [r for r in rows if selector(r) == value]))
        if sum(len(group) for dim, _, group in groups if dim == dimension) != len(rows):
            raise ValueError(f"BLOCKED_EDGE_ROBUSTNESS_NONDETERMINISTIC: unknown {dimension}")
    return groups


def _robustness(trades: list[dict], features: dict[str, dict]) -> tuple[list[dict], list[dict], list[dict]]:
    normal, board, temporal = [], [], []
    for layer in LAYERS:
        for exit_id in EXIT_IDS:
            selected = [r for r in trades if r["population_layer"] == layer and r["exit_contract_id"] == exit_id]
            for dim, value, group in _selected_groups(selected, features):
                record = {"dataset": "NORMAL_CLOSE_ROBUSTNESS_V1" if layer == LAYERS[0]
                          else "BOARD_CONDITIONAL_ROBUSTNESS_V1",
                          "population_layer": layer, "exit_contract_id": exit_id,
                          "dimension": dim, "value": value, **trade_summary(group)}
                (normal if layer == LAYERS[0] else board).append(record)
                if dim in {"YEAR", "SPLIT"}:
                    temporal.append(record)
    return normal, board, temporal


def _joined_diagnostics(features: dict[str, dict], events: dict[str, dict],
                        trades: dict[tuple[str, str], dict]) -> list[dict]:
    rows = []
    for identity, feature in sorted(features.items()):
        event = events[identity]
        tp5 = trades[(identity, "TP5_H5")]
        row = {"entry_event_id": identity, "entry_date": feature["entry_date"],
               "entry_type": feature["entry_type"], "execution_intent": feature["execution_intent"],
               "observation_strength": feature["observation_strength"],
               "trigger_provenance": feature["trigger_provenance"],
               "feature": feature,
               **{f"h{h}_return": finite(event[f"h{h}_signal_forward_close_return"])
                  if event[f"h{h}_outcome_status"] == "AVAILABLE" else None for h in (1, 3, 5)},
               "h5_mfe": finite(event["h5_signal_forward_mfe"])
               if event["h5_outcome_status"] == "AVAILABLE" else None,
               "tp5_net": finite(tp5["net_price_return"]) if tp5["outcome_status"] == "CLOSED" else None,
               **outcome_labels(event, tp5)}
        rows.append(row)
    return rows


def _diagnostics(joined: list[dict], contract: dict) -> tuple[list[dict], list[dict]]:
    output = []
    normal = [r for r in joined if r["execution_intent"] == "NORMAL_CLOSE_INTENT"]
    periods = ("ALL", "DEVELOPMENT", "TEMPORAL_VALIDATION")
    for period in periods:
        period_normal = [r for r in normal if period == "ALL" or _period(r["entry_date"]) == period]
        for feature in contract["features"]:
            bounds = contract["diagnostic_bins"][feature]
            for index in [*range(len(bounds) + 1), None]:
                selected = [r for r in period_normal if bin_index(r["feature"][feature], bounds) == index]
                output.append({"dataset": "LEFT_TAIL_DIAGNOSTICS_V1", "analysis": "UNIVARIATE",
                               "period": period, "population": "NORMAL_CLOSE_INTENT",
                               "feature": feature, "subgroup": "ALL", "bin_index": index,
                               "bin_label": f"BIN_{index}" if index is not None else "MISSING",
                               "bin_lower_inclusive": bounds[index - 1] if index not in (None, 0) else None,
                               "bin_upper_exclusive": bounds[index] if index is not None and index < len(bounds) else None,
                               **diagnostic_summary(selected)})
        bounds = contract["diagnostic_bins"]["prior5_raw_return"]
        for cross, source, dimension, values in (
                ("ENTRY_TYPE_X_PRIOR5_RAW_RETURN", period_normal, "entry_type",
                 ("ENTRY_A_PULLBACK_TO_MA5_V1", "ENTRY_B_RSI_RECROSS_70_V1")),
                ("INTENT_X_PRIOR5_RAW_RETURN",
                 [r for r in joined if period == "ALL" or _period(r["entry_date"]) == period],
                 "execution_intent", ("NORMAL_CLOSE_INTENT", "LIMIT_UP_CLOSE_BOARD_INTENT"))):
            for value in values:
                for index in [*range(len(bounds) + 1), None]:
                    selected = [r for r in source if r[dimension] == value
                                and bin_index(r["feature"]["prior5_raw_return"], bounds) == index]
                    output.append({"dataset": "LEFT_TAIL_DIAGNOSTICS_V1", "analysis": cross,
                                   "period": period, "population": "NORMAL_CLOSE_INTENT" if cross.startswith("ENTRY")
                                   else "FROZEN_ALL_INTENTS_NO_PNL_POOLING",
                                   "feature": "prior5_raw_return", "subgroup": value,
                                   "bin_index": index,
                                   "bin_label": f"BIN_{index}" if index is not None else "MISSING",
                                   "bin_lower_inclusive": bounds[index - 1] if index not in (None, 0) else None,
                                   "bin_upper_exclusive": bounds[index] if index is not None and index < len(bounds) else None,
                                   **diagnostic_summary(selected)})
    candidates = []
    for feature in contract["features"]:
        bins = []
        for period in ("DEVELOPMENT", "TEMPORAL_VALIDATION"):
            values = [r for r in output if r["analysis"] == "UNIVARIATE"
                      and r["period"] == period and r["feature"] == feature
                      and r["bin_index"] is not None]
            bins.append(values)
        if (not all(values and values[0]["n_total"] >= (10 if i == 0 else 5)
                    and values[-1]["n_total"] >= (10 if i == 0 else 5)
                    and all(r["left_tail_h5_10_fraction"] is not None for r in values)
                    for i, values in enumerate(bins))):
            continue
        differences = [values[-1]["left_tail_h5_10_fraction"] - values[0]["left_tail_h5_10_fraction"]
                       for values in bins]
        if abs(differences[0]) < .10 or differences[0] * differences[1] <= 0:
            continue
        direction = 1 if differences[0] > 0 else -1
        if not all(all(direction * (values[j + 1]["left_tail_h5_10_fraction"]
                                - values[j]["left_tail_h5_10_fraction"]) >= 0
                           for j in range(len(values) - 1)) for values in bins):
            continue
        candidates.append({"feature": feature,
                           "direction": "HIGHER_FEATURE_HIGHER_LEFT_TAIL" if direction > 0
                           else "HIGHER_FEATURE_LOWER_LEFT_TAIL",
                           "development_extreme_bin_gap": differences[0],
                           "temporal_descriptive_extreme_bin_gap": differences[1],
                           "development_extreme_bin_n": [bins[0][0]["n_total"], bins[0][-1]["n_total"]],
                           "temporal_extreme_bin_n": [bins[1][0]["n_total"], bins[1][-1]["n_total"]],
                           "ab_robustness": "CROSS_TAB_AVAILABLE_EXPLORATORY" if feature == "prior5_raw_return"
                           else "NOT_TESTED_AS_EXTRA_CROSS_TAB",
                           "status": "HYPOTHESIS_CANDIDATE_NOT_A_FILTER"})
    candidates.sort(key=lambda c: (-abs(c["development_extreme_bin_gap"]), c["feature"]))
    return output, candidates[:3]


def _cost_sensitivity(trades: list[dict]) -> list[dict]:
    output = []
    for layer in LAYERS:
        for exit_id in EXIT_IDS:
            selected = [r for r in trades if r["population_layer"] == layer and r["exit_contract_id"] == exit_id]
            closed = [r for r in selected if r["outcome_status"] == "CLOSED"]
            for scenario, multiple in (("BASELINE_RESEARCH_COST", 1), ("ZERO_COST_DIAGNOSTIC_ONLY", 0),
                                       ("TWO_X_RESEARCH_COST_CONSERVATIVE", 2)):
                values = [float(r["gross_price_return"]) - multiple * float(r["transaction_cost"])
                          for r in closed]
                output.append({"population_layer": layer, "exit_contract_id": exit_id,
                               "scenario": scenario, "cost_multiple": multiple,
                               "n_total": len(selected), "n_closed": len(closed),
                               "mean": statistics.fmean(values) if values else None,
                               "median": statistics.median(values) if values else None,
                               "positive_fraction": sum(x > 0 for x in values) / len(values) if values else None})
    return output


def _ambiguity_sensitivity(trades: list[dict], registry: dict, costs: dict) -> list[dict]:
    configs = {x["id"]: x for x in registry["exit_contracts"]}
    output = []
    for layer in LAYERS:
        for exit_id in EXIT_IDS:
            selected = [r for r in trades if r["population_layer"] == layer and r["exit_contract_id"] == exit_id]
            closed = [r for r in selected if r["outcome_status"] == "CLOSED"]
            tp = configs[exit_id]["take_profit"]
            upper = [target_first_net(r, tp=Decimal(str(tp)), costs=costs)
                     if tp is not None else float(r["net_price_return"]) for r in closed]
            primary = [float(r["net_price_return"]) for r in closed]
            output.append({"population_layer": layer, "exit_contract_id": exit_id,
                           "n_total": len(selected), "n_closed": len(closed),
                           "same_bar_ambiguous_n": sum(bool(r["same_bar_ambiguity"]) for r in closed),
                           "stop_first_primary_net_mean": statistics.fmean(primary) if primary else None,
                           "target_first_optimistic_net_mean": statistics.fmean(upper) if upper else None,
                           "optimistic_mean_delta": statistics.fmean(upper) - statistics.fmean(primary) if primary else None,
                           "target_first_role": "SENSITIVITY_UPPER_BOUND_NOT_EXECUTABLE_BASELINE"})
    return output


def _board_audit(features: dict[str, dict]) -> tuple[dict, dict[str, dict]]:
    board = [f for f in features.values() if f["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT"]
    codes = sorted({f["security_id"] for f in board})
    with FrozenResearchSnapshot(SNAPSHOT) as snapshot:
        if snapshot.validation["manifest_sha256"] != SNAPSHOT_SHA:
            raise ValueError("BLOCKED_BOARD_FILLABILITY_OVERCLAIM: snapshot changed")
        bars = {(str(r["security_id"]), pd.Timestamp(r["trade_date"]).date().isoformat()): r
                for r in snapshot.load_daily_bars(codes).to_dict("records")}
        status = {(str(r["security_id"]), pd.Timestamp(r["trade_date"]).date().isoformat()): r
                  for r in snapshot.load_daily_status(codes).to_dict("records")}
        dataset_names = sorted(snapshot.manifest["datasets"])
    types = Counter()
    cases = {}
    for feature in sorted(board, key=lambda f: f["entry_event_id"]):
        key = (feature["security_id"], feature["entry_date"])
        bar, state = bars.get(key), status.get(key)
        upper = finite(state.get("high_limited")) if state else None
        observed = [finite(bar.get(k)) for k in ("open", "high", "low", "close")] if bar else []
        if upper is None or not observed or any(x is None for x in observed):
            name = "UNKNOWN_D4_BOUND_OR_OHLC"
        elif observed[0] == observed[1] == observed[2] == observed[3] == upper:
            name = "ONE_PRICE_AT_D4_UPPER_BOUND"
        elif observed[3] == upper and observed[2] < upper:
            name = "LOWER_PRICE_SEEN_BEFORE_T_CLOSE_UNTIMED"
        elif observed[3] == upper:
            name = "CLOSE_AT_D4_UPPER_BOUND_OTHER"
        else:
            name = "D4_BOUND_CONFLICT_WITH_BOARD_INTENT"
        types[name] += 1
        cases[feature["entry_event_id"]] = {"structure": name, "security_id": key[0], "date": key[1],
                                             "d4_vendor_upper": upper,
                                             "raw_open_high_low_close": observed or None,
                                             "fill_inference": "NONE"}
    if sum(types.values()) != 263:
        raise ValueError("BLOCKED_BOARD_FILLABILITY_OVERCLAIM: board audit coverage")
    audit = {"id": "BOARD_FILLABILITY_AUDIT_V1", "status": "BOARD_FILLABILITY_DATA_INSUFFICIENT",
             "board_entry_n": 263, "historical_fill_probability": None,
             "eligible_unconditional_board_fills": 0,
             "frozen_snapshot_dataset_names": dataset_names,
             "frozen_snapshot_intraday_orderbook_or_queue_datasets": [
                 x for x in dataset_names if any(word in x.lower() for word in (
                     "minute", "tick", "order", "queue", "intraday", "seal"))],
             "repo_qualified_bridge": "research/data/amazingdata.py checked_sdk_bars uses Period.day only",
             "sdk_surface_from_existing_gate_c_audit": "Period.min1/min5 and query_snapshot API surface described; historical coverage, T-close timestamps, seal/queue records and entitlement unqualified; no live query in this stage",
             "entry_day_daily_structure_counts": dict(sorted(types.items())),
             "daily_ohlc_interpretation": "Even one-price upper-bound OHLC cannot prove queue fill; lower-price prints have no post-signal timestamp",
             "matching_rule_sources": {
                 "sse_price_time_priority": "https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml",
                 "szse_price_time_priority": "https://docs.static.szse.cn/www/lawrules/rule/trade/W020260424690713155663.pdf"
             },
             "d4_limit_caveat": "D4 vendor upper bound is a diagnostic comparison, not globally certified exchange reference rows",
             "conditional_pnl_only": True,
             "possible_next_contract": "BOARD_FILLABILITY_V1 only after timestamped historical order/queue or other verified fill evidence"}
    return audit, cases


def _case_audit(joined: list[dict], trades: dict[tuple[str, str], dict],
                board_cases: dict[str, dict]) -> dict:
    ordered = sorted(joined, key=lambda r: r["entry_event_id"])
    predicates = {
        "normal_failed_tp": lambda r: r["execution_intent"] == "NORMAL_CLOSE_INTENT"
                                    and r["FAILED_REBOUND_TP5"] is True,
        "normal_deep_loss": lambda r: r["execution_intent"] == "NORMAL_CLOSE_INTENT"
                                   and r["LEFT_TAIL_H5_20"] is True,
        "normal_mfe_then_reversal": lambda r: r["execution_intent"] == "NORMAL_CLOSE_INTENT"
                                           and r["TP5_THEN_DEEP_REVERSAL"] is True,
        "normal_early_negative": lambda r: r["execution_intent"] == "NORMAL_CLOSE_INTENT"
                                       and r["h1_return"] is not None and r["h1_return"] <= -.05,
        "normal_tp5_success": lambda r: r["execution_intent"] == "NORMAL_CLOSE_INTENT"
                                      and str(trades[(r["entry_event_id"], "TP5_H5")]["exit_reason"]).startswith("TP_"),
        "normal_quick_rebound": lambda r: r["execution_intent"] == "NORMAL_CLOSE_INTENT"
                                        and r["h1_return"] is not None and r["h1_return"] >= .05,
        "board_conditional_strong": lambda r: r["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT"
                                            and r["tp5_net"] is not None and r["tp5_net"] >= .05,
        "board_conditional_weak": lambda r: r["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT"
                                          and r["tp5_net"] is not None and r["tp5_net"] <= -.10,
        "board_one_price": lambda r: r["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT"
                                     and board_cases[r["entry_event_id"]]["structure"] == "ONE_PRICE_AT_D4_UPPER_BOUND",
        "board_lower_price_seen": lambda r: r["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT"
                                           and board_cases[r["entry_event_id"]]["structure"] == "LOWER_PRICE_SEEN_BEFORE_T_CLOSE_UNTIMED",
    }
    selected = {}
    for category, predicate in predicates.items():
        row = next((r for r in ordered if predicate(r)), None)
        if row is None:
            selected[category] = {"status": "NOT_OBSERVED_IN_FROZEN_POPULATION"}
            continue
        trade = trades[(row["entry_event_id"], "TP5_H5")]
        selected[category] = {"status": "OBSERVED", "entry_event_id": row["entry_event_id"],
                              "security_id": row["feature"]["security_id"],
                              "entry_date": row["entry_date"],
                              "pre_entry_features": {k: v for k, v in row["feature"].items()
                                                     if k not in {"entry_event_id", "observation_instance_id"}},
                              "later_outcome": {k: row[k] for k in (
                                  "h1_return", "h3_return", "h5_return", "h5_mfe",
                                  "LEFT_TAIL_H5_10", "LEFT_TAIL_H5_20", "FAILED_REBOUND_TP5",
                                  "TP5_THEN_DEEP_REVERSAL")},
                              "tp5_exit": {k: trade[k] for k in (
                                  "outcome_status", "exit_date", "exit_reason", "gross_price_return",
                                  "transaction_cost", "net_price_return")},
                              "board_t_day_ohlc": board_cases.get(row["entry_event_id"])}
    return {"selection": "first sorted event identity per frozen case category; no case-derived rule",
            "cases": selected}


def _tail_anatomy(trades: list[dict]) -> dict:
    by_exit = {}
    for exit_id in EXIT_IDS:
        selected = [r for r in trades if r["population_layer"] == LAYERS[0]
                    and r["exit_contract_id"] == exit_id and r["outcome_status"] == "CLOSED"]
        net = [float(r["net_price_return"]) for r in selected]
        negatives = [x for x in net if x < 0]
        extreme = [x for x in net if x <= -.20]
        by_exit[exit_id] = {"n_closed": len(net), "n_negative": len(negatives),
                            "n_loss_le_5": sum(x <= -.05 for x in net),
                            "n_loss_le_10": sum(x <= -.10 for x in net),
                            "n_loss_le_20": len(extreme),
                            "negative_return_sum": sum(negatives),
                            "loss_le_20_return_sum": sum(extreme),
                            "share_of_negative_return_magnitude_from_le_20": (
                                sum(extreme) / sum(negatives) if negatives else None),
                            "interpretation": "arithmetic contribution, not a removable or forecastable subset"}
    return {"population": "NORMAL_CLOSE_INTENT", "metric": "NET_RESEARCH_PRICE_RETURN",
            "by_exit": by_exit}


def _quality(features: dict[str, dict], events: dict[str, dict], trades: list[dict],
             normal: list[dict], board: list[dict], diagnostics: list[dict],
             candidates: list[dict], cost_rows: list[dict], ambiguity: list[dict],
             board_audit: dict, split: dict) -> dict:
    failures = []
    ids = set(features)
    if len(features) != 505 or set(events) != ids:
        failures.append("FEATURE_EVENT_GRAIN_OR_JOIN")
    keys = [(r["entry_event_id"], r["exit_contract_id"]) for r in trades]
    if (len(trades) != 4040 or len(set(keys)) != 4040
            or set(keys) != {(identity, exit_id) for identity in ids for exit_id in EXIT_IDS}):
        failures.append("EXIT_GRAIN_OR_JOIN")
    if (sum(f["execution_intent"] == "NORMAL_CLOSE_INTENT" for f in features.values()) != 242
            or sum(f["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT" for f in features.values()) != 263):
        failures.append("INTENT_POPULATION")
    for part, expected in (("development", "DEVELOPMENT"), ("temporal_validation", "TEMPORAL_VALIDATION")):
        selected = [f for f in features.values() if _period(f["entry_date"]) == expected]
        target = split[part]
        if (len(selected) != target["entry_n"]
                or sum(f["execution_intent"] == "NORMAL_CLOSE_INTENT" for f in selected) != target["normal_n"]
                or sum(f["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT" for f in selected) != target["board_n"]
                or sum(f["entry_type"] == "ENTRY_A_PULLBACK_TO_MA5_V1" for f in selected) != target["entry_a_n"]
                or sum(f["entry_type"] == "ENTRY_B_RSI_RECROSS_70_V1" for f in selected) != target["entry_b_n"]):
            failures.append("FROZEN_SPLIT_COUNTS")
    if any(f["feature_source_max_date"] != f["entry_date"] or f["trigger_date"] > f["entry_date"]
           for f in features.values()):
        failures.append("FEATURE_DATE_LOOKAHEAD")
    if any(any(any(forbidden in key.lower() for forbidden in (
        "outcome", "pnl", "mfe", "mae", "d8", "h1", "h3", "h5")) for key in f)
           for f in features.values()):
        failures.append("FEATURE_SCHEMA_LEAKAGE")
    if any(r["conditional_on_fill"] != (r["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT")
           or r["population_layer"] != (LAYERS[1] if r["conditional_on_fill"] else LAYERS[0])
           for r in trades):
        failures.append("BOARD_PNL_LAYER")
    if (len(normal) != len(board) or len(cost_rows) != 16 * 3 or len(ambiguity) != 16
            or len({(r["population_layer"], r["exit_contract_id"]) for r in normal if r["dimension"] == "ALL"}) != 8
            or len({(r["population_layer"], r["exit_contract_id"]) for r in board if r["dimension"] == "ALL"}) != 8):
        failures.append("ALL_EIGHT_EXITS_OR_SENSITIVITY")
    if len(candidates) > 3 or board_audit["status"] != "BOARD_FILLABILITY_DATA_INSUFFICIENT":
        failures.append("CANDIDATE_OR_BOARD_BOUNDARY")
    if any(r["target_first_optimistic_net_mean"] + 1e-12 < r["stop_first_primary_net_mean"]
           for r in ambiguity):
        failures.append("OPTIMISTIC_SENSITIVITY_BELOW_PRIMARY")
    if any(r["mean"] > next(x["mean"] for x in cost_rows
                            if x["population_layer"] == r["population_layer"]
                            and x["exit_contract_id"] == r["exit_contract_id"]
                            and x["scenario"] == "ZERO_COST_DIAGNOSTIC_ONLY") + 1e-12
           for r in cost_rows if r["scenario"] == "BASELINE_RESEARCH_COST"):
        failures.append("COST_SIGN")
    for feature in {r["feature"] for r in diagnostics if r["analysis"] == "UNIVARIATE"}:
        for period, expected in (("ALL", 242), ("DEVELOPMENT", 174), ("TEMPORAL_VALIDATION", 68)):
            n = sum(r["n_total"] for r in diagnostics if r["analysis"] == "UNIVARIATE"
                    and r["feature"] == feature and r["period"] == period)
            if n != expected:
                failures.append("DIAGNOSTIC_BIN_COVERAGE")
    return {"status": "PASS" if not failures else "FAIL", "failures": sorted(set(failures)),
            "frozen_entry_n": len(features), "unique_security_n": len({f["security_id"] for f in features.values()}),
            "trade_n": len(trades), "unique_trade_keys": len(set(keys)),
            "feature_outcome_join_n": len(ids & set(events)),
            "tested_feature_n": len({r["feature"] for r in diagnostics if r["analysis"] == "UNIVARIATE"}),
            "tested_cross_tab_n": len({r["analysis"] for r in diagnostics if r["analysis"] != "UNIVARIATE"}),
            "candidate_n": len(candidates), "board_unconditional_fill_n": 0,
            "validation_grade": "TEMPORAL_VALIDATION_DESCRIPTIVE_ONLY",
            "signal_firewall": "feature builder has no outcome/PnL/D8 imports or H1+ bar lookups; outcome joins occur only after feature parquet is frozen"}


def main(*, focused_tests: str = "NOT_RUN", full_suite: str = "NOT_RUN") -> dict:
    parent, feature_contract, split, costs, registry = _validate_lineage()
    feature_rows = _read_parquet(FEATURE_PRIVATE)
    event_rows = _read_parquet(EVENT_PRIVATE)
    trades = _read_parquet(EXIT_PRIVATE)
    features = {r["entry_event_id"]: r for r in feature_rows}
    events = {r["entry_event_id"]: r for r in event_rows}
    trade_map = {(r["entry_event_id"], r["exit_contract_id"]): r for r in trades}
    if len(features) != 505 or len(events) != 505 or len(trade_map) != 4040:
        raise ValueError("BLOCKED_EDGE_POPULATION_DRIFT: private grain")
    normal, board, temporal = _robustness(trades, features)
    joined = _joined_diagnostics(features, events, trade_map)
    diagnostics, candidates = _diagnostics(joined, feature_contract)
    cost_rows = _cost_sensitivity(trades)
    ambiguity = _ambiguity_sensitivity(trades, registry, costs)
    bootstrap = [cluster_bootstrap([r for r in trades if r["population_layer"] == layer
                                    and r["exit_contract_id"] == exit_id],
                                   layer=layer, exit_id=exit_id)
                 for layer in LAYERS for exit_id in EXIT_IDS]
    board_audit, board_cases = _board_audit(features)
    cases = _case_audit(joined, trade_map, board_cases)
    anatomy = _tail_anatomy(trades)
    quality = _quality(features, events, trades, normal, board, diagnostics,
                       candidates, cost_rows, ambiguity, board_audit, split)
    parent_summary = {(r["population_layer"], r["exit_contract_id"]): r for r in
                      csv.DictReader((EXIT_PUBLIC / "exit_contract_summary.csv").open(encoding="utf-8"))}
    for row in (r for r in normal + board if r["dimension"] == "ALL"):
        source = parent_summary[(row["population_layer"], row["exit_contract_id"])]
        if (row["n_total"] != int(source["n_total"])
                or row["n_closed"] != int(source["n_closed"])
                or abs(row["net_mean"] - float(source["net_mean"])) > 1e-12
                or abs(row["gross_mean"] - float(source["gross_mean"])) > 1e-12):
            quality["failures"].append("PARENT_BASELINE_RECONCILIATION")
    for row in (r for r in cost_rows if r["scenario"] == "BASELINE_RESEARCH_COST"):
        if abs(row["mean"] - float(parent_summary[(row["population_layer"], row["exit_contract_id"])]["net_mean"])) > 1e-12:
            quality["failures"].append("COST_BASELINE_RECONCILIATION")
    quality["failures"] = sorted(set(quality["failures"]))
    quality["status"] = "PASS" if not quality["failures"] else "FAIL"
    if quality["status"] != "PASS":
        raise ValueError(f"BLOCKED_EDGE_ROBUSTNESS_NONDETERMINISTIC: {quality['failures']}")
    PUBLIC.mkdir(parents=True, exist_ok=True)
    _csv(PUBLIC / "normal_close_robustness_summary.csv", normal)
    _csv(PUBLIC / "board_conditional_robustness_summary.csv", board)
    _csv(PUBLIC / "temporal_robustness_summary.csv", temporal)
    _csv(PUBLIC / "left_tail_diagnostics.csv", diagnostics)
    _csv(PUBLIC / "cost_sensitivity.csv", cost_rows)
    _csv(PUBLIC / "same_bar_ambiguity_sensitivity.csv", ambiguity)
    _json(PUBLIC / "left_tail_hypothesis_candidates.json", {
        "status": "HYPOTHESIS_CANDIDATES_FOUND" if candidates else "NO_STABLE_PRE_ENTRY_DIAGNOSTIC",
        "tested_feature_n": len(feature_contract["features"]),
        "tested_cross_tabs": feature_contract["tested_cross_tabs"],
        "multiple_testing_risk": "EXPLORATORY_MULTIPLE_TESTING_RISK; 2026 is descriptive, not untouched holdout",
        "candidates": candidates})
    _json(PUBLIC / "edge_cluster_bootstrap.json", {
        "method": "event-weighted mean of closed rows; resample unique security clusters with replacement, retaining all repeated events",
        "seed_base": BOOTSTRAP_SEED, "draws_per_layer_exit": BOOTSTRAP_DRAWS,
        "intervals": "two-sided percentile 90% and 95%; within-sample uncertainty only",
        "results": bootstrap})
    _json(PUBLIC / "board_fillability_audit.json", board_audit)
    _json(PUBLIC / "edge_case_audit.json", cases)
    _json(PUBLIC / "left_tail_anatomy.json", anatomy)
    _json(PUBLIC / "edge_assessment_quality_receipt.json", quality)
    overall_normal = [r for r in normal if r["dimension"] == "ALL"]
    board_positive = [r for r in board if r["dimension"] == "ALL" and r["net_mean"] > 0
                      and all(next(x["net_mean"] for x in board if x["exit_contract_id"] == r["exit_contract_id"]
                                   and x["dimension"] == "SPLIT" and x["value"] == part) > 0
                              for part in ("DEVELOPMENT", "TEMPORAL_VALIDATION"))]
    conclusions = {
        "normal_close": "EDGE_NOT_SUPPORTED" if all(r["net_mean"] < 0 for r in overall_normal)
        else "EDGE_INCONCLUSIVE",
        "board_conditional": "CONDITIONAL_EDGE_CANDIDATE" if board_positive
        else "CONDITIONAL_EDGE_NOT_SUPPORTED",
        "board_fillability": "FILLABILITY_UNQUALIFIED",
        "left_tail_filtering": "HYPOTHESIS_CANDIDATES_FOUND" if candidates
        else "NO_STABLE_PRE_ENTRY_DIAGNOSTIC",
        "validation_grade": "POST_OUTCOME_EXPLORATORY_TEMPORAL_DESCRIPTIVE_ONLY",
        "next_research_recommendation": "STAGE1_HYPOTHESIS_HOLDOUT_VALIDATION" if candidates
        else "STAGE1_STRATEGY_REASSESSMENT",
        "limitations": "No claim of an independently validated executable or board-fill edge"}
    _json(PUBLIC / "edge_assessment_conclusions.json", conclusions)
    code_sha = {name: digest(ROOT / name) for name in (
        "research/edge_assessment_v1.py", "scripts/run_exit_robustness_v1.py")}
    old_path = PUBLIC / "run_manifest.json"
    old = json.loads(old_path.read_text(encoding="utf-8")) if old_path.exists() else {}
    prior = old.get("public_artifacts", {}) if old.get("code_sha256") == code_sha else {}
    actual = {p.name: digest(p) for p in sorted(PUBLIC.iterdir()) if p.is_file()
              and p.name not in {"run_manifest.json", "edge_assessment_reproducibility.json"}}
    if any(actual.get(name) != expected for name, expected in prior.items()
           if name not in {"edge_assessment_reproducibility.json"}):
        raise ValueError("BLOCKED_EDGE_ROBUSTNESS_NONDETERMINISTIC: public artifact hash")
    _json(PUBLIC / "edge_assessment_reproducibility.json", {
        "status": "PASS", "parent_exit_manifest_sha256": EXIT_MANIFEST_SHA,
        "private_feature_parquet_sha256": digest(FEATURE_PRIVATE),
        "private_trade_parquet_sha256": digest(EXIT_PRIVATE),
        "public_content_sha256": sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest(),
        "determinism": "fixed split/features/bins/bootstrap seed and sorted frozen inputs; rerun refuses changed public hashes"})
    actual["edge_assessment_reproducibility.json"] = digest(PUBLIC / "edge_assessment_reproducibility.json")
    manifest = {"run_id": "EXIT_ROBUSTNESS_AND_EDGE_ASSESSMENT_RUN_V1", "status": "MATERIALIZED",
                "parent_run_id": parent["run_id"], "parent_manifest_sha256": EXIT_MANIFEST_SHA,
                "entry_population_sha256": ENTRY_SHA, "entry_n": 505,
                "exit_contract_ids": list(EXIT_IDS), "snapshot_manifest_sha256": SNAPSHOT_SHA,
                "split_contract": "EDGE_ASSESSMENT_SPLIT_V1",
                "split_sha256": digest(PUBLIC / "edge_assessment_split_v1.json"),
                "feature_contract": feature_contract["id"],
                "feature_contract_sha256": digest(PUBLIC / "pre_entry_feature_contract_v1.json"),
                "tested_feature_list": feature_contract["features"],
                "tested_cross_tabs": feature_contract["tested_cross_tabs"],
                "bootstrap": {"unit": "security_id", "draws": BOOTSTRAP_DRAWS, "seed_base": BOOTSTRAP_SEED},
                "board_fillability_status": board_audit["status"],
                "conclusions": conclusions,
                "code_sha256": code_sha,
                "feature_builder_code_sha256": json.loads((PUBLIC / "feature_builder_receipt.json").read_text(encoding="utf-8"))["code_sha256"],
                "runtime": {"python": platform.python_version(), "duckdb": duckdb.__version__,
                            "pandas": pd.__version__},
                "tests": {"focused": focused_tests, "full_suite": full_suite},
                "public_artifacts": actual,
                "private_artifacts": {FEATURE_PRIVATE.name: digest(FEATURE_PRIVATE),
                                      EXIT_PRIVATE.name: digest(EXIT_PRIVATE)}}
    _json(old_path, manifest)
    return manifest


if __name__ == "__main__":
    print(json.dumps(main(focused_tests="PASS" if "--focused-pass" in sys.argv else "NOT_RUN",
                          full_suite="ONE_PREEXISTING_FAILURE" if "--full-preexisting" in sys.argv else "NOT_RUN"),
                     ensure_ascii=False, sort_keys=True, indent=2))
