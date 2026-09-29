"""Join frozen outcomes only after independent T-close context materialization."""

from __future__ import annotations

import csv
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

from research.context_discovery_v1 import bucket, cluster_contrast, summary
from research.context_features_v1 import PRIMARY_FEATURES
from scripts.build_entry_context_features_v1 import digest, write_csv, write_json


PUBLIC = ROOT / "artifacts/stage1_context_discovery_v1"
PRIVATE = ROOT / ".local_research_data/stage1_context_discovery_v1/entry_context_features_v1.parquet"
ROBUST = ROOT / "artifacts/stage1_exit_robustness_v1"
TRADES = ROOT / ".local_research_data/stage1_exit_and_pnl_v1/entry_trade_pnl_v1.parquet"
EVENTS = ROOT / ".local_research_data/stage1_entry_event_study_v1/entry_event_outcome_v1.parquet"
PARENT_SHA = "06db29faf777912fd1f44e2b6bd599e1211cbc6538fc3a7c8dffaf810ebac769"
LAYERS = ("NORMAL_CLOSE_INTENT", "LIMIT_UP_CLOSE_BOARD_INTENT")
PERIODS = ("ALL", "2024", "2025", "2026", "DEVELOPMENT", "TEMPORAL_DESCRIPTIVE")
EXITS = ("TIME_H1", "TIME_H3", "TIME_H5", "TP5_H5", "TP10_H5", "TP5_SL5_H5",
         "TP10_SL10_H5", "TP10_SL5_H5")


def read_parquet(path: Path) -> list[dict]:
    con = duckdb.connect()
    try:
        raw = con.execute("SELECT * FROM read_parquet(?)", [str(path)]).df().to_dict("records")
        return [{name: None if pd.isna(value) else value for name, value in row.items()}
                for row in raw]
    finally:
        con.close()


def _verify() -> tuple[dict, dict, dict]:
    if digest(ROBUST / "run_manifest.json") != PARENT_SHA:
        raise ValueError("BLOCKED_CONTEXT_POPULATION_DRIFT: parent manifest")
    parent = json.loads((ROBUST / "run_manifest.json").read_text(encoding="utf-8"))
    if parent["entry_n"] != 505 or tuple(parent["exit_contract_ids"]) != EXITS:
        raise ValueError("BLOCKED_CONTEXT_POPULATION_DRIFT: parent counts")
    receipt = json.loads((PUBLIC / "context_feature_builder_receipt.json").read_text(encoding="utf-8"))
    contract = json.loads((PUBLIC / "context_feature_contract_v1.json").read_text(encoding="utf-8"))
    bins = json.loads((PUBLIC / "context_bins_v1.json").read_text(encoding="utf-8"))
    if (receipt["status"] != "PASS" or receipt["entry_n"] != 505
            or receipt["contract_sha256"] != digest(PUBLIC / "context_feature_contract_v1.json")
            or receipt["bins_sha256"] != digest(PUBLIC / "context_bins_v1.json")
            or receipt["parquet_sha256"][PRIVATE.name] != digest(PRIVATE)
            or tuple(contract["primary_features"]) != PRIMARY_FEATURES
            or bins["id"] != "CONTEXT_DEVELOPMENT_TERCILES_V1"):
        raise ValueError("BLOCKED_CONTEXT_FEATURE_LEAKAGE: frozen feature contract")
    for name, expected in receipt["code_sha256"].items():
        if digest(ROOT / name) != expected:
            raise ValueError("BLOCKED_CONTEXT_FEATURE_LEAKAGE: builder code drift")
    if (digest(TRADES) != parent["private_artifacts"][TRADES.name]
            or digest(EVENTS) != json.loads((ROOT / "artifacts/stage1_entry_event_study_v1/run_manifest.json").read_text(
                encoding="utf-8"))["private_artifacts"][EVENTS.name]):
        raise ValueError("BLOCKED_CONTEXT_POPULATION_DRIFT: frozen outcome files")
    return parent, contract, bins


def _period(row: dict, period: str) -> bool:
    year = row["entry_date"][:4]
    return (period == "ALL" or period == year or
            period == "DEVELOPMENT" and year in ("2024", "2025") or
            period == "TEMPORAL_DESCRIPTIVE" and year == "2026")


def _join(features: list[dict], events: list[dict], trades: list[dict]) -> list[dict]:
    fm = {x["entry_event_id"]: x for x in features}
    em = {x["entry_event_id"]: x for x in events}
    tm = {(x["entry_event_id"], x["exit_contract_id"]): x for x in trades}
    if (len(fm) != 505 or len(em) != 505 or set(fm) != set(em)
            or len(trades) != 4040 or len(tm) != 4040
            or set(tm) != {(identity, exit_id) for identity in fm for exit_id in EXITS}):
        raise ValueError("BLOCKED_CONTEXT_POPULATION_DRIFT: join grain")
    joined = []
    for identity in sorted(fm):
        feature, event = fm[identity], em[identity]
        if (feature["feature_source_max_date"] != feature["entry_date"]
                or feature["entry_date"] != event["entry_date"]
                or feature["security_id"] != event["security_id"]
                or feature["execution_intent"] != event["execution_intent"]):
            raise ValueError("BLOCKED_CONTEXT_FEATURE_LEAKAGE: identity/time mismatch")
        tp = tm[(identity, "TP5_H5")]
        h5 = (float(event["h5_signal_forward_close_return"])
              if event["h5_outcome_status"] == "AVAILABLE" else None)
        if tp["population_layer"] != ("CONDITIONAL_ON_FILL_ONLY" if feature["execution_intent"] == LAYERS[1]
                                      else "EXECUTABLE_PNL_BASELINE_RESEARCH_ASSUMPTION"):
            raise ValueError("BLOCKED_CONTEXT_POPULATION_DRIFT: board layer")
        row = {"entry_event_id": identity, "security_id": feature["security_id"],
               "entry_date": feature["entry_date"], "execution_intent": feature["execution_intent"],
               "feature": feature, "tp5_net": float(tp["net_price_return"]) if tp["outcome_status"] == "CLOSED" else None,
               "tp5_success": str(tp["exit_reason"]).startswith("TP_") if tp["outcome_status"] == "CLOSED" else None,
               "h5_signal": h5, "tail10": h5 <= -.10 if h5 is not None else None,
               "tail20": h5 <= -.20 if h5 is not None else None,
               "market_risk_regime": feature["market_risk_regime"] or "MISSING",
               "style_regime": feature["style_regime"] or "MISSING",
               "exit_nets": {exit_id: (float(trade["net_price_return"])
                                       if (trade := tm[(identity, exit_id)])["outcome_status"] == "CLOSED" else None)
                             for exit_id in EXITS}}
        for horizon in (1, 3, 5):
            trade = tm[(identity, f"TIME_H{horizon}")]
            row[f"time_h{horizon}_net"] = (float(trade["net_price_return"])
                                            if trade["outcome_status"] == "CLOSED" else None)
        joined.append(row)
    return joined


def _feature_summaries(rows: list[dict], bins: dict) -> tuple[list[dict], list[dict], list[dict]]:
    market, style, temporal = [], [], []
    for row in rows:
        for name in PRIMARY_FEATURES:
            row[name + "_bucket"] = bucket(row["feature"][name], bins["boundaries"][name])
    for intent in LAYERS:
        selected_intent = [r for r in rows if r["execution_intent"] == intent]
        for period in PERIODS:
            selected = [r for r in selected_intent if _period(r, period)]
            for name in PRIMARY_FEATURES:
                for value in ("LOW", "MID", "HIGH", "MISSING"):
                    group = [r for r in selected if r[name + "_bucket"] == value]
                    record = {"population": intent, "execution_basis": "CONDITIONAL_ON_FILL" if intent == LAYERS[1]
                              else "SAME_CLOSE_RESEARCH_COUNTERFACTUAL", "period": period,
                              "feature": name, "bucket": value, "development_cutpoints": json.dumps(
                                  bins["boundaries"][name], separators=(",", ":")), **summary(group)}
                    (market if PRIMARY_FEATURES.index(name) < 5 else style).append(record)
                    if period not in ("ALL",):
                        temporal.append({"population": intent, "period": period,
                                         "dimension": name, "subgroup": value, **summary(group)})
    return market, style, temporal


def _regimes(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    output, interaction = [], []
    risk = ("RISK_OFF", "NEUTRAL", "RISK_ON", "MISSING")
    style = ("TREND_DOMINANT", "MIXED", "SHORTLINE_DOMINANT", "MISSING")
    for intent in LAYERS:
        population = [r for r in rows if r["execution_intent"] == intent]
        for period in PERIODS:
            selected = [r for r in population if _period(r, period)]
            for dimension, labels in (("MARKET_RISK_REGIME", risk), ("STYLE_REGIME", style)):
                key = dimension.lower()
                for label in labels:
                    output.append({"population": intent, "execution_basis": "CONDITIONAL_ON_FILL" if intent == LAYERS[1]
                                   else "SAME_CLOSE_RESEARCH_COUNTERFACTUAL", "period": period,
                                   "dimension": dimension, "regime": label,
                                   **summary([r for r in selected if r[key] == label])})
            for a in risk[:3]:
                for b in style[:3]:
                    interaction.append({"population": intent, "execution_basis": "CONDITIONAL_ON_FILL" if intent == LAYERS[1]
                                        else "SAME_CLOSE_RESEARCH_COUNTERFACTUAL", "period": period,
                                        "interaction": "MARKET_RISK_REGIME_X_STYLE_REGIME", "market_risk_regime": a,
                                        "style_regime": b,
                                        **summary([r for r in selected if r["market_risk_regime"] == a
                                                   and r["style_regime"] == b])})
    return output, interaction


def _all_exit_regimes(rows: list[dict]) -> list[dict]:
    """Fixed eight-exit readout, with no within-context exit selection."""
    output = []
    for intent in LAYERS:
        population = [r for r in rows if r["execution_intent"] == intent]
        for period in PERIODS:
            selected = [r for r in population if _period(r, period)]
            for dimension, key, labels in (
                    ("MARKET_RISK_REGIME", "market_risk_regime", ("RISK_OFF", "NEUTRAL", "RISK_ON", "MISSING")),
                    ("STYLE_REGIME", "style_regime", ("TREND_DOMINANT", "MIXED", "SHORTLINE_DOMINANT", "MISSING"))):
                for label in labels:
                    group = [r for r in selected if r[key] == label]
                    for exit_id in EXITS:
                        values = [r["exit_nets"][exit_id] for r in group if r["exit_nets"][exit_id] is not None]
                        output.append({"population": intent, "execution_basis": "CONDITIONAL_ON_FILL" if intent == LAYERS[1]
                                       else "SAME_CLOSE_RESEARCH_COUNTERFACTUAL", "period": period,
                                       "dimension": dimension, "regime": label, "exit_contract_id": exit_id,
                                       "n_events": len(group), "n_closed": len(values),
                                       "net_mean": sum(values) / len(values) if values else None,
                                       "net_median": statistics.median(values) if values else None,
                                       "net_positive_fraction": sum(x > 0 for x in values) / len(values) if values else None,
                                       "sample_guardrail": "N_LT_10_NO_SHORTLIST" if len(group) < 10 else
                                                           "SMALL_SAMPLE_DESCRIPTIVE_ONLY" if len(group) < 20 else ""})
    return output


def _hypotheses(rows: list[dict], bins: dict, boot: list[dict]) -> dict:
    normal = [r for r in rows if r["execution_intent"] == LAYERS[0]]
    specs = (("H1A_PRIOR_STRONG_FEEDBACK", "prior_limit_up_next_day_median_return_bucket", "HIGH", "LOW", 1,
              "Prior limit-up stocks continuing to earn may permit high-position rebound."),
             ("H1B_LOW_HIGH_POSITION_NEGATIVE_FEEDBACK", "high_position_negative_feedback_rate_bucket", "LOW", "HIGH", -1,
              "Fewer sharp losses among yesterday's high-board stocks may reduce left-tail risk."),
             ("H2_SHORTLINE_STYLE_DOMINANCE", "style_regime", "SHORTLINE_DOMINANT", "TREND_DOMINANT", 0,
              "The Entry may work only when shortline rather than capacity trend dominates."))
    checked, candidates = [], []
    for identity, field, good, bad, monotonic_sign, rationale in specs:
        selected = [r for r in normal if r[field] in (good, bad)]
        favorable = [r for r in selected if r[field] == good]
        adverse = [r for r in selected if r[field] == bad]
        def delta(a: list[dict], b: list[dict], key: str) -> float | None:
            va, vb = [r[key] for r in a if r[key] is not None], [r[key] for r in b if r[key] is not None]
            return sum(va) / len(va) - sum(vb) / len(vb) if va and vb else None
        net = delta(favorable, adverse, "tp5_net")
        tail = delta(favorable, adverse, "tail10")
        yearly = {}
        for year in ("2024", "2025", "2026"):
            a, b = [r for r in favorable if r["entry_date"].startswith(year)], [r for r in adverse if r["entry_date"].startswith(year)]
            yearly[year] = {"favorable_n": len(a), "adverse_n": len(b),
                            "tp5_net_difference": delta(a, b, "tp5_net"),
                            "left_tail_difference": delta(a, b, "tail10")}
        monotonic = True
        if monotonic_sign:
            for period in ("ALL", "2024", "2025", "2026"):
                means = [summary([r for r in normal if _period(r, period) and r[field] == label])["tp5_net_mean"]
                         for label in ("LOW", "MID", "HIGH")]
                if any(x is None for x in means):
                    monotonic = False
                    break
                if monotonic_sign == 1 and not means[0] <= means[1] <= means[2]:
                    monotonic = False
                if monotonic_sign == -1 and not means[0] >= means[1] >= means[2]:
                    monotonic = False
        loo_positive = all((value := delta([r for r in favorable if r["security_id"] != code],
                                        [r for r in adverse if r["security_id"] != code], "tp5_net")) is not None
                           and value > 0 for code in {r["security_id"] for r in selected})
        gates = {"full_n_each_ge20": min(len(favorable), len(adverse)) >= 20,
                 "year_n_each_ge10": all(min(x["favorable_n"], x["adverse_n"]) >= 10 for x in yearly.values()),
                 "net_improvement_ge_2pp": net is not None and net >= .02,
                 "tail_reduction_ge_5pp": tail is not None and tail <= -.05,
                 "yearly_net_and_tail_direction": all(x["tp5_net_difference"] is not None and x["tp5_net_difference"] > 0
                                                       and x["left_tail_difference"] is not None and x["left_tail_difference"] < 0
                                                       for x in yearly.values()),
                 "monotonic_if_numeric": monotonic, "leave_one_security_out_positive": loo_positive}
        bootstrap = next(x for x in boot if x["contrast_id"] == identity and x["population"] == LAYERS[0])
        record = {"hypothesis_id": identity, "feature_or_regime": field,
                  "rule_candidate_description": f"{field} {good} rather than {bad}; no filter approved",
                  "economic_rationale": rationale,
                  "full_sample_n": {"favorable": len(favorable), "adverse": len(adverse)},
                  "yearly": yearly,
                  "development": {"favorable_n": sum(r["entry_date"] < "2026-01-01" for r in favorable),
                                  "adverse_n": sum(r["entry_date"] < "2026-01-01" for r in adverse),
                                  "tp5_net_difference": delta([r for r in favorable if r["entry_date"] < "2026-01-01"],
                                                               [r for r in adverse if r["entry_date"] < "2026-01-01"], "tp5_net")},
                  "temporal_2026_grade": "DESCRIPTIVE_POST_OUTCOME_NOT_OOS",
                  "tp5_net_difference": net, "left_tail_h5_10_difference": tail,
                  "bootstrap_ci95": bootstrap["tp5_net_difference_ci95"],
                  "gates": gates, "caveats": ["Post-outcome hypothesis, not independent validation",
                                                "Same-close research fill; board fillability separate"]}
        passed = all(gates.values())
        record["status"] = "EXPLORATORY_ONLY" if passed else "NOT_ADMITTED"
        checked.append(record)
        if passed:
            candidates.append(record)
    return {"status": "MARKET_STYLE_HYPOTHESIS_CANDIDATE_FOUND" if candidates else "NO_STABLE_MARKET_STYLE_CONTEXT",
            "hypothesis_status": "HYPOTHESIS_CANDIDATE" if candidates else "NO_STABLE_CONTEXT_HYPOTHESIS",
            "theme_status": "THEME_CONTEXT_DEFERRED_DATA_UNQUALIFIED",
            "validation_grade": "POST_OUTCOME_EXPLORATORY_TEMPORAL_DESCRIPTIVE_ONLY",
            "candidates": candidates[:3], "pre_registered_questions_checked": checked,
            "no_approved_filter": True}


def main() -> dict:
    parent, contract, bins = _verify()
    features, events, trades = read_parquet(PRIVATE), read_parquet(EVENTS), read_parquet(TRADES)
    rows = _join(features, events, trades)
    market, style, temporal = _feature_summaries(rows, bins)
    regimes, interaction = _regimes(rows)
    all_exits = _all_exit_regimes(rows)
    temporal += [{"population": x["population"], "period": x["period"],
                  "dimension": x["dimension"], "subgroup": x["regime"],
                  **{key: value for key, value in x.items() if key in summary([])}}
                 for x in regimes if x["period"] != "ALL"]
    normal = [r for r in rows if r["execution_intent"] == LAYERS[0]]
    board = [r for r in rows if r["execution_intent"] == LAYERS[1]]
    contrast_specs = (
        ("MARKET_RISK_ON_VS_OFF", "market_risk_regime", "RISK_ON", "RISK_OFF"),
        ("H1A_PRIOR_STRONG_FEEDBACK", "prior_limit_up_next_day_median_return_bucket", "HIGH", "LOW"),
        ("H1B_LOW_HIGH_POSITION_NEGATIVE_FEEDBACK", "high_position_negative_feedback_rate_bucket", "LOW", "HIGH"),
        ("H2_SHORTLINE_STYLE_DOMINANCE", "style_regime", "SHORTLINE_DOMINANT", "TREND_DOMINANT"),
    )
    boot = [{"population": intent, "execution_basis": "CONDITIONAL_ON_FILL" if intent == LAYERS[1]
             else "SAME_CLOSE_RESEARCH_COUNTERFACTUAL", **cluster_contrast(group, field, good, bad, identity)}
            for intent, group in ((LAYERS[0], normal), (LAYERS[1], board))
            for identity, field, good, bad in contrast_specs]
    shortlist = _hypotheses(rows, bins, boot)
    quality_failures = []
    if (len(rows) != 505 or len(normal) != 242 or len(board) != 263
            or len({r["entry_event_id"] for r in rows}) != 505):
        quality_failures.append("FROZEN_POPULATION")
    if any(r["feature"]["feature_source_max_date"] != r["entry_date"] for r in rows):
        quality_failures.append("LOOKAHEAD")
    for name in PRIMARY_FEATURES:
        for intent, expected in ((LAYERS[0], 242), (LAYERS[1], 263)):
            if sum(x["n_events"] for x in market + style if x["population"] == intent and x["period"] == "ALL"
                   and x["feature"] == name) != expected:
                quality_failures.append("FEATURE_BIN_COVERAGE")
    for intent, expected in ((LAYERS[0], 242), (LAYERS[1], 263)):
        for dimension in ("MARKET_RISK_REGIME", "STYLE_REGIME"):
            if sum(x["n_events"] for x in regimes if x["population"] == intent and x["period"] == "ALL"
                   and x["dimension"] == dimension) != expected:
                quality_failures.append("REGIME_COVERAGE")
    for intent, file_name in ((LAYERS[0], "normal_close_robustness_summary.csv"),
                              (LAYERS[1], "board_conditional_robustness_summary.csv")):
        with (ROBUST / file_name).open(encoding="utf-8", newline="") as stream:
            baseline = next(x for x in csv.DictReader(stream) if x["dimension"] == "ALL"
                            and x["exit_contract_id"] == "TP5_H5")
        actual = summary(normal if intent == LAYERS[0] else board)
        if (actual["tp5_closed_n"] != int(baseline["n_closed"])
                or abs(actual["tp5_net_mean"] - float(baseline["net_mean"])) > 1e-12):
            quality_failures.append("PARENT_TP5_RECONCILIATION")
        with (ROBUST / file_name).open(encoding="utf-8", newline="") as stream:
            baselines = {x["exit_contract_id"]: x for x in csv.DictReader(stream) if x["dimension"] == "ALL"}
        for exit_id in EXITS:
            partition = [r for r in all_exits if r["population"] == intent and r["period"] == "ALL"
                         and r["dimension"] == "MARKET_RISK_REGIME" and r["exit_contract_id"] == exit_id]
            total = sum(int(r["n_closed"]) for r in partition)
            weighted = sum(r["net_mean"] * r["n_closed"] for r in partition if r["net_mean"] is not None) / total
            if (total != int(baselines[exit_id]["n_closed"])
                    or abs(weighted - float(baselines[exit_id]["net_mean"])) > 1e-12):
                quality_failures.append("PARENT_ALL_EXIT_RECONCILIATION")
    theme = json.loads((PUBLIC / "theme_context_data_qualification.json").read_text(encoding="utf-8"))
    if (theme["status"] != "THEME_CONTEXT_DEFERRED_DATA_UNQUALIFIED"
            or len(shortlist["candidates"]) > 3):
        quality_failures.append("THEME_OR_HYPOTHESIS_BOUNDARY")
    receipt = {"status": "PASS" if not quality_failures else "FAIL", "failures": sorted(set(quality_failures)),
               "entry_n": len(rows), "normal_n": len(normal), "board_n": len(board),
               "feature_count": len(PRIMARY_FEATURES), "trade_keys": len(trades),
               "event_join_n": len(rows), "feature_source_max_date": "ENTRY_T_CLOSE",
               "post_outcome_exploratory": True, "board_conditional_only": True,
               "theme_deferred": True, "tested_numeric_features": list(PRIMARY_FEATURES),
               "tested_regimes": ["MARKET_RISK_REGIME", "STYLE_REGIME"],
               "tested_interactions": ["MARKET_RISK_REGIME_X_STYLE_REGIME"],
               "theme_interactions": "NOT_TESTED_DATA_UNQUALIFIED",
               "no_parameter_optimization": True, "no_population_reselection": True}
    if quality_failures:
        raise ValueError(f"Context quality failed: {quality_failures}")
    outputs = {"market_context_univariate_summary.csv": market,
               "style_context_summary.csv": style,
               "market_style_regime_summary.csv": regimes,
               "context_all_exits_regime_summary.csv": all_exits,
               "context_interaction_summary.csv": interaction,
               "context_temporal_robustness.csv": temporal}
    prior = {name: digest(PUBLIC / name) for name in outputs if (PUBLIC / name).exists()}
    prior.update({name: digest(PUBLIC / name) for name in (
        "context_cluster_bootstrap.json", "context_hypothesis_candidates.json",
        "context_discovery_quality_receipt.json") if (PUBLIC / name).exists()})
    for name, data in outputs.items():
        write_csv(PUBLIC / name, data)
    write_json(PUBLIC / "context_cluster_bootstrap.json", {
        "draws": 2000, "unit": "security_id", "contrasts": boot,
        "interpretation": "Descriptive within-sample uncertainty only; 2026 has prior outcome exposure"})
    write_json(PUBLIC / "context_hypothesis_candidates.json", shortlist)
    write_json(PUBLIC / "context_discovery_quality_receipt.json", receipt)
    complete_prior = len(prior) == len(outputs) + 3
    repeat = complete_prior and all(digest(PUBLIC / name) == previous for name, previous in prior.items())
    write_json(PUBLIC / "context_discovery_reproducibility.json", {
        "status": "PASS" if repeat else "NOT_YET_RERUN" if not complete_prior else "FAIL",
        "compared_public_artifact_n": len(prior), "all_prior_output_hashes_match": repeat,
        "private_feature_parquet_sha256": digest(PRIVATE),
        "meaning": "Same frozen inputs/code yield byte-identical public assessment outputs; feature builder separately rerun for private Parquet"})
    test_path = PUBLIC / "context_test_receipt.json"
    tests = json.loads(test_path.read_text(encoding="utf-8")) if test_path.exists() else {"status": "PENDING"}
    names = [*outputs, "context_cluster_bootstrap.json", "context_hypothesis_candidates.json",
             "context_discovery_quality_receipt.json", "context_discovery_reproducibility.json",
             "context_feature_contract_v1.json", "context_feature_inventory.csv", "context_bins_v1.json",
             "context_feature_builder_receipt.json", "theme_context_data_qualification.json"]
    if test_path.exists():
        names.append(test_path.name)
    manifest = {"run_id": "MARKET_STYLE_THEME_CONTEXT_DISCOVERY_RUN_V1", "status": "MATERIALIZED",
                "parent_run_id": parent["run_id"], "parent_manifest_sha256": PARENT_SHA,
                "entry_population_sha256": parent["entry_population_sha256"],
                "snapshot_manifest_sha256": parent["snapshot_manifest_sha256"],
                "frozen_population": {"all": 505, "normal": 242, "board_conditional": 263},
                "frozen_outcomes": {"event_parquet_sha256": digest(EVENTS), "trade_parquet_sha256": digest(TRADES),
                                    "primary": "TP5_H5_NET", "auxiliary": ["TIME_H1", "TIME_H3", "TIME_H5", "H5_SIGNAL", "LEFT_TAIL_H5_10", "LEFT_TAIL_H5_20", "FAILED_REBOUND_TP5"]},
                "feature_list": list(PRIMARY_FEATURES), "feature_contract_sha256": digest(PUBLIC / "context_feature_contract_v1.json"),
                "bins_sha256": digest(PUBLIC / "context_bins_v1.json"),
                "regimes": ["MARKET_RISK_REGIME_V1", "STYLE_REGIME_V1"],
                "interactions": ["MARKET_RISK_REGIME_X_STYLE_REGIME"],
                "hypothesis_admission": contract["hypothesis_admission"],
                "multiple_testing": contract["multiple_testing"],
                "theme_status": theme["status"], "market_style_status": shortlist["status"],
                "validation_grade": shortlist["validation_grade"],
                "code_sha256": {name: digest(ROOT / name) for name in (
                    "research/context_features_v1.py", "research/context_discovery_v1.py",
                    "scripts/build_entry_context_features_v1.py", "scripts/run_context_discovery_v1.py")},
                "private_artifacts": json.loads((PUBLIC / "context_feature_builder_receipt.json").read_text(
                    encoding="utf-8"))["parquet_sha256"],
                "public_artifacts": {name: digest(PUBLIC / name) for name in names},
                "tests": tests,
                "runtime": {"python": platform.python_version(), "duckdb": duckdb.__version__, "pandas": pd.__version__}}
    write_json(PUBLIC / "run_manifest.json", manifest)
    return {"quality": receipt["status"], "reproducibility": "PASS" if repeat else "PENDING_OR_FAIL",
            "market_style_status": shortlist["status"], "theme_status": theme["status"],
            "candidate_n": len(shortlist["candidates"]), "manifest_sha256": digest(PUBLIC / "run_manifest.json")}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, sort_keys=True, indent=2))
