"""Characterize the frozen 505 Entry signals; never regenerate or trade them."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
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

from research.entry_event_outcome_v1 import HORIZONS, MAX_HORIZON, evaluate_event
from research.snapshot import FrozenResearchSnapshot


SMOKE = ROOT / "artifacts/stage1_real_strategy_smoke_v1"
SNAPSHOT = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
PRIVATE = ROOT / ".local_research_data/stage1_entry_event_study_v1"
PUBLIC = ROOT / "artifacts/stage1_entry_event_study_v1"
SNAPSHOT_SHA = "a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67"
ENTRY_SHA = "82d16ddf0f3c1af3c5015dd34ae08b9bbe83e23ed90c03b2c8b7ade7ec4bb0f1"
OBS_SHA = "d811b3f150e0aeb22c33d7b7d4fe4f0d07fcbbae4dee9715bb49d5975f7e6153"
CUTOFF = "2026-09-23"


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def write_json(path: Path, value: object) -> None:
    path.write_bytes(json_bytes(value))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, object]], columns: list[str]) -> None:
    # A fixed LF encoding makes output hashes independent of the host newline mode.
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def validate_population() -> tuple[dict[str, object], list[dict[str, str]], dict[tuple[str, str], dict[str, str]]]:
    manifest = json.loads((SMOKE / "run_manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("run_id") != "REAL_STRATEGY_SMOKE_RUN_V1"
            or manifest.get("snapshot_id") != "REAL_RESEARCH_SNAPSHOT_V1"
            or manifest.get("snapshot_manifest_sha256") != SNAPSHOT_SHA
            or manifest.get("config") != {
                "cutoff": CUTOFF, "lookback_valid_observations": 5,
                "observation_horizon_exchange_sessions": 7,
                "target_boards": ["SSE Main", "SZSE Main"],
                "truncated_rsi_minimum": 120, "truncated_rsi_target": 150}
            or manifest.get("counts", {}).get("entry") != {
                "ENTRY_A_PULLBACK_TO_MA5_V1": 426, "ENTRY_B_RSI_RECROSS_70_V1": 79}
            or manifest.get("counts", {}).get("intent") != {
                "LIMIT_UP_CLOSE_BOARD_INTENT": 263, "NORMAL_CLOSE_INTENT": 242}
            or manifest.get("counts", {}).get("formal_observation_count") != 993
            or not {"ENTRY_A_PULLBACK_TO_MA5_V1", "ENTRY_B_RSI_RECROSS_70_V1"} <= set(manifest.get("contracts", []))
            or manifest.get("outcomes_accessed") is not False
            or manifest.get("d8_accessed") is not False):
        raise ValueError("BLOCKED_EVENT_POPULATION_MISMATCH: smoke identity/config changed")
    for name, expected in (("entries", ENTRY_SHA), ("observations", OBS_SHA)):
        item = manifest["files"][name]
        if item["sha256"] != expected or digest(SMOKE / item["path"]) != expected:
            raise ValueError(f"BLOCKED_EVENT_POPULATION_MISMATCH: {name} hash changed")
    for path, expected in manifest["code_sha256"].items():
        if digest(ROOT / path) != expected:
            raise ValueError(f"BLOCKED_EVENT_POPULATION_MISMATCH: smoke code changed: {path}")
    entries = read_csv(SMOKE / "entries.csv")
    observations = read_csv(SMOKE / "observations.csv")
    obs = {(x["security_id"], x["observation_instance_id"]): x for x in observations}
    keys = {(x["security_id"], x["entry_date"], x["observation_instance_id"]) for x in entries}
    entry_counts = Counter(x["entry_reasons"] for x in entries)
    intent_counts = Counter(x["execution_intent"] for x in entries)
    if (len(entries) != 505 or len(keys) != 505 or len(observations) != 993
            or len(obs) != 993 or entry_counts != {"ENTRY_A_PULLBACK_TO_MA5_V1": 426,
                                                    "ENTRY_B_RSI_RECROSS_70_V1": 79}
            or intent_counts != {"LIMIT_UP_CLOSE_BOARD_INTENT": 263,
                                 "NORMAL_CLOSE_INTENT": 242}
            or any((x["security_id"], x["observation_instance_id"]) not in obs for x in entries)
            or int(manifest["files"]["entries"]["count"]) != 505
            or int(manifest["files"]["observations"]["count"]) != 993):
        raise ValueError("BLOCKED_EVENT_POPULATION_MISMATCH: formal Entry cohort changed")
    return manifest, entries, obs


def _date(value: object) -> str:
    return pd.Timestamp(value).date().isoformat()


def _records(frame: pd.DataFrame, date_column: str) -> dict[tuple[str, str], dict[str, object]]:
    return {(str(row["security_id"]), _date(row[date_column])): row
            for row in frame.to_dict("records")}


def _quantile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lo = int(position)
    return ordered[lo] + (ordered[min(lo + 1, len(ordered) - 1)] - ordered[lo]) * (position - lo)


def distribution(values: list[float]) -> dict[str, object]:
    return {"n": len(values), "mean": statistics.fmean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "std_population": statistics.pstdev(values) if values else None,
            **{f"p{int(p * 100)}": _quantile(values, p) for p in (.1, .25, .5, .75, .9)}}


def summarize_group(rows: list[dict[str, object]], h: int) -> dict[str, object]:
    prefix = f"h{h}_"
    available = [r for r in rows if r[prefix + "outcome_status"] == "AVAILABLE"]
    returns = [r[prefix + "signal_forward_close_return"] for r in available]
    statuses = Counter(r[prefix + "outcome_status"] for r in rows)
    return {"horizon": h, "n_total": len(rows), "n_available": len(available),
            "n_right_censored": statuses["RIGHT_CENSORED"],
            "n_no_valid_quote": statuses["NO_VALID_QUOTE"],
            "n_other_missing": len(rows) - len(available) - statuses["RIGHT_CENSORED"]
                               - statuses["NO_VALID_QUOTE"],
            "n_adjustment_unresolved": statuses["ADJUSTMENT_UNRESOLVED"],
            "n_terminal": statuses["TERMINAL_NO_QUOTE"],
            **distribution(returns),
            "positive_fraction": sum(x > 0 for x in returns) / len(returns) if returns else None,
            "zero_fraction": sum(x == 0 for x in returns) / len(returns) if returns else None,
            "negative_fraction": sum(x < 0 for x in returns) / len(returns) if returns else None,
            "small_sample_flag": "SMALL_SAMPLE_DESCRIPTIVE_ONLY" if len(rows) < 20 else ""}


def _excursion(rows: list[dict[str, object]], h: int, side: str) -> dict[str, object]:
    metric = f"h{h}_signal_forward_{side}"
    values = [r[metric] for r in rows if r[metric] is not None]
    source_sessions = Counter(r[f"h{h}_{side}_source_session"] for r in rows
                              if r[f"h{h}_{side}_source_session"] is not None)
    return {"horizon": h, "metric": side.upper(), **distribution(values),
            "source_session_distribution": json.dumps(dict(sorted(source_sessions.items())),
                                                      sort_keys=True, separators=(",", ":"))}


def _groups(rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    selectors = {
        "ENTRY_TYPE": ("ENTRY_A_PULLBACK_TO_MA5_V1", "ENTRY_B_RSI_RECROSS_70_V1"),
        "OBSERVATION_STRENGTH": ("4/5", "5/5"),
        "EXECUTION_INTENT": ("LIMIT_UP_CLOSE_BOARD_INTENT", "NORMAL_CLOSE_INTENT"),
        "TRIGGER_QUALIFICATION": ("QUALIFIED_BY_V3_PRICE_PATH", "QUALIFIED_BY_OPERATIONAL_STATUS"),
        "RSI_PROVENANCE": ("TRUNCATED_SLICE_150_NONCANONICAL", "FULL_AVAILABLE_PREFIX"),
    }
    field = {"ENTRY_TYPE": "entry_type", "OBSERVATION_STRENGTH": "observation_pattern",
             "EXECUTION_INTENT": "execution_intent", "TRIGGER_QUALIFICATION": "trigger_qualification_method",
             "RSI_PROVENANCE": "rsi_provenance"}
    return {f"{dimension}:{value}": [r for r in rows if r[field[dimension]] == value]
            for dimension, values in selectors.items() for value in values}


def _case_audit(rows: list[dict[str, object]], path: list[dict[str, object]]) -> dict[str, object]:
    ordered = sorted(rows, key=lambda r: r["entry_event_id"])
    paths = defaultdict(list)
    for item in path:
        paths[item["entry_event_id"]].append(item)
    filters = {
        "entry_a": lambda r: r["entry_type"] == "ENTRY_A_PULLBACK_TO_MA5_V1",
        "entry_b": lambda r: r["entry_type"] == "ENTRY_B_RSI_RECROSS_70_V1",
        "board_intent": lambda r: r["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT",
        "normal_intent": lambda r: r["execution_intent"] == "NORMAL_CLOSE_INTENT",
        "four_of_five": lambda r: r["observation_pattern"] == "4/5",
        "five_of_five": lambda r: r["observation_pattern"] == "5/5",
        "positive_h20": lambda r: r["h20_signal_forward_close_return"] is not None
                                  and r["h20_signal_forward_close_return"] > 0,
        "negative_h20": lambda r: r["h20_signal_forward_close_return"] is not None
                                  and r["h20_signal_forward_close_return"] < 0,
        "suspension": lambda r: any(x["missing_reason"] == "SUSPENDED" for x in paths[r["entry_event_id"]]),
        "right_censor": lambda r: r["h20_outcome_status"] == "RIGHT_CENSORED",
        "corporate_action": lambda r: any(x["d8_event_kind"] for x in paths[r["entry_event_id"]]),
        "operational_trigger": lambda r: r["trigger_qualification_method"] == "QUALIFIED_BY_OPERATIONAL_STATUS",
        "v3_trigger": lambda r: r["trigger_qualification_method"] == "QUALIFIED_BY_V3_PRICE_PATH",
    }
    result = {}
    for name, predicate in filters.items():
        selected = next((r for r in ordered if predicate(r)), None)
        result[name] = _case(selected, paths) if selected else None
    ranked = {
        "large_mfe_weak_close": (
            lambda r: r["h20_signal_forward_mfe"] >= .05 and r["h20_signal_forward_close_return"] <= 0,
            lambda r: r["h20_signal_forward_mfe"] - r["h20_signal_forward_close_return"]),
        "large_mae_recovery": (
            lambda r: r["h20_signal_forward_mae"] <= -.05 and r["h20_signal_forward_close_return"] > 0,
            lambda r: r["h20_signal_forward_close_return"] - r["h20_signal_forward_mae"]),
    }
    for name, (qualifies, score) in ranked.items():
        candidates = [r for r in ordered if r["h20_signal_forward_close_return"] is not None
                      and r["h20_signal_forward_mfe"] is not None
                      and r["h20_signal_forward_mae"] is not None and qualifies(r)]
        selected = max(candidates, key=lambda r: (score(r), r["entry_event_id"])) if candidates else None
        result[name] = _case(selected, paths) if selected else None
    return {"case_selection": "deterministic first identity per category; weak close requires H20 MFE>=5% and return<=0; recovery requires MAE<=-5% and return>0; two extremes ranked on H20 dispersion",
            "cases": result}


def _case(row: dict[str, object], paths: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    return {"entry_event_id": row["entry_event_id"], "security_id": row["security_id"],
            "signal_date": row["entry_date"], "signal_anchor_close": row["signal_anchor_close"],
            "entry_type": row["entry_type"], "execution_intent": row["execution_intent"],
            "observation_pattern": row["observation_pattern"],
            "trigger_qualification_method": row["trigger_qualification_method"],
            "rsi_provenance": row["rsi_provenance"],
            "d8_events": [{"date": x["session_date"], "kind": x["d8_event_kind"],
                           "factor": x["cumulative_d8_factor"]}
                          for x in paths[row["entry_event_id"]] if x["d8_event_kind"]],
            "horizons": {f"H{h}": {key: row[f"h{h}_{key}"] for key in (
                "session_date", "outcome_status", "missing_reason", "raw_future_close",
                "comparable_close", "signal_forward_close_return", "signal_forward_mfe",
                "signal_forward_mae", "mfe_source_date", "mae_source_date", "valid_session_count")}
                         for h in HORIZONS}}


def _quality(rows: list[dict[str, object]], path: list[dict[str, object]], cutoff: str) -> dict[str, object]:
    ids = [r["entry_event_id"] for r in rows]
    failures = []
    if len(rows) != 505 or len(set(ids)) != 505 or any(r["signal_anchor_close"] <= 0 for r in rows):
        failures.append("POPULATION_OR_ANCHOR")
    if len(path) != 505 * MAX_HORIZON:
        failures.append("PATH_GRAIN")
    by_id = defaultdict(list)
    for item in path:
        by_id[item["entry_event_id"]].append(item)
        day = item["session_date"]
        if day and day > cutoff:
            failures.append("FUTURE_BEYOND_CUTOFF")
        if item["path_status"] == "AVAILABLE":
            if (item["comparable_low"] <= 0 or item["comparable_close"] <= 0
                    or item["comparable_high"] <= 0
                    or not item["comparable_low"] <= item["comparable_close"] <= item["comparable_high"]
                    or item["cumulative_d8_factor"] <= 0):
                failures.append("INVALID_COMPARABLE_PRICE")
        elif item["comparable_close"] is not None:
            failures.append("NONAVAILABLE_HAS_COMPARABLE_PRICE")
    for row in rows:
        items = by_id[row["entry_event_id"]]
        if [x["horizon"] for x in items] != list(range(1, MAX_HORIZON + 1)):
            failures.append("HORIZON_ORDER")
        dated = [x["session_date"] for x in items if x["session_date"]]
        if dated != sorted(set(dated)) or any(d <= row["entry_date"] for d in dated):
            failures.append("EVENT_CLOCK")
        for h in HORIZONS:
            p = f"h{h}_"
            if row[p + "outcome_status"] != "AVAILABLE" and row[p + "signal_forward_close_return"] is not None:
                failures.append("MISSING_RETURN_IMPUTED")
            if row[p + "outcome_status"] == "RIGHT_CENSORED" and row[p + "session_date"] is not None:
                failures.append("CENSORING_DATE")
            if row[p + "valid_session_count"] > h:
                failures.append("EXCURSION_OBSERVATION_COUNT")
            if row[p + "outcome_status"] == "AVAILABLE" and (
                    row[p + "signal_forward_mfe"] is None or row[p + "signal_forward_mae"] is None
                    or row[p + "signal_forward_mfe"] < row[p + "signal_forward_close_return"]
                    or row[p + "signal_forward_mae"] > row[p + "signal_forward_close_return"]):
                failures.append("ENDPOINT_OUTSIDE_EXCURSION_BOUNDS")
            if row[p + "outcome_status"] in {"RIGHT_CENSORED", "ADJUSTMENT_UNRESOLVED"} and (
                    row[p + "signal_forward_mfe"] is not None or row[p + "signal_forward_mae"] is not None):
                failures.append("UNQUALIFIED_EXCURSION")
    return {"status": "PASS" if not failures else "FAIL", "failures": sorted(set(failures)),
            "entry_rows": len(rows), "unique_event_ids": len(set(ids)), "daily_path_rows": len(path),
            "anchor_nulls": sum(r["signal_anchor_close"] is None for r in rows),
            "signal_firewall": "outcome reader consumes frozen CSV; signal modules unchanged; D8 loaded only here"}


def _parquet(path: Path, rows: list[dict[str, object]]) -> None:
    con = duckdb.connect()
    try:
        con.register("event_rows", pd.DataFrame(rows))
        # Preserve fixed row order; avoid platform-specific DataFrame parquet metadata.
        con.execute("COPY (SELECT * FROM event_rows) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(path)])
    finally:
        con.close()


def main(*, focused_tests: str = "NOT_RUN", full_suite: str = "NOT_RUN") -> dict[str, object]:
    parent, entries, obs = validate_population()
    with FrozenResearchSnapshot(SNAPSHOT) as snapshot:
        if (snapshot.validation["manifest_sha256"] != SNAPSHOT_SHA
                or snapshot.manifest["cutoff_date"] != CUTOFF
                or snapshot.manifest["pit_policy"]["d8_contract"] != "ENTRY_COMPARABLE_FORWARD_PATH_V1"
                or snapshot.manifest["pit_policy"]["d8_usage"] != "OUTCOME_ONLY_NEVER_SIGNAL"
                or snapshot.manifest["quality"]["store_quality"]["outcome_adjustment"]["canonical_status"] != "PASS"):
            raise ValueError("BLOCKED_ENTRY_OUTCOME_COMPARABILITY: frozen D8 contract changed")
        codes = sorted({x["security_id"] for x in entries})
        sessions = tuple(_date(x) for x in snapshot.load_calendar().trade_date)
        bars = _records(snapshot.load_daily_bars(codes), "trade_date")
        statuses = _records(snapshot.load_daily_status(codes), "trade_date")
        d8 = snapshot.load_d8(codes)
        universe = {str(r["security_id"]): r for r in snapshot.load_universe(codes).to_dict("records")}
        exclusion_files = [str(snapshot.root / x["path"]) for x in
                           snapshot.manifest["datasets"]["outcome_adjustment_exclusions"]["files"]]
        excluded = snapshot.con.execute(
            "SELECT security_id, effective_date, reason FROM read_parquet(?) "
            "WHERE security_id IN (SELECT UNNEST(?)) ORDER BY security_id,effective_date",
            [exclusion_files, codes]).df()
        actions_by_code: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
        for item in d8.to_dict("records"):
            actions_by_code[str(item["security_id"])][_date(item["effective_date"])] = {
                **item, "known_date": _date(item["known_date"])}
        exclusions_by_code: dict[str, dict[str, str]] = defaultdict(dict)
        for item in excluded.to_dict("records"):
            exclusions_by_code[str(item["security_id"])][_date(item["effective_date"])] = str(item["reason"])
        rows, path = [], []
        for entry in sorted(entries, key=lambda x: (x["security_id"], x["entry_date"], x["observation_instance_id"])):
            code, day = entry["security_id"], entry["entry_date"]
            anchor_bar = bars.get((code, day))
            if anchor_bar is None or str(anchor_bar["close"]) != entry["raw_close"]:
                raise ValueError("BLOCKED_EVENT_POPULATION_MISMATCH: Entry anchor differs from D3")
            source = obs[(code, entry["observation_instance_id"])]
            delisted = universe[code]["delisting_date"]
            entry_actions = {k: v for k, v in actions_by_code[code].items() if k > day}
            entry_exclusions = {k: v for k, v in exclusions_by_code[code].items() if k > day}
            outcome, daily = evaluate_event(
                security_id=code, entry_date=day, observation_id=entry["observation_instance_id"],
                anchor=anchor_bar["close"], sessions=sessions, bars=bars, statuses=statuses,
                actions=entry_actions, exclusions=entry_exclusions,
                delisting_date=None if pd.isna(delisted) else _date(delisted))
            rows.append({"entry_event_id": outcome["entry_event_id"],
                         "observation_id": entry["observation_instance_id"],
                         "security_id": code, "entry_date": day,
                         "entry_type": entry["entry_reasons"], "execution_intent": entry["execution_intent"],
                         "observation_pattern": f'{source["v3_lookback_hits"]}/5',
                         "limit_up_count": int(source["v3_lookback_hits"]),
                         "trigger_qualification_method": source["trigger_regime_qualification_method"],
                         "rsi_provenance": source["rsi_replay_mode"],
                         "snapshot_id": snapshot.snapshot_id, "smoke_run_id": parent["run_id"],
                         "entry_contract_versions": "|".join(x for x in parent["contracts"] if x.startswith("ENTRY_")),
                         **{k: v for k, v in outcome.items() if k != "entry_event_id"}})
            path.extend(daily)
    quality = _quality(rows, path, CUTOFF)
    if digest(SMOKE / "entries.csv") != ENTRY_SHA or digest(SMOKE / "observations.csv") != OBS_SHA:
        raise ValueError("BLOCKED_EVENT_STUDY_SIGNAL_CONTAMINATION: frozen CSV changed during outcome run")
    if quality["status"] != "PASS":
        raise ValueError(f"BLOCKED_ENTRY_EVENT_STUDY_COVERAGE: {quality['failures']}")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    outcome_file = PRIVATE / "entry_event_outcome_v1.parquet"
    path_file = PRIVATE / "entry_forward_path_h1_h20_v1.parquet"
    # Compare bytes only across reruns of the same code; development changes rebaseline.
    code_sha = {name: digest(ROOT / name) for name in (
        "research/entry_event_outcome_v1.py", "scripts/run_entry_event_study_v1.py")}
    old_manifest_path = PUBLIC / "run_manifest.json"
    old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8")) if old_manifest_path.exists() else {}
    prior = ({p.name: digest(p) for p in (outcome_file, path_file) if p.exists()}
             if old_manifest.get("code_sha256") == code_sha else {})
    _parquet(outcome_file, rows)
    _parquet(path_file, path)
    if any(digest(PRIVATE / name) != previous for name, previous in prior.items()):
        raise ValueError("BLOCKED_ENTRY_EVENT_STUDY_NONDETERMINISTIC: parquet changed on rerun")
    overall = [summarize_group(rows, h) for h in HORIZONS]
    groups = _groups(rows)
    for dimension in ("ENTRY_TYPE", "OBSERVATION_STRENGTH", "EXECUTION_INTENT",
                      "TRIGGER_QUALIFICATION", "RSI_PROVENANCE"):
        if sum(len(selected) for name, selected in groups.items()
               if name.startswith(dimension + ":")) != len(rows):
            raise ValueError(f"BLOCKED_ENTRY_EVENT_STUDY_COVERAGE: unknown {dimension} provenance")
    subgroup = [{"dimension": name.split(":", 1)[0], "value": name.split(":", 1)[1],
                 **summarize_group(selected, h)} for name, selected in groups.items() for h in HORIZONS]
    excursion = [_excursion(rows, h, side) for h in HORIZONS for side in ("mfe", "mae")]
    sensitivity = [{"population": label, **summarize_group(selected, h),
                    "mean_mfe": distribution([r[f"h{h}_signal_forward_mfe"] for r in selected
                                              if r[f"h{h}_signal_forward_mfe"] is not None])["mean"],
                    "mean_mae": distribution([r[f"h{h}_signal_forward_mae"] for r in selected
                                              if r[f"h{h}_signal_forward_mae"] is not None])["mean"]}
                   for label, selected in [("ALL", rows),
                       ("V3_PRICE_PATH", groups["TRIGGER_QUALIFICATION:QUALIFIED_BY_V3_PRICE_PATH"]),
                       ("OPERATIONAL_STATUS", groups["TRIGGER_QUALIFICATION:QUALIFIED_BY_OPERATIONAL_STATUS"]),
                       ("FULL_PREFIX_RSI", groups["RSI_PROVENANCE:FULL_AVAILABLE_PREFIX"]),
                       ("TRUNCATED_RSI", groups["RSI_PROVENANCE:TRUNCATED_SLICE_150_NONCANONICAL"])]
                   for h in HORIZONS]
    write_csv(PUBLIC / "entry_event_horizon_summary.csv", overall, list(overall[0]))
    write_csv(PUBLIC / "entry_event_subgroup_summary.csv", subgroup, list(subgroup[0]))
    write_csv(PUBLIC / "entry_event_path_excursion_summary.csv", excursion, list(excursion[0]))
    write_csv(PUBLIC / "entry_event_provenance_sensitivity.csv", sensitivity, list(sensitivity[0]))
    counts = Counter(r["security_id"] for r in rows)
    years = {str(y): {"entry_n": sum(r["entry_date"].startswith(str(y)) for r in rows),
                      **{f"h{h}_available_n": sum(r["entry_date"].startswith(str(y))
                                               and r[f"h{h}_outcome_status"] == "AVAILABLE" for r in rows)
                         for h in HORIZONS}} for y in (2024, 2025, 2026)}
    coverage = {f"H{h}": {"total": summary["n_total"], "available": summary["n_available"],
                          "right_censored": summary["n_right_censored"],
                          "no_valid_quote": summary["n_no_valid_quote"],
                          "other_missing": summary["n_other_missing"],
                          "adjustment_unresolved": summary["n_adjustment_unresolved"],
                          "terminal": summary["n_terminal"]} for h, summary in zip(HORIZONS, overall)}
    write_json(PUBLIC / "entry_event_outcome_coverage.json", coverage)
    write_json(PUBLIC / "entry_event_study_summary.json", {
        "run_id": "ENTRY_EVENT_STUDY_RUN_V1", "entry_event_n": len(rows),
        "unique_security_n": len(counts),
        "securities_with_multiple_entries": sum(n > 1 for n in counts.values()),
        "repeated_event_n": len(rows) - len(counts), "max_entries_per_security": max(counts.values()),
        "years": years, "horizons": overall,
        "interpretation": "signal price outcomes only; no fills, Exit, costs, PnL or edge verdict"})
    write_json(PUBLIC / "entry_event_study_quality_receipt.json", quality)
    write_json(PUBLIC / "entry_event_study_case_audit.json", _case_audit(rows, path))
    contract = {"id": "ENTRY_EVENT_OUTCOME_V1", "parent": parent["run_id"],
                "grain": "one frozen formal Entry signal", "entry_anchor": "D3 raw close on signal date T; not a fill",
                "horizons": list(HORIZONS), "daily_path_horizons": list(range(1, 21)),
                "clock": "Hk is kth exchange trading session strictly after T",
                "price": "raw future OHLC multiplied by cumulative canonical D8 single_factor after T through session; T scale",
                "d8": "ENTRY_COMPARABLE_FORWARD_PATH_V1; OUTCOME_ONLY_NEVER_SIGNAL",
                "d8_exclusion": "first excluded effective event and later sessions are ADJUSTMENT_UNRESOLVED/NULL",
                "cash_dividend_income": "EXCLUDED; price return only",
                "suspension": "NO_VALID_QUOTE/NULL; no forward fill; available sessions only for excursion",
                "right_censor": "each horizon independent; no endpoint after snapshot cutoff; excursions NULL for censored horizon",
                "terminal": "TERMINAL_NO_QUOTE/NULL; no arbitrary liquidation value",
                "statistics": "descriptive event-weighted; population standard deviation; linear-interpolated percentiles",
                "uncertainty": "no iid confidence intervals, p-values, or edge verdict",
                "publication": "event-level and daily-path Parquet remain gitignored locally; public artifacts are aggregates and audits"}
    write_json(PUBLIC / "entry_event_study_contract_v1.json", contract)
    public_files = sorted(p for p in PUBLIC.iterdir() if p.is_file()
                          and p.name not in {"entry_event_study_reproducibility.json", "run_manifest.json"})
    content_sha = sha256(json_bytes({"rows": rows, "path": path})).hexdigest()
    reproducibility = {"status": "PASS" if not prior or all(digest(PRIVATE / name) == previous
                                                                for name, previous in prior.items()) else "FAIL",
                       "semantic_rows_sha256": content_sha, "prior_parquet_sha256": prior,
                       "private_parquet_sha256": {p.name: digest(p) for p in (outcome_file, path_file)},
                       "determinism": "rerun refuses a changed existing parquet hash; JSON/CSV are fixed LF and sorted inputs"}
    write_json(PUBLIC / "entry_event_study_reproducibility.json", reproducibility)
    public_files.append(PUBLIC / "entry_event_study_reproducibility.json")
    manifest = {"run_id": "ENTRY_EVENT_STUDY_RUN_V1", "status": "MATERIALIZED",
                "parent_run_id": parent["run_id"], "parent_manifest_sha256": digest(SMOKE / "run_manifest.json"),
                "snapshot_id": "REAL_RESEARCH_SNAPSHOT_V1", "snapshot_manifest_sha256": SNAPSHOT_SHA,
                "entry_population_sha256": ENTRY_SHA, "observation_sha256": OBS_SHA,
                "entry_count": len(rows), "entry_contract_versions": [x for x in parent["contracts"] if x.startswith("ENTRY_")],
                "outcome_contract": contract["id"], "d8_contract": "ENTRY_COMPARABLE_FORWARD_PATH_V1",
                "horizons": list(HORIZONS), "cutoff": CUTOFF,
                "censoring": contract["right_censor"], "adjustment": contract["price"],
                "config": {"max_horizon": MAX_HORIZON, "anchor": "SIGNAL_T_RAW_CLOSE", "case_selection": "DETERMINISTIC"},
                "code_sha256": code_sha,
                "runtime": {"python": platform.python_version(), "duckdb": duckdb.__version__,
                            "pandas": pd.__version__},
                "tests": {"focused": focused_tests, "full_suite": full_suite},
                "public_artifacts": {p.name: digest(p) for p in sorted(public_files)},
                "private_artifacts": {p.name: digest(p) for p in (outcome_file, path_file)}}
    write_json(PUBLIC / "run_manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    print(json.dumps(main(focused_tests="PASS" if "--focused-pass" in sys.argv else "NOT_RUN",
                          full_suite="ONE_PREEXISTING_FAILURE" if "--full-preexisting" in sys.argv else "NOT_RUN"),
                     ensure_ascii=False, sort_keys=True, indent=2))
