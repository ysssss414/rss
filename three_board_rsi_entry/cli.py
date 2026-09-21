from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .backtest import run_event_backtest
from .backtest_outputs import write_backtest_outputs
from .config import StrategyConfig
from .input_excel import create_input_template
from .market_data import AmazingDataMarketDataProvider, CsvMarketDataProvider
from .outputs import write_outputs
from .pipeline import run_analysis


def _date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def _add_analysis_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--start-date", type=_date_argument, required=True)
    parser.add_argument("--as-of", type=_date_argument, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/three_board_rsi_entry.json"),
    )
    parser.add_argument(
        "--market-data-csv",
        type=Path,
        help=(
            "Offline daily-bar CSV; otherwise the RSI project's verified "
            "AmazingDataProvider is used"
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache/three_board_rsi_entry"),
    )
    parser.add_argument(
        "--legacy-provider-root",
        type=Path,
        help=(
            "Root containing yh_quant_shape/data_provider.py; defaults to "
            "AMAZINGDATA_LEGACY_PROVIDER_ROOT or the sibling yh project"
        ),
    )
    parser.add_argument("--retry-count", type=int, default=3)
    parser.add_argument("--retry-delay-seconds", type=float, default=1.0)
    parser.add_argument(
        "--no-numba-compat",
        action="store_true",
        help="Disable the RSI project's verified AmazingData 1.1.6 no-JIT shim",
    )
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--numba-compat", action="store_true", help="Explicitly enable the AmazingData no-JIT compatibility mode")
    parser.add_argument("--research-snapshot", help="Immutable Stage 0 snapshot ID")
    parser.add_argument("--research-cache", type=Path, default=Path(".cache/research"))
    parser.add_argument("--outcome-as-of", type=_date_argument, help="Outcome cutoff for snapshot-backed event evaluation")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="three_board_rsi_entry",
        description="Replay RSI secondary-entry signals for manually entered stocks.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    template = subparsers.add_parser(
        "init-template", help="Create the manual-input Excel template"
    )
    template.add_argument("--output", type=Path, required=True)

    run = subparsers.add_parser("run", help="Run a deterministic historical replay")
    _add_analysis_arguments(run)
    backtest = subparsers.add_parser(
        "backtest",
        help="Replay signals and evaluate their forward daily-bar events",
    )
    _add_analysis_arguments(backtest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init-template":
            output = create_input_template(args.output)
            print(f"Created input template: {output}")
            return 0

        config = StrategyConfig.from_file(args.config)
        if args.research_snapshot:
            if args.market_data_csv or args.force_refresh:
                raise ValueError("Snapshot runs cannot use market-data-csv or force-refresh")
            from research.data.cache import SnapshotStore
            from research.runs.legacy import run_with_snapshot, write_research_outputs
            run = run_with_snapshot(snapshot=SnapshotStore(args.research_cache).load(args.research_snapshot),
                input_path=args.input, start_date=args.start_date, decision_as_of=args.as_of,
                outcome_as_of=args.outcome_as_of, config=config, allow_incomplete=args.allow_incomplete,
                evaluate=args.command == "backtest")
            write_research_outputs(run, args.output_dir)
            print(f"Completed research run: {run.manifest['run_id']}")
            return 0
        if args.outcome_as_of:
            raise ValueError("outcome-as-of requires research-snapshot")
        provider = (
            CsvMarketDataProvider(args.market_data_csv)
            if args.market_data_csv
            else AmazingDataMarketDataProvider(
                args.cache_dir,
                legacy_provider_root=args.legacy_provider_root,
                retry_count=args.retry_count,
                retry_delay_seconds=args.retry_delay_seconds,
                use_numba_compat=args.numba_compat and not args.no_numba_compat,
            )
        )
        try:
            analysis = run_analysis(
                input_path=args.input,
                start_date=args.start_date,
                as_of_date=args.as_of,
                provider=provider,
                config=config,
                allow_incomplete=args.allow_incomplete,
                force_refresh=args.force_refresh,
            )
            paths = write_outputs(analysis, args.output_dir)
            if args.command == "backtest":
                backtest = run_event_backtest(
                    signals=analysis.result.signals,
                    candidate_cycles=analysis.result.candidate_cycles,
                    indicator_bars=analysis.indicator_bars,
                    trading_days=analysis.trading_days,
                    as_of_date=analysis.as_of_date,
                    config=analysis.config,
                )
                paths.update(
                    write_backtest_outputs(backtest, analysis, args.output_dir)
                )
        finally:
            provider.close()
        print(
            f"Completed: cycles={len(analysis.result.candidate_cycles)}, "
            f"signals={len(analysis.result.signals)}, "
            f"warnings={len(analysis.warnings)}"
        )
        if args.command == "backtest":
            print(
                f"Backtest: events={len(backtest.events)}, "
                f"first_signals={len(backtest.first_signal_events)}, "
                f"warnings={len(backtest.warnings)}"
            )
        for name, path in paths.items():
            print(f"{name}: {path}")
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
