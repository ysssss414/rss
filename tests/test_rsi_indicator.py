from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from three_board_rsi_entry.indicators import calculate_rsi

from .helpers import CODE, board_frame, indicator_bars, replay_one, trading_days


class RsiIndicatorTests(unittest.TestCase):
    def test_recurrence_matches_manual_calculation(self):
        result = calculate_rsi(pd.Series([10.0, 11.0, 10.0, 12.0]), period=2)
        self.assertTrue(np.isnan(result.iloc[0]))
        self.assertAlmostEqual(result.iloc[1], 100.0)
        self.assertAlmostEqual(result.iloc[2], 50.0)
        self.assertAlmostEqual(result.iloc[3], 100 * 1.25 / 1.5)

    def test_flat_prices_produce_nan_not_infinity(self):
        result = calculate_rsi(pd.Series([10.0] * 20), period=14)
        self.assertFalse(np.isinf(result.to_numpy()).any())
        self.assertTrue(result.isna().all())

    def test_period_parameter_changes_result(self):
        close = pd.Series([10, 11, 10, 12, 11, 14], dtype=float)
        period_two = calculate_rsi(close, period=2)
        period_four = calculate_rsi(close, period=4)
        self.assertNotAlmostEqual(period_two.iloc[-1], period_four.iloc[-1])

    def test_warmup_dates_cannot_create_cycles_or_signals(self):
        days = trading_days(8)
        start = days[3]
        boards = board_frame([(days[1], CODE, 3, "预热股")])
        result = replay_one(
            days=days[3:],
            boards=boards,
            bars=indicator_bars(days),
            as_of_index=4,
        )
        self.assertTrue(result.candidate_cycles.empty)
        self.assertTrue(result.signals.empty)


if __name__ == "__main__":
    unittest.main()
