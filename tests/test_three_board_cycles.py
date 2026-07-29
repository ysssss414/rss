from __future__ import annotations

import unittest

from .helpers import CODE, board_frame, indicator_bars, replay_one, trading_days


class CandidateCycleTests(unittest.TestCase):
    def test_three_board_rsi_above_70_qualifies(self):
        days = trading_days(3)
        result = replay_one(days=days, as_of_index=0)
        row = result.candidate_cycles.iloc[0]
        self.assertEqual(row["qualified_date"], days[0])
        self.assertEqual(row["qualified_board_count"], 3)

    def test_fourth_board_can_be_first_qualification(self):
        days = trading_days(4)
        boards = board_frame(
            [(days[0], CODE, 3, "测试股"), (days[1], CODE, 4, "测试股")]
        )
        bars = indicator_bars(days, rsi={0: 65.0, 1: 71.0})
        result = replay_one(days=days, boards=boards, bars=bars, as_of_index=1)
        row = result.candidate_cycles.iloc[0]
        self.assertEqual(row["qualified_date"], days[1])
        self.assertEqual(row["qualified_board_count"], 4)

    def test_never_above_70_is_not_qualified(self):
        days = trading_days(4)
        boards = board_frame(
            [(days[0], CODE, 3, "测试股"), (days[1], CODE, 4, "测试股")]
        )
        bars = indicator_bars(days, rsi={0: 65.0, 1: 69.9})
        result = replay_one(days=days, boards=boards, bars=bars, as_of_index=3)
        row = result.candidate_cycles.iloc[0]
        self.assertEqual(row["current_stage"], "NOT_QUALIFIED")
        self.assertIsNone(row["qualified_date"])

    def test_exactly_70_does_not_qualify(self):
        days = trading_days(2)
        bars = indicator_bars(days, rsi={0: 70.0})
        result = replay_one(days=days, bars=bars, as_of_index=0)
        self.assertEqual(
            result.candidate_cycles.iloc[0]["current_stage"], "NOT_QUALIFIED"
        )

    def test_gap_creates_a_new_stable_cycle_id(self):
        days = trading_days(5)
        boards = board_frame(
            [(days[0], CODE, 3, "测试股"), (days[2], CODE, 3, "测试股")]
        )
        result = replay_one(days=days, boards=boards, as_of_index=4)
        self.assertEqual(len(result.candidate_cycles), 2)
        self.assertEqual(
            result.candidate_cycles["candidate_cycle_id"].tolist(),
            [
                f"{CODE}_{days[0].strftime('%Y%m%d')}",
                f"{CODE}_{days[2].strftime('%Y%m%d')}",
            ],
        )


if __name__ == "__main__":
    unittest.main()
