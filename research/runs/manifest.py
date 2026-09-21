from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import platform
import subprocess
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd

from research.data.contracts import QUALITY_POLICY_VERSION, AnalysisWindow


def _json_value(value):
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if not math.isfinite(value):
            raise ValueError("Infinite values cannot be serialized")
    return value


def canonical_bytes(value) -> bytes:
    return (json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def frame_records(frame: pd.DataFrame) -> list[dict]:
    return _json_value(frame.to_dict("records"))


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def environment_identity(root: Path) -> dict:
    def git(*args):
        try:
            return subprocess.check_output(["git", "--no-optional-locks", *args], cwd=root,
                                           stderr=subprocess.DEVNULL).decode().strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    versions = {}
    for name in ("numpy", "pandas", "openpyxl", "tables"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "unavailable"
    status = git("status", "--porcelain")
    sources = [p for folder in ("research", "three_board_rsi_entry") for p in (root / folder).rglob("*.py")]
    sources += [root / "pyproject.toml"]
    return {"git_sha": git("rev-parse", "HEAD"),
            "worktree_state": "UNKNOWN" if status is None else "DIRTY" if status else "CLEAN",
            "code_fingerprint": digest({p.relative_to(root).as_posix(): file_hash(p) for p in sorted(sources) if p.is_file()}),
            "python_version": platform.python_version(), "dependency_versions": versions}


def build_manifest(*, snapshot, window: AnalysisWindow, config: dict, input_path: Path,
                   status: str, root: Path, eligibility: list[dict], evaluate: bool,
                   allow_incomplete: bool) -> dict:
    payload = snapshot.payload
    hashes = payload["hashes"]
    metadata = payload["metadata"]
    spec = {
        "strategy_id": "three_board_rsi_entry", "strategy_version": "0.2.0",
        "operation": "backtest" if evaluate else "run", "allow_incomplete": allow_incomplete,
        **environment_identity(root), "data_snapshot_id": snapshot.snapshot_id,
        "data_as_of": payload["request"]["end"], **asdict(window),
        "input_file_hashes": {"strategy_input": file_hash(input_path)},
        "effective_config_hash": digest(config), "effective_config": config,
        "calendar_hash": hashes["calendar"], "raw_bars_hash": hashes["bars"],
        "factor_hash": hashes["factors"], "price_adjustment": config["price_adjustment"],
        "adjustment_anchor": window.decision_as_of.isoformat(),
        "outcome_adjustment_anchor": window.decision_as_of.isoformat(),
        "provider_version": metadata["provider_version"], "sdk_version": metadata["sdk_version"],
        "quality_policy_version": QUALITY_POLICY_VERSION,
    }
    return {**_json_value(spec), "spec_hash": digest(spec), "run_id": uuid4().hex,
            "status": status, "created_at": datetime.now(timezone.utc).isoformat(),
            "research_eligibility": eligibility}
