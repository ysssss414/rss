from __future__ import annotations

import unittest

from .helpers import CODE, board_frame, indicator_bars, replay_one, trading_days


class Md1Tests(unittest.TestCase):
    def test_first_touch_recovers_and_triggers(self):
        days = trading_days(5)
        bars = indicator_bars(days, low={1: 9.5}, close={1: 10.5})
        result = replay_one(days=days, bars=bars, as_of_index=2)
        row = result.candidate_cycles.iloc[0]
        self.assertEqual(row["md1_date"], days[1])
        self.assertEqual(row["md1_result"], "TRIGGERED")

    def test_failed_first_touch_can_never_retry(self):
        days = trading_days(5)
        bars = indicator_bars(
            days, low={1: 9.0, 2: 9.0}, close={1: 9.5, 2: 11.0}
        )
        result = replay_one(days=days, bars=bars, as_of_index=3)
        row = result.candidate_cycles.iloc[0]
        self.assertEqual(row["first_ma5_touch_date"], days[1])
        self.assertEqual(row["md1_result"], "FAILED_FIRST_TOUCH")
        self.assertIsNone(row["md1_date"])

    def test_rsi_below_70_before_touch_invalidates_md1(self):
        days = trading_days(5)
        bars = indicator_bars(days, rsi={1: 69.0, 2: 75.0}, low={2: 9.0})
        result = replay_one(days=days, bars=bars, as_of_index=3)
        row = result.candidate_cycles.iloc[0]
        self.assertEqual(row["md1_result"], "LOST_RSI70_BEFORE_TOUCH")
        self.assertIsNone(row["md1_date"])

    def test_continuing_fourth_board_is_not_a_touch_day(self):
        days = trading_days(5)
        boards = board_frame(
            [(days[0], CODE, 3, "测试股"), (days[1], CODE, 4, "测试股")]
        )
        bars = indicator_bars(days, low={1: 9.0, 2: 9.0})
        result = replay_one(
            days=days, boards=boards, bars=bars, as_of_index=3
        )
        row = result.candidate_cycles.iloc[0]
        self.assertEqual(row["last_limit_up_date"], days[1])
        self.assertEqual(row["first_ma5_touch_date"], days[2])
        self.assertEqual(row["md1_date"], days[2])

    def test_md1_triggers_at_most_once(self):
        days = trading_days(6)
        bars = indicator_bars(days, low={1: 9.0, 2: 9.0, 3: 9.0})
        result = replay_one(days=days, bars=bars, as_of_index=5)
        md1 = result.signals[result.signals["signal_type"] == "MD_1"]
        self.assertEqual(len(md1), 1)


if __name__ == "__main__":
    unittest.main()
