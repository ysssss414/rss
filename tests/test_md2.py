from __future__ import annotations

import unittest

from .helpers import indicator_bars, replay_one, trading_days


class Md2Tests(unittest.TestCase):
    def test_rebreak_within_nine_days_triggers(self):
        days = trading_days(8)
        bars = indicator_bars(days, rsi={1: 65.0, 2: 68.0, 3: 72.0})
        result = replay_one(days=days, bars=bars, as_of_index=4)
        row = result.candidate_cycles.iloc[0]
        self.assertEqual(row["md2_date"], days[3])
        self.assertEqual(row["below70_trading_days"], 2)
        self.assertEqual(row["below70_min_rsi"], 65.0)

    def test_rsi_below_60_permanently_invalidates_md2(self):
        days = trading_days(8)
        bars = indicator_bars(days, rsi={1: 65.0, 2: 59.0, 3: 75.0})
        result = replay_one(days=days, bars=bars, as_of_index=5)
        row = result.candidate_cycles.iloc[0]
        self.assertEqual(row["md2_result"], "RSI_BELOW_60")
        self.assertIsNone(row["md2_date"])

    def test_ten_consecutive_days_below_70_times_out(self):
        days = trading_days(15)
        values = {index: 65.0 for index in range(1, 11)}
        values[11] = 75.0
        bars = indicator_bars(days, rsi=values)
        result = replay_one(days=days, bars=bars, as_of_index=12)
        row = result.candidate_cycles.iloc[0]
        self.assertEqual(row["below70_trading_days"], 10)
        self.assertEqual(row["md2_result"], "BELOW70_TIMEOUT")

    def test_rebreak_below_ma5_does_not_trigger(self):
        days = trading_days(6)
        bars = indicator_bars(
            days, rsi={1: 65.0, 2: 72.0}, close={2: 9.0}, ma5={2: 10.0}
        )
        result = replay_one(days=days, bars=bars, as_of_index=2)
        self.assertTrue(
            result.signals[result.signals["signal_type"] == "MD_2"].empty
        )

    def test_md2_triggers_at_most_once(self):
        days = trading_days(10)
        bars = indicator_bars(
            days,
            rsi={1: 65.0, 2: 72.0, 3: 65.0, 4: 72.0},
        )
        result = replay_one(days=days, bars=bars, as_of_index=8)
        md2 = result.signals[result.signals["signal_type"] == "MD_2"]
        self.assertEqual(len(md2), 1)

    def test_md1_does_not_disable_later_md2(self):
        days = trading_days(8)
        bars = indicator_bars(
            days,
            rsi={1: 75.0, 2: 65.0, 3: 72.0},
            low={1: 9.0},
        )
        result = replay_one(days=days, bars=bars, as_of_index=5)
        self.assertEqual(set(result.signals["signal_type"]), {"MD_1", "MD_2"})


if __name__ == "__main__":
    unittest.main()
