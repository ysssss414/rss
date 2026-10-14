"""Append-only local forward observation; frozen inception never gets overwritten."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import pandas as pd

from research.real_materialization import freeze_private_blob
from research.snapshot import FrozenResearchSnapshot

from .data import stock_frame
from .evaluation import evaluate
from .indicators import calculate, config_hash
from .risk import apply_risk
from .samples import read_samples


def _freeze(path: Path, content: bytes, root: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("Immutable observation conflict: " + str(path))
    else:
        freeze_private_blob(path, content, private_root=root)


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def latest_snapshot(root: Path) -> Path:
    candidates = []
    for path in sorted(root.glob("*/metadata/manifest.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("schema") == "real-research-snapshot/1" and item.get("quality", {}).get("status") == "PASS":
            candidates.append((item["cutoff_date"], str(path.parent.parent), path.parent.parent))
    if not candidates:
        raise ValueError("No qualified local frozen snapshot")
    return max(candidates)[2]


def run_forward(*, args, config, risk_config, provenance) -> int:
    from .cli import MODULE, ROOT
    if args.confirm_days != 3 or not args.as_of:
        raise ValueError("Forward observation requires frozen three-day rule and explicit --as-of")
    as_of = date.fromisoformat(args.as_of).isoformat()
    path = args.snapshot if args.snapshot_explicit else latest_snapshot(ROOT / "data/research_snapshots")
    manifest = json.loads((path / "metadata/manifest.json").read_text(encoding="utf-8"))
    with FrozenResearchSnapshot(path, expected_id=manifest["snapshot_id"]) as snapshot:
        master = snapshot.load_universe()
        samples = [item for item in read_samples(args.samples, master) if item["sample_type"] == "FORWARD_OBSERVATION"]
        for sample in samples:
            if sample["resolution_status"] != "RESOLVED" or as_of < sample["observation_start"]:
                raise ValueError("Unresolved or pre-inception forward observation")
            if not sample["observation_id"].replace("_", "").isalnum():
                raise ValueError("Unsafe forward identity")
            root = MODULE / "artifacts/forward" / sample["observation_id"]
            root.mkdir(parents=True, exist_ok=True)
            frame = stock_frame(snapshot, sample, master)
            frame = frame.loc[frame.trade_date.le(as_of)].copy()
            actual_latest = frame.loc[frame.issue.eq(""), "trade_date"].max() if not frame.empty else None
            actual_latest = None if actual_latest is None or str(actual_latest) == "nan" else actual_latest
            rule_identity = {"risk_rule_version": risk_config["version"], "risk_config_sha256": config_hash(risk_config),
                             "candidate_config_sha256": config_hash(config),
                             "signal_code_sha256": {key: provenance["code_sha256"][key] for key in
                                ("trend_exit_research/src/risk.py", "trend_exit_research/src/indicators.py", "trend_exit_research/src/data.py")}}
            initial_path = root / "initial_receipt.json"
            if initial_path.exists():
                initial = json.loads(initial_path.read_text(encoding="utf-8"))
                if (initial["rule_identity"] != rule_identity or initial["initial_observation_date"] != sample["observation_start"]
                        or initial["security_id"] != sample["resolved_code"] or initial["stock_name"] != sample["stock_name"]):
                    raise ValueError("Frozen forward rules changed; create a separately reviewed observation identity")
            else:
                inception = frame.loc[frame.trade_date.le(sample["observation_start"]) & frame.issue.eq("")]
                initial = {"observation_id": sample["observation_id"], "sample_type": "FORWARD_OBSERVATION",
                    "stock_name": sample["stock_name"], "security_id": sample["resolved_code"],
                    "initial_observation_date": sample["observation_start"], "initial_recorded_date": as_of,
                    "initial_data_as_of": inception.trade_date.max() if not inception.empty else None,
                    "initial_source_snapshot_id": snapshot.snapshot_id,
                    "initial_snapshot_manifest_sha256": snapshot.validation["manifest_sha256"], "rule_identity": rule_identity,
                    "availability_basis": "ASSUMED_15_30_SHANGHAI_NOT_HISTORICAL_PUBLICATION_PROOF",
                    "scope": "Signals before inception are never forward observations"}
                _freeze(initial_path, _bytes(initial), root)
            if frame.empty or not frame.trade_date.ge(sample["observation_start"]).any():
                risk = pd.DataFrame(columns=["security_id", "trade_date", "source_snapshot_id", "risk_rule_version", "POSITION_RISK_STATE"])
                events = pd.DataFrame(columns=["security_id", "trade_date", "risk_rule_version", "signal"])
                outcomes = pd.DataFrame(columns=["security_id", "trade_date", "risk_rule_version", "outcome_only"])
            else:
                indicators, _ = calculate(frame, config)
                risk, events = apply_risk(indicators, risk_config)
                outcomes = evaluate(risk, frame)
            for table in (risk, events, outcomes):
                if not table.empty:
                    table.drop(table.index[table.trade_date.lt(sample["observation_start"])], inplace=True)
                table["observation_id"] = sample["observation_id"]
                table["observation_recorded_date"] = as_of
                if "trade_date" in table:
                    table["capture_status"] = table.trade_date.map(lambda day: "SAME_DAY_CAPTURE" if day == as_of else "LATE_CAPTURE_NOT_SAME_DAY_PROSPECTIVE")
            identity = snapshot.validation["manifest_sha256"]
            target = root / as_of / identity / config_hash(provenance["code_sha256"])[:16]
            outputs = {}
            for name, table in (("daily_risk.csv", risk), ("risk_signals.csv", events), ("daily_evaluation.csv", outcomes)):
                content = table.to_csv(index=False, float_format="%.12g", lineterminator="\n").encode("utf-8-sig")
                _freeze(target / name, content, root)
                outputs[name] = {"rows": len(table), "sha256": sha256(content).hexdigest()}
            receipt = {"observation_id": sample["observation_id"], "sample_type": "FORWARD_OBSERVATION", "observed_as_of": as_of,
                "initial_receipt_sha256": sha256(initial_path.read_bytes()).hexdigest(), "data_as_of": actual_latest,
                "source_snapshot_id": snapshot.snapshot_id, "snapshot_validation": snapshot.validation,
                "rule_identity": rule_identity, "outputs": outputs, "code_sha256": provenance["code_sha256"],
                "status": "WAITING_FOR_POST_INCEPTION_DATA" if risk.empty else "RECORDED_WITH_PUBLICATION_TIME_LIMITATION",
                "no_download": True, "no_actual_orders": True}
            _freeze(target / "run_receipt.json", _bytes(receipt), root)
            print(json.dumps({"observation_id": sample["observation_id"], "data_as_of": actual_latest,
                              "observed_as_of": as_of, "status": receipt["status"], "output": str(target)}, ensure_ascii=False))
    return 0
