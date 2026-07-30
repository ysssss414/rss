from __future__ import annotations

import json
import unittest
from pathlib import Path

import pandas as pd

from three_board_rsi_entry.cli import main

from .helpers import TEST_TEMP_ROOT


class BacktestCliTests(unittest.TestCase):
    def test_offline_backtest_command_writes_original_and_new_outputs(self):
        output_dir = Path(TEST_TEMP_ROOT) / "backtest_cli"
        exit_code = main(
            [
                "backtest",
                "--input",
                "examples/backtest_smoke_input.xlsx",
                "--market-data-csv",
                "examples/backtest_smoke_market_data.csv",
                "--start-date",
                "2025-06-30",
                "--as-of",
                "2025-07-14",
                "--output-dir",
                str(output_dir),
            ]
        )

        self.assertEqual(exit_code, 0)
        expected = {
            "candidate_cycles.csv",
            "signals.csv",
            "candidate_status.xlsx",
            "run_summary.json",
            "backtest_events.csv",
            "first_signal_events.csv",
            "backtest_summary.xlsx",
            "backtest_run_summary.json",
        }
        self.assertTrue(all((output_dir / name).exists() for name in expected))
        events = pd.read_csv(output_dir / "backtest_events.csv")
        self.assertEqual(events["signal_type"].tolist(), ["MD_1", "MD_2"])
        self.assertTrue((events["holding_status_5d"] == "COMPLETED").all())
        self.assertTrue(
            (
                events["holding_status_10d"]
                == "INSUFFICIENT_FORWARD_DATA"
            ).all()
        )
        summary = json.loads(
            (output_dir / "backtest_run_summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(summary["raw_signal_count"], 2)
        self.assertEqual(summary["first_signal_count"], 1)


if __name__ == "__main__":
    unittest.main()
