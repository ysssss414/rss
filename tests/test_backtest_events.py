from __future__ import annotations

import unittest

import pandas as pd

from .backtest_helpers import (
    candidate_frame,
    market_bars,
    run_backtest,
    signal_frame,
)
from .helpers import trading_days


class BacktestEventTests(unittest.TestCase):
    def test_next_tradable_open_is_entry_and_horizons_start_after_entry(self):
        days = trading_days(13)
        result = run_backtest(days=days)
        event = result.events.iloc[0]

        self.assertEqual(event["entry_status"], "COMPLETED")
        self.assertEqual(event["entry_date"], days[1])
        self.assertEqual(event["entry_delay_market_days"], 1)
        self.assertEqual(event["entry_price"], 101.0)
        self.assertAlmostEqual(event["signal_close_to_entry_gap"], 0.01)
        self.assertNotEqual(event["entry_price"], event["signal_close"])
        self.assertEqual(event["exit_date_1d"], days[2])
        self.assertEqual(event["exit_date_3d"], days[4])
        self.assertEqual(event["exit_date_5d"], days[6])
        self.assertEqual(event["exit_date_10d"], days[11])
        self.assertAlmostEqual(event["return_1d"], 103.0 / 101.0 - 1.0)

    def test_suspension_delays_entry_without_fabricating_an_open(self):
        days = trading_days(6)
        bars = market_bars(days, suspended={1})
        result = run_backtest(days=days, bars=bars)
        event = result.events.iloc[0]

        self.assertEqual(event["entry_status"], "COMPLETED")
        self.assertEqual(event["entry_date"], days[2])
        self.assertEqual(event["entry_delay_market_days"], 2)
        self.assertEqual(event["entry_price"], 102.0)
        self.assertIn("ENTRY_DELAYED_BY_SUSPENSION", set(result.incomplete_samples["issue_type"]))

    def test_no_tradable_bar_before_cutoff_has_no_entry(self):
        days = trading_days(4)
        bars = market_bars(days, suspended={1, 2, 3})
        result = run_backtest(days=days, bars=bars)
        event = result.events.iloc[0]

        self.assertEqual(event["entry_status"], "NO_ENTRY_DATA")
        self.assertIsNone(event["entry_date"])
        self.assertTrue(pd.isna(event["entry_price"]))
        self.assertEqual(
            result.incomplete_samples.iloc[0]["issue_type"], "NO_ENTRY_DATA"
        )

    def test_missing_market_row_before_entry_is_not_silently_skipped(self):
        days = trading_days(5)
        bars = market_bars(days, omitted={1})
        result = run_backtest(days=days, bars=bars)
        event = result.events.iloc[0]

        self.assertEqual(event["entry_status"], "MARKET_DATA_INCOMPLETE")
        self.assertFalse(event["data_complete"])
        self.assertTrue(any(days[1].isoformat() in item for item in result.warnings))

    def test_short_horizons_complete_while_ten_day_is_insufficient(self):
        days = trading_days(8)
        result = run_backtest(days=days)
        event = result.events.iloc[0]

        self.assertEqual(event["holding_status_1d"], "COMPLETED")
        self.assertEqual(event["holding_status_3d"], "COMPLETED")
        self.assertEqual(event["holding_status_5d"], "COMPLETED")
        self.assertEqual(
            event["holding_status_10d"], "INSUFFICIENT_FORWARD_DATA"
        )
        self.assertTrue(pd.isna(event["return_10d"]))
        self.assertTrue(pd.isna(event["mfe_10d"]))
        self.assertTrue(pd.isna(event["mae_10d"]))

    def test_mfe_mae_include_entry_and_exit_and_record_extreme_dates(self):
        days = trading_days(5)
        prices = {
            1: (100.0, 120.0, 95.0, 105.0),
            2: (106.0, 110.0, 90.0, 108.0),
        }
        result = run_backtest(
            days=days, bars=market_bars(days, prices=prices)
        )
        event = result.events.iloc[0]

        self.assertAlmostEqual(event["mfe_1d"], 0.20)
        self.assertAlmostEqual(event["mae_1d"], -0.10)
        self.assertEqual(event["max_high_date_1d"], days[1])
        self.assertEqual(event["min_low_date_1d"], days[2])

    def test_missing_bar_inside_horizon_marks_only_affected_windows(self):
        days = trading_days(8)
        bars = market_bars(days, omitted={4})
        result = run_backtest(days=days, bars=bars)
        event = result.events.iloc[0]

        self.assertEqual(event["holding_status_1d"], "COMPLETED")
        self.assertEqual(event["holding_status_3d"], "MARKET_DATA_INCOMPLETE")
        self.assertEqual(event["holding_status_5d"], "MARKET_DATA_INCOMPLETE")
        self.assertTrue(pd.isna(event["return_3d"]))

    def test_as_of_date_blocks_future_bars(self):
        days = trading_days(12)
        full_bars = market_bars(days)
        early = run_backtest(
            days=days,
            bars=full_bars,
            as_of_date=days[4],
        )
        truncated = run_backtest(
            days=days[:5],
            bars=full_bars.iloc[:5].copy(),
            as_of_date=days[4],
        )

        pd.testing.assert_frame_equal(early.events, truncated.events)
        self.assertEqual(
            early.events.iloc[0]["holding_status_5d"],
            "INSUFFICIENT_FORWARD_DATA",
        )

    def test_backtest_does_not_mutate_signals_or_candidates(self):
        days = trading_days(6)
        signals = signal_frame([(days[0], "cycle-a", "MD_1")])
        candidates = candidate_frame(["cycle-a"])
        original_signals = signals.copy(deep=True)
        original_candidates = candidates.copy(deep=True)

        run_backtest(days=days, signals=signals, candidates=candidates)

        pd.testing.assert_frame_equal(signals, original_signals)
        pd.testing.assert_frame_equal(candidates, original_candidates)

    def test_open_equal_ohlc_is_audit_flag_not_an_exclusion(self):
        days = trading_days(4)
        bars = market_bars(
            days,
            prices={1: (100.0, 100.0, 100.0, 100.0)},
        )
        event = run_backtest(days=days, bars=bars).events.iloc[0]

        self.assertEqual(event["entry_status"], "COMPLETED")
        self.assertTrue(event["entry_open_equals_high_equals_low_equals_close"])


class FirstSignalTests(unittest.TestCase):
    def test_two_signals_are_kept_but_first_file_keeps_earlier_signal(self):
        days = trading_days(8)
        signals = signal_frame(
            [
                (days[0], "cycle-a", "MD_1"),
                (days[2], "cycle-a", "MD_2"),
            ]
        )
        result = run_backtest(days=days, signals=signals)

        self.assertEqual(result.events["signal_type"].tolist(), ["MD_1", "MD_2"])
        self.assertEqual(len(result.first_signal_events), 1)
        self.assertEqual(result.first_signal_events.iloc[0]["signal_type"], "MD_1")

    def test_same_day_dual_signal_prefers_md1(self):
        days = trading_days(6)
        signals = signal_frame(
            [
                (days[0], "cycle-a", "MD_2"),
                (days[0], "cycle-a", "MD_1"),
            ]
        )
        result = run_backtest(days=days, signals=signals)

        self.assertEqual(len(result.events), 2)
        self.assertEqual(result.first_signal_events.iloc[0]["signal_type"], "MD_1")


if __name__ == "__main__":
    unittest.main()
