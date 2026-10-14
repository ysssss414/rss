"""python -m trend_exit_research.src.cli: offline coverage and bounded smoke."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess
import sys

import pandas as pd

from .data import DEFAULT_SNAPSHOT, FrozenResearchSnapshot, coverage_row, stock_frame
from .indicators import calculate, config_hash, load_config, METRICS, SIGNAL_COLUMNS
from .samples import read_samples
from .synthetic import synthetic_frame
from .risk import apply_risk, load_risk_config
from .evaluation import evaluate, comparison_rows


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "trend_exit_research"


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def write_outputs(out: Path, *, coverage: list[dict], daily: list[pd.DataFrame], logs: list[pd.DataFrame],
                  config: dict, provenance: dict, extra_tables=None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    tables = {"coverage.csv": pd.DataFrame(coverage) if coverage else pd.DataFrame(columns=["sample_id", "stock_name", "resolved_code", "data_coverage", "missing_reasons"]),
              "daily_indicators.csv": pd.concat(daily, ignore_index=True) if daily else pd.DataFrame(columns=["security_id", "trade_date", *METRICS, "A", "B", "C", "D"]),
              "candidate_signals.csv": pd.concat(logs, ignore_index=True) if logs else pd.DataFrame(columns=SIGNAL_COLUMNS)}
    tables.update(extra_tables or {})
    for name, table in tables.items():
        table.to_csv(out / name, index=False, encoding="utf-8-sig", float_format="%.12g", lineterminator="\n")
    receipt = {"schema": "trend-exit-run/0.1", "research_only": True, "config": config,
               "config_sha256": config_hash(config), **provenance,
               "outputs": {name: {"rows": len(table), "sha256": _hash(out / name)} for name, table in tables.items()},
               "no_download": True, "no_actual_orders_or_portfolio_pnl": True,
               "limitations": ["MANUALLY_SELECTED_CASES_NOT_AN_UNBIASED_POPULATION",
                               "DEFAULT_PARAMETERS_NOT_VALIDATED_OR_OPTIMIZED",
                               "NO_EXIT_EFFECTIVENESS_CLAIM"]}
    (out / "run_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Independent offline candidate research, maximum two real samples")
    parser.add_argument("mode", choices=("coverage", "run", "synthetic", "forward"))
    parser.add_argument("--samples", type=Path, default=MODULE / "inputs/manual_samples.csv")
    parser.add_argument("--config", type=Path, default=MODULE / "config/default.json")
    parser.add_argument("--snapshot", type=Path, default=ROOT / DEFAULT_SNAPSHOT)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--max-real", type=int, choices=(0, 1, 2), default=2)
    parser.add_argument("--risk-config", type=Path, default=MODULE / "config/risk_baseline.json")
    parser.add_argument("--confirm-days", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--as-of", help="Explicit Shanghai observation date for forward mode")
    arguments = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(arguments)
    args.snapshot_explicit = "--snapshot" in arguments or any(value.startswith("--snapshot=") for value in arguments)
    out = (args.out or MODULE / "artifacts" / args.mode).resolve()
    if not out.is_relative_to((MODULE / "artifacts").resolve()):
        parser.error("Output must remain inside trend_exit_research/artifacts")
    try:
        config = load_config(args.config)
        risk_config = load_risk_config(args.risk_config)
        code = {path.relative_to(ROOT).as_posix(): _hash(path) for path in sorted(MODULE.glob("src/*.py"))}
        reused = {path.relative_to(ROOT).as_posix(): _hash(path) for path in
                  (ROOT / "research/snapshot.py", ROOT / "research/data/adjustment.py",
                   ROOT / "research/real_materialization.py", ROOT / "research/factor_pit.py", ROOT / "research/foundation.py")}
        provenance = {"mode": args.mode, "python": platform.python_version(), "pandas": pd.__version__,
                      "code_sha256": code, "reused_code_sha256": reused,
                      "git_branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
                      "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
        provenance.update(risk_config=risk_config, risk_config_sha256=config_hash(risk_config), confirmation_days=args.confirm_days)
        coverage, daily, logs, risk_logs, outcomes, comparison, summaries = [], [], [], [], [], [], []
        if args.mode == "forward":
            from .forward import run_forward
            return run_forward(args=args, config=config, risk_config=risk_config, provenance=provenance)

        def append_research(indicators, signals, frame, sample=None):
            risk, events = apply_risk(indicators, risk_config, confirmation_days=args.confirm_days)
            evaluation = evaluate(risk, frame)
            if sample:
                start, end = sample["effective_start"], sample["effective_end"]
                for table in (risk, signals, events, evaluation):
                    if not table.empty:
                        table.drop(table.index[~table.trade_date.between(start, end)], inplace=True)
                    table["sample_id"], table["stock_name"] = sample["sample_id"], sample["stock_name"]
            daily.append(risk)
            logs.append(signals)
            risk_logs.append(events)
            outcomes.append(evaluation)
            comparison.extend(comparison_rows(indicators, frame, risk_config,
                start=sample["effective_start"] if sample else None, end=sample["effective_end"] if sample else None))
            summaries.append({"security_id": frame.security_id.iloc[0], "risk_rule_version": risk_config["version"],
                "confirmation_days": args.confirm_days, "daily_rows": len(risk),
                "ma28_reduce_signals": int(risk.reduce_required.sum()), "ma55_clear_signals": int(risk.clear_required.sum()),
                "modelled_fills": int(evaluation.execution_status.eq("MODELLED_FILL").sum()),
                "no_fill": int(evaluation.execution_status.eq("NO_FILL").sum()),
                "execution_unresolved": int(evaluation.execution_status.eq("EXECUTION_UNRESOLVED").sum()),
                "forward20_available": int(evaluation.forward_20_status.eq("AVAILABLE").sum()),
                "state_days": json.dumps(risk.POSITION_RISK_STATE.value_counts().to_dict(), sort_keys=True),
                "position_caps": "EXPLICIT_ZERO_FOR_EXIT;OTHER_RATIOS_UNSPECIFIED"})
        if args.mode == "synthetic":
            frame = synthetic_frame()
            indicators, signals = calculate(frame, config, actions=())
            append_research(indicators, signals, frame)
            provenance.update(source_snapshot_id=frame.source_snapshot_id.iloc[0], selected_samples=["SYNTHETIC_ONLY"],
                              selected_real_count=0)
        else:
            provenance["manual_samples_sha256"] = _hash(args.samples)
            with FrozenResearchSnapshot(args.snapshot) as snapshot:
                master = snapshot.load_universe()
                samples = read_samples(args.samples, master)
                for sample in samples:
                    frame = stock_frame(snapshot, sample, master) if sample["resolution_status"] == "RESOLVED" else pd.DataFrame()
                    record = coverage_row(sample, frame, snapshot)
                    record["computed"] = False
                    if (args.mode == "run" and len(daily) < args.max_real and record["range_status"] == "WITHIN_SNAPSHOT"
                            and record["data_coverage"] == 1 and record["usable_trading_bars"] >= 60):
                        indicators, signals = calculate(frame.loc[frame.trade_date.le(record["effective_end"])], config)
                        # Complete prehistory drives causal state; sample boundaries only slice results.
                        append_research(indicators, signals, frame, record)
                        record.update(computed=True, ready_days=int(daily[-1].signal_eligible.sum()),
                                      reset_days=int(daily[-1].indicator_status.eq("UNQUALIFIED_ACTION_RESET").sum()),
                                      signal_count=len(logs[-1]))
                    coverage.append(record)
                provenance.update(source_snapshot_id=snapshot.snapshot_id, snapshot_validation=snapshot.validation,
                    selected_samples=[row["sample_id"] for row in coverage if row["computed"]],
                    selected_real_count=len(daily), selection_policy="FIRST_COMPLETE_CSV_SAMPLES_MAX_TWO_NO_OUTCOME_SELECTION",
                    real_factor_policy="EVENT_FREE_SEGMENT_ONLY_RESET_ON_ACTION_OR_FACTOR_CHANGE",
                    real_availability_basis="ASSUMED_15_30_SHANGHAI_NOT_HISTORICAL_PUBLICATION_PROOF")
        extras = {"risk_signals.csv": pd.concat(risk_logs, ignore_index=True) if risk_logs else pd.DataFrame(columns=["security_id", "trade_date", "signal"]),
                  "daily_evaluation.csv": pd.concat(outcomes, ignore_index=True) if outcomes else pd.DataFrame(columns=["security_id", "trade_date", "outcome_only"]),
                  "confirmation_comparison.csv": pd.DataFrame(comparison) if comparison else pd.DataFrame(columns=["security_id", "confirmation_days"]),
                  "risk_summary.csv": pd.DataFrame(summaries) if summaries else pd.DataFrame(columns=["security_id", "daily_rows"])}
        receipt = write_outputs(out, coverage=coverage, daily=daily, logs=logs, config=config, provenance=provenance, extra_tables=extras)
        report = "# 趋势退出与风险预算 V0.1\n\n规则：TREND_EXIT_BASELINE_V0_1；本轮确认天数：" + str(args.confirm_days) + "。\n\n"
        report += "| 证券 | 逐日行数 | MA28 减仓确认 | MA55 清仓确认 | 模型成交 | 执行未解决 |\n| --- | ---: | ---: | ---: | ---: | ---: |\n"
        for item in summaries:
            report += f'| {item["security_id"]} | {item["daily_rows"]} | {item["ma28_reduce_signals"]} | {item["ma55_clear_signals"]} | {item["modelled_fills"]} | {item["execution_unresolved"]} |\n'
        report += "\n1/2/3 日确认对照见 confirmation_comparison.csv，未选择最优参数。五日重站均线是事后诊断代理，不等于总体假跌破率。"
        report += "\n\n未来 5/10/20 日与执行条件仅存在 daily_evaluation.csv，不进入风险状态。真实早盘约束未获验证，不能声称实际成交。"
        report += "\n\n退出及重新评估状态的趋势目标为 0；其它仓位比例保持空值。箱体仅记录候选上下沿，组合层预算输入尚缺。人工案例存在选择偏差。\n"
        (out / "research_report.md").write_text(report, encoding="utf-8")
        receipt["outputs"]["research_report.md"] = {"rows": len(summaries), "sha256": _hash(out / "research_report.md")}
        (out / "run_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError) as exc:
        print(f"STOP: {exc}")
        return 1
    print(json.dumps({"status": "CALCULATION_COMPLETE" if daily else "COVERAGE_ONLY", "output": str(out),
                      "selected_real_count": receipt["selected_real_count"], "outputs": receipt["outputs"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
