from __future__ import annotations

import unittest

import pandas as pd

from three_board_rsi_entry.config import StrategyConfig
from three_board_rsi_entry.replay import run_replay

from .helpers import CODE, board_frame, indicator_bars, replay_one, trading_days


class ExpiryAndReplayTests(unittest.TestCase):
    def test_day_20_can_trigger(self):
        days = trading_days(23)
        bars = indicator_bars(days, low={20: 9.0})
        result = replay_one(days=days, bars=bars, as_of_index=20)
        row = result.candidate_cycles.iloc[0]
        self.assertEqual(row["md1_date"], days[20])
        self.assertEqual(row["cycle_expiry_date"], days[20])

    def test_day_21_cannot_trigger(self):
        days = trading_days(23)
        bars = indicator_bars(days, low={21: 9.0})
        result = replay_one(days=days, bars=bars, as_of_index=21)
        row = result.candidate_cycles.iloc[0]
        self.assertIsNone(row["md1_date"])
        self.assertEqual(row["md1_result"], "EXPIRED")
        self.assertEqual(row["current_stage"], "EXPIRED")

    def test_as_of_filters_future_manual_input(self):
        days = trading_days(4)
        full_boards = board_frame(
            [(days[0], CODE, 3, "测试股"), (days[1], CODE, 4, "测试股")]
        )
        bars = indicator_bars(days, low={1: 9.0})
        with_future = run_replay(
            boards=full_boards,
            indicator_bars=bars,
            trading_days=days,
            start_date=days[0],
            as_of_date=days[0],
            config=StrategyConfig(),
        )
        truncated = run_replay(
            boards=full_boards.iloc[:1],
            indicator_bars=bars,
            trading_days=days,
            start_date=days[0],
            as_of_date=days[0],
            config=StrategyConfig(),
        )
        pd.testing.assert_frame_equal(
            with_future.candidate_cycles, truncated.candidate_cycles
        )

    def test_identical_replay_is_deterministic(self):
        days = trading_days(8)
        bars = indicator_bars(days, rsi={1: 65.0, 2: 72.0})
        first = replay_one(days=days, bars=bars, as_of_index=5)
        second = replay_one(days=days, bars=bars, as_of_index=5)
        pd.testing.assert_frame_equal(first.candidate_cycles, second.candidate_cycles)
        pd.testing.assert_frame_equal(first.signals, second.signals)

    def test_t_output_is_not_polluted_by_t_plus_one_fourth_board(self):
        days = trading_days(4)
        boards = board_frame(
            [(days[0], CODE, 3, "测试股"), (days[1], CODE, 4, "测试股")]
        )
        bars = indicator_bars(days, low={1: 9.0})
        result_t = run_replay(
            boards=boards,
            indicator_bars=bars,
            trading_days=days,
            start_date=days[0],
            as_of_date=days[0],
            config=StrategyConfig(),
        )
        result_t1 = run_replay(
            boards=boards,
            indicator_bars=bars,
            trading_days=days,
            start_date=days[0],
            as_of_date=days[1],
            config=StrategyConfig(),
        )
        self.assertEqual(
            result_t.candidate_cycles.iloc[0]["last_limit_up_date"], days[0]
        )
        self.assertEqual(
            result_t1.candidate_cycles.iloc[0]["last_limit_up_date"], days[1]
        )
        self.assertTrue(result_t1.signals.empty)


if __name__ == "__main__":
    unittest.main()
