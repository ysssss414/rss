"""Independent frozen-Entry feature materialization: no outcome/PnL imports."""

from __future__ import annotations

import csv
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb
import pandas as pd

from research.pre_entry_features_v1 import FEATURES, build_features
from research.snapshot import FrozenResearchSnapshot


SMOKE = ROOT / "artifacts/stage1_real_strategy_smoke_v1"
SNAPSHOT = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
PUBLIC = ROOT / "artifacts/stage1_exit_robustness_v1"
PRIVATE = ROOT / ".local_research_data/stage1_exit_robustness_v1"
ENTRY_SHA = "82d16ddf0f3c1af3c5015dd34ae08b9bbe83e23ed90c03b2c8b7ade7ec4bb0f1"
OBS_SHA = "d811b3f150e0aeb22c33d7b7d4fe4f0d07fcbbae4dee9715bb49d5975f7e6153"
SNAPSHOT_SHA = "a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67"


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _date(value: object) -> str:
    return pd.Timestamp(value).date().isoformat()


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _json(path: Path, value: object) -> None:
    path.write_bytes((json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                 allow_nan=False) + "\n").encode("utf-8"))


def main() -> dict:
    contract = json.loads((PUBLIC / "pre_entry_feature_contract_v1.json").read_text(encoding="utf-8"))
    if (contract["id"] != "ENTRY_PRE_SIGNAL_FEATURE_CONTRACT_V1"
            or tuple(contract["features"]) != FEATURES):
        raise ValueError("BLOCKED_EDGE_FEATURE_LOOKAHEAD: feature contract mismatch")
    manifest = json.loads((SMOKE / "run_manifest.json").read_text(encoding="utf-8"))
    if (manifest["run_id"] != "REAL_STRATEGY_SMOKE_RUN_V1"
            or manifest["files"]["entries"]["sha256"] != ENTRY_SHA
            or manifest["files"]["observations"]["sha256"] != OBS_SHA
            or digest(SMOKE / "entries.csv") != ENTRY_SHA
            or digest(SMOKE / "observations.csv") != OBS_SHA):
        raise ValueError("BLOCKED_EDGE_POPULATION_DRIFT")
    entries = _csv(SMOKE / "entries.csv")
    observations = {(x["security_id"], x["observation_instance_id"]): x
                    for x in _csv(SMOKE / "observations.csv")}
    if (len(entries) != 505 or len(observations) != 993
            or sum(x["execution_intent"] == "NORMAL_CLOSE_INTENT" for x in entries) != 242
            or sum(x["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT" for x in entries) != 263):
        raise ValueError("BLOCKED_EDGE_POPULATION_DRIFT: feature source count")
    with FrozenResearchSnapshot(SNAPSHOT) as snapshot:
        if (snapshot.validation["manifest_sha256"] != SNAPSHOT_SHA
                or snapshot.manifest["cutoff_date"] != "2026-09-23"):
            raise ValueError("BLOCKED_EDGE_FEATURE_LOOKAHEAD: snapshot identity")
        codes = sorted({x["security_id"] for x in entries})
        calendar = [_date(x) for x in snapshot.load_calendar().trade_date]
        bars = {(str(x["security_id"]), _date(x["trade_date"])): x
                for x in snapshot.load_daily_bars(codes).to_dict("records")}
        rows = build_features(entries, observations, bars, calendar)
    if (len(rows) != 505 or any(x["feature_source_max_date"] != x["entry_date"]
                                or x["trigger_date"] > x["entry_date"] for x in rows)
            or any(any(bad in key.lower() for bad in ("outcome", "pnl", "mfe", "mae", "d8", "h1", "h3", "h5"))
                   for key in rows[0])):
        raise ValueError("BLOCKED_EDGE_FEATURE_LOOKAHEAD: output schema or date")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    parquet = PRIVATE / "entry_pre_signal_features_v1.parquet"
    old = json.loads((PUBLIC / "feature_builder_receipt.json").read_text(encoding="utf-8")) if (
        PUBLIC / "feature_builder_receipt.json").exists() else {}
    code_sha = {name: digest(ROOT / name) for name in (
        "research/pre_entry_features_v1.py", "scripts/build_pre_entry_features_v1.py")}
    prior = digest(parquet) if parquet.exists() and old.get("code_sha256") == code_sha else None
    con = duckdb.connect()
    try:
        con.register("features", pd.DataFrame(rows))
        con.execute("COPY (SELECT * FROM features) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(parquet)])
    finally:
        con.close()
    if prior is not None and digest(parquet) != prior:
        raise ValueError("BLOCKED_EDGE_ROBUSTNESS_NONDETERMINISTIC: feature parquet")
    inventory = []
    for name in FEATURES:
        values = [r[name] for r in rows]
        inventory.append({"feature": name, "source_max_date": "ENTRY_T_CLOSE",
                          "n_available": sum(v is not None for v in values),
                          "n_missing": sum(v is None for v in values),
                          "tested_bins": json.dumps(contract["diagnostic_bins"][name], separators=(",", ":")),
                          "diagnostic_status": "TESTED_UNIVARIATE_EXPLORATORY"})
    with (PUBLIC / "pre_entry_feature_inventory.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(inventory[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(inventory)
    receipt = {"id": "ENTRY_PRE_SIGNAL_FEATURES_V1", "status": "PASS",
               "entry_n": 505, "unique_event_ids": len({r["entry_event_id"] for r in rows}),
               "max_source_date": "ENTRY_T_CLOSE", "forbidden_feature_data_loaded": False,
               "snapshot_integrity_note": "Opening the snapshot hashes all constituent files, including D8, but this builder loads only calendar and D3 daily bars as economic rows.",
               "source_entry_sha256": ENTRY_SHA, "source_observation_sha256": OBS_SHA,
               "snapshot_manifest_sha256": SNAPSHOT_SHA,
               "feature_contract_sha256": digest(PUBLIC / "pre_entry_feature_contract_v1.json"),
               "code_sha256": code_sha, "private_parquet_sha256": digest(parquet),
               "inventory_sha256": digest(PUBLIC / "pre_entry_feature_inventory.csv")}
    _json(PUBLIC / "feature_builder_receipt.json", receipt)
    return receipt


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, sort_keys=True, indent=2))
