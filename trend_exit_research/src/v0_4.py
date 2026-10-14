"""Reproducible offline V0.4 preflight. Current native SDK scope blocks live capture."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess

from research.data.cache import SnapshotStore
from research.runs.manifest import canonical_bytes
from .cases_v0_3 import audit_case, read_cases
from .data import DEFAULT_SNAPSHOT, FrozenResearchSnapshot, stock_frame
from .samples import read_samples
from .sdk_scope_v0_4 import inspect_wheels


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "trend_exit_research"
ANCHOR = "ebd42f4d8cea15e78f11cbe59bf955b511534cb2"
TARGETS = {"KUNLUN_2023_AI", "WULIANGYE_2020_2021", "WUXI_FORWARD_20261013"}


def file_hash(path):
    return sha256(path.read_bytes()).hexdigest()


def protection_guard():
    """Record once, then compare all pre-V0.4 tracked bytes and existing local results."""
    path = MODULE / "artifacts/v0_4_protected_start.json"
    if path.exists():
        original = json.loads(path.read_text(encoding="utf-8"))
    else:
        names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", ANCHOR], cwd=ROOT).decode("utf-8").splitlines()
        paths = [ROOT / name for name in names]
        paths += [ROOT / "inputs/three_board_daily_input_2026_backtest.xlsx", ROOT / "手工统计.xlsx"]
        paths += [p for p in (MODULE / "artifacts").rglob("*") if p.is_file() and
                  not p.relative_to(MODULE / "artifacts").as_posix().startswith("v0_4") and
                  p.suffix in {".csv", ".json", ".md", ".parquet"}]
        original = {"anchor": ANCHOR, "recorded_at": datetime.now(timezone.utc).isoformat(),
            "files": {p.relative_to(ROOT).as_posix(): file_hash(p) for p in sorted(set(paths))}}
        SnapshotStore(MODULE / "artifacts")._atomic_write(path, canonical_bytes(original), immutable=True)
    failures = [name for name, expected in original["files"].items() if not (ROOT/name).is_file() or file_hash(ROOT/name) != expected]
    if failures:
        raise ValueError(f"PROTECTED_FILES_CHANGED: {failures}")
    return original


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-wheel", type=Path, default=ROOT.parent / "yh/AmazingData_sdk/AmazingData/AmazingData-1.1.6-cp312-none-any.whl")
    parser.add_argument("--tgw-wheel", type=Path, default=ROOT.parent / "yh/tgw-1.0.8.7-py3-none-any.whl")
    parser.add_argument("--out", type=Path, default=MODULE / "artifacts/v0_4")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if not out.is_relative_to(MODULE / "artifacts") or not out.name.startswith("v0_4"):
        raise ValueError("Use an independent V0.4 artifacts directory")
    protected = protection_guard()
    config_path = MODULE / "config/v0_4.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    audit = inspect_wheels(args.sdk_wheel, args.tgw_wheel)
    # Scope STOP precedes SDK import, session construction, login and both smoke calls.
    if audit["scope_verdict"] != "STOP_NETWORK_SCOPE_UNQUALIFIED":
        raise ValueError("Unexpected scope change requires a new reviewed capture implementation")
    coverage = []
    with FrozenResearchSnapshot(ROOT / DEFAULT_SNAPSHOT) as snapshot:
        master = snapshot.load_universe()
        cases = read_cases(MODULE / "inputs/target_cases_v0_3.csv", read_samples(MODULE / "inputs/manual_samples.csv", master))
        for case in cases:
            if case["case_id"] not in TARGETS:
                continue
            frame = stock_frame(snapshot, case, master)
            row = audit_case(case, frame, snapshot)
            start, end = config["authorized"][case["resolved_code"]]
            existing = frame.loc[frame.trade_date.between(start, end)]
            row.update(requested_new_range=[start, end], newly_captured_bars=0, newly_captured_status=0,
                existing_bars_in_new_range=int(existing.has_bars.eq(True).sum()),
                computed=False, exploratory="UNQUALIFIED", strict_pit_execution="UNQUALIFIED",
                capture_status=audit["scope_verdict"], factor_policy="NO_NEW_ADJUSTED_PRICES_COMPUTED")
            if case["case_id"] == "KUNLUN_2023_AI":
                reuse = frame.loc[frame.trade_date.between("2024-01-01", "2024-03-31")]
                row["existing_evaluation_bars"] = int(reuse.has_bars.eq(True).sum())
                row["existing_evaluation_snapshot_grade"] = "FROZEN_SOURCE_VALIDATED;STRICT_PIT_EXECUTION_NOT_QUALIFIED"
            if case["case_id"] == "WUXI_FORWARD_20261013":
                row.update(observation_id=config["observation_id"], initial_record_preserved=True,
                    required_backfill_label=config["backfill_label"], actual_backfill_retrieved_at=None,
                    backfill_capture_status="NOT_CAPTURED", post_inception_validation=False)
            coverage.append(row)
        original_validation = dict(snapshot.validation)
    qualification = {"version": config["version"], "cases": coverage, "new_snapshots": [],
        "exploratory_verdict": "UNQUALIFIED", "strict_pit_execution_verdict": "UNQUALIFIED",
        "missing_evidence": ["AUTHORIZED_WINDOW_BARS_AND_STATUS", "BOUNDED_NATIVE_INITIALIZATION_AND_QUERY",
            "RAW_UNIT_DECLARATION", "PIT_FACTORS_ACTIONS_IMPLEMENTATION_REVISION", "HISTORICAL_MORNING_CONSTRAINTS"]}
    code = {p.relative_to(ROOT).as_posix(): file_hash(p) for p in sorted((MODULE / "src").glob("*v0_4.py"))}
    receipt = {"version": config["version"], "git_anchor": ANCHOR, "branch": "codex/trend-exit-research-v0.1",
        "draft_pr": "https://github.com/ysssss414/rss/pull/33", "recorded_at": protected["recorded_at"],
        "config_sha256": file_hash(config_path), "code_sha256": code,
        "baseline_config_sha256": config["baseline_config_sha256"], "snapshot_validation": original_validation,
        "protected_file_count": len(protected["files"]), "protected_files_sha256": sha256(canonical_bytes(protected["files"])).hexdigest(),
        "protected_files_unchanged": True, "live_sdk_imports": 0, "live_initializations": 0,
        "live_python_data_attempts": 0, "live_market_network_requests": 0, "new_snapshots": [],
        "bounded_acquisition": "STOP", "scope_status": audit["scope_verdict"],
        "case_alignment": "PARTIAL", "strategy_effectiveness": "STRATEGY_EFFECTIVENESS_NOT_YET_ESTABLISHED",
        "synthetic_snapshot_tests_are_not_real_acquisition": True, "automatic_online_permission_changed": False}
    case_report = "# TREND_EXIT_V0_4 案例结果\n\n所有新增授权区间均未取得新行情：SDK原生层范围未获资格，联网在预检停止。以下结果来自既有冻结Snapshot的离线覆盖审计，不是新实证结果。\n\n"
    case_report += "| 案例 | 目标研究区间 | 实际缓存行情 | 新增有效行情 | 目标状态 |\n| --- | --- | --- | --- | --- |\n"
    for row in coverage:
        case_report += f"| {row['case_id']} | {row['study_start']}～{row['study_end']} | {row['actual_first_date']}～{row['actual_last_date']} | 0 | {row['target_status']} |\n"
    case_report += "\n昆仑已有2024评价行情可复用（见JSON条数），但不能替代2023主升；五粮液2020—2021未恢复。MA28/MA55初跌与三日确认、两次动作间路径、条件退出回撤、后5/10/20/40日表现和重新准入均为NOT_COMPUTED_TARGET_UNCOVERED，绝不记作零收益或无风险。原三日规则与V0.1—V0.3结果保留。\n\n"
    case_report += "药明初始ID为WUXI_FORWARD_20261013_V0_1，2026-10-13初始记录未改，实际最新行情仍为2026-09-23；补取0行，检索时间为空，没有10月13日及之后的前瞻验证。未来补取必须保留真实检索时间并标为LATE_CAPTURE_NOT_SAME_DAY_PROSPECTIVE；本轮没有新归档或新Snapshot供16:30观察器使用。\n\n"
    case_report += "CASE_ALIGNMENT_PARTIAL；EXPLORATORY_DATA_UNQUALIFIED；STRICT_PIT_EXECUTION_UNQUALIFIED；STRATEGY_EFFECTIVENESS_NOT_YET_ESTABLISHED。\n"
    out.mkdir(parents=True, exist_ok=True)
    outputs = {"v0_4_sdk_scope_audit.json": canonical_bytes(audit),
        "v0_4_data_qualification.json": canonical_bytes(qualification), "v0_4_case_results.md": case_report.encode("utf-8")}
    receipt["outputs"] = {name: sha256(content).hexdigest() for name, content in outputs.items()}
    outputs["v0_4_run_receipt.json"] = canonical_bytes(receipt)
    for name, content in outputs.items():
        SnapshotStore(out)._atomic_write(out/name, content, immutable=True)
    protection_guard()
    print(json.dumps({"status": receipt["scope_status"], "live_calls": 0,
        "cases": len(coverage), "new_snapshots": 0, "out": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
