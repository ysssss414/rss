from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

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
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--start-date", type=_date_argument, required=True)
    run.add_argument("--as-of", type=_date_argument, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument(
        "--config",
        type=Path,
        default=Path("config/three_board_rsi_entry.json"),
    )
    run.add_argument(
        "--market-data-csv",
        type=Path,
        help=(
            "Offline daily-bar CSV; otherwise the RSI project's verified "
            "AmazingDataProvider is used"
        ),
    )
    run.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache/three_board_rsi_entry"),
    )
    run.add_argument(
        "--legacy-provider-root",
        type=Path,
        help=(
            "Root containing yh_quant_shape/data_provider.py; defaults to "
            "AMAZINGDATA_LEGACY_PROVIDER_ROOT or the sibling yh project"
        ),
    )
    run.add_argument("--retry-count", type=int, default=3)
    run.add_argument("--retry-delay-seconds", type=float, default=1.0)
    run.add_argument(
        "--no-numba-compat",
        action="store_true",
        help="Disable the RSI project's verified AmazingData 1.1.6 no-JIT shim",
    )
    run.add_argument("--allow-incomplete", action="store_true")
    run.add_argument("--force-refresh", action="store_true")
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
        provider = (
            CsvMarketDataProvider(args.market_data_csv)
            if args.market_data_csv
            else AmazingDataMarketDataProvider(
                args.cache_dir,
                legacy_provider_root=args.legacy_provider_root,
                retry_count=args.retry_count,
                retry_delay_seconds=args.retry_delay_seconds,
                use_numba_compat=not args.no_numba_compat,
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
        finally:
            provider.close()
        print(
            f"Completed: cycles={len(analysis.result.candidate_cycles)}, "
            f"signals={len(analysis.result.signals)}, "
            f"warnings={len(analysis.warnings)}"
        )
        for name, path in paths.items():
            print(f"{name}: {path}")
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
