from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import pandas as pd

from research.data.cache import Snapshot
from research.data.contracts import AnalysisWindow, DataContractError
from research.data.provider import SnapshotProvider
from research.data.validation import available_mask
from three_board_rsi_entry.backtest import BacktestResult, run_event_backtest
from three_board_rsi_entry.config import StrategyConfig
from three_board_rsi_entry.pipeline import AnalysisRun, run_analysis
from .manifest import build_manifest, canonical_bytes, frame_records


@dataclass(frozen=True)
class ResearchRun:
    analysis: AnalysisRun
    backtest: BacktestResult | None
    eligibility: pd.DataFrame
    manifest: dict


def research_eligibility(analysis: AnalysisRun, snapshot: Snapshot) -> pd.DataFrame:
    """Quality uses each signal's decision prefix, never later outcomes/statuses."""
    payload = snapshot.payload
    raw = snapshot.bars()
    rows = []
    # Include non-signalling cycles as auditable research samples too.
    samples = [(row.candidate_cycle_id, row.ts_code, row.signal_date) for row in analysis.result.signals.itertuples()]
    signalled = {row[0] for row in samples}
    samples += [(row.candidate_cycle_id, row.ts_code, analysis.as_of_date)
                for row in analysis.result.candidate_cycles.itertuples() if row.candidate_cycle_id not in signalled]
    for cycle_id, code, cutoff in samples:
        prefix = raw.loc[raw.ts_code.eq(code) & raw.trade_date.le(cutoff.isoformat()) & available_mask(raw, cutoff)]
        calendar_prior = [d for d in payload["calendar"] if d < analysis.start_date.isoformat()]
        calculation_start = calendar_prior[-analysis.config.warmup_trading_days:][0] if calendar_prior else analysis.start_date.isoformat()
        prefix = prefix.loc[prefix.trade_date.ge(calculation_start)]
        warmup = prefix.loc[prefix.trade_date.lt(analysis.start_date.isoformat())
                            & prefix.trading_status.eq("TRADING") & prefix.raw_close.notna()]
        reasons = []
        if len(warmup) < analysis.config.warmup_trading_days:
            reasons.append("insufficient_warmup")
        if not payload["metadata"]["calendar_verified"]:
            reasons.append("unverified_calendar")
        if not payload["metadata"]["availability_verified"]:
            reasons.append("unverified_historical_availability")
        expected = {d for d in payload["calendar"] if calculation_start <= d <= cutoff.isoformat()}
        if expected - set(prefix.trade_date):
            reasons += ["incomplete_snapshot", "unknown_trading_status"]
        if prefix.trading_status.eq("UNKNOWN").any():
            reasons.append("unknown_trading_status")
        if not prefix.quality_status.eq("PASS").all():
            reasons.append("data_quality_failure")
        if any(d <= cutoff for d in analysis.input_data.missing_confirmation_dates):
            reasons.append("incomplete_manual_input")
        rows.append({"candidate_cycle_id": cycle_id, "ts_code": code, "decision_date": cutoff,
                     "research_eligible": not reasons,
                     "research_exclusion_reason": "|".join(dict.fromkeys(reasons))})
    return pd.DataFrame(rows, columns=["candidate_cycle_id", "ts_code", "decision_date", "research_eligible", "research_exclusion_reason"])


def run_with_snapshot(*, snapshot: Snapshot, input_path: Path | str, start_date: date,
                      decision_as_of: date, outcome_as_of: date | None = None,
                      config: StrategyConfig | None = None, allow_incomplete=False,
                      evaluate=True) -> ResearchRun:
    config = config or StrategyConfig()
    outcome = outcome_as_of or decision_as_of
    prior = [date.fromisoformat(d) for d in snapshot.payload["calendar"] if d < start_date.isoformat()]
    warmup = prior[-config.warmup_trading_days:]
    calculation_start = warmup[0] if warmup else start_date
    window = AnalysisWindow(calculation_start, start_date, decision_as_of, decision_as_of, outcome)
    provider = SnapshotProvider(snapshot, price_anchor=decision_as_of)
    analysis = run_analysis(input_path=input_path, start_date=start_date, as_of_date=decision_as_of,
                            provider=provider, config=config, allow_incomplete=allow_incomplete)
    eligibility = research_eligibility(analysis, snapshot)
    event = None
    if evaluate:
        codes = tuple(sorted(set(analysis.input_data.boards.ts_code)))
        if codes:
            outcome_bars = provider.daily_bars(codes, calculation_start, outcome, price_adjustment=config.price_adjustment)
        else:
            outcome_bars = analysis.indicator_bars
        event = run_event_backtest(signals=analysis.result.signals, candidate_cycles=analysis.result.candidate_cycles,
                                   indicator_bars=outcome_bars, trading_days=provider.trading_days(start_date, outcome),
                                   as_of_date=outcome, config=config)
    manifest = build_manifest(snapshot=snapshot, window=window, config=config.as_dict(), input_path=Path(input_path),
                              status="COMPLETED" if eligibility.research_eligible.all() else "COMPLETED_WITH_EXCLUSIONS",
                              root=Path(__file__).resolve().parents[2], eligibility=frame_records(eligibility),
                              evaluate=evaluate, allow_incomplete=allow_incomplete)
    return ResearchRun(analysis, event, eligibility, manifest)


def write_research_outputs(run: ResearchRun, destination: Path | str):
    from three_board_rsi_entry.outputs import write_outputs
    from three_board_rsi_entry.backtest_outputs import write_backtest_outputs
    root = Path(destination)
    # Research artifacts are never silently overwritten by another run.
    if root.exists() and any(root.iterdir()):
        raise DataContractError("OUTPUT_EXISTS", "Research output directory must be new or empty")
    write_outputs(run.analysis, root)
    if run.backtest is not None:
        outcome_analysis = replace(run.analysis, as_of_date=date.fromisoformat(run.manifest["outcome_as_of"]))
        write_backtest_outputs(run.backtest, outcome_analysis, root)
    run.eligibility.to_csv(root / "research_eligibility.csv", index=False, lineterminator="\n")
    (root / "research_manifest.json").write_bytes(canonical_bytes(run.manifest))
