from __future__ import annotations

import unittest

from .helpers import CODE, board_frame, indicator_bars, replay_one, trading_days


class CycleSupersessionTests(unittest.TestCase):
    def test_new_cycle_supersedes_old_cycle_before_md1(self):
        days = trading_days(6)
        boards = board_frame(
            [(days[0], CODE, 3, "测试股"), (days[2], CODE, 3, "测试股")]
        )
        bars = indicator_bars(days, low={3: 9.0})

        result = replay_one(
            days=days, boards=boards, bars=bars, as_of_index=4
        )
        old_cycle, new_cycle = result.candidate_cycles.to_dict("records")

        self.assertEqual(old_cycle["invalid_reason"], "SUPERSEDED_BY_NEW_CYCLE")
        self.assertEqual(old_cycle["current_stage"], "INVALID")
        self.assertEqual(old_cycle["superseded_date"], days[2])
        self.assertIsNone(old_cycle["md1_date"])
        self.assertEqual(new_cycle["md1_date"], days[3])
        day_three_signals = result.signals[
            result.signals["signal_date"] == days[3]
        ]
        self.assertEqual(len(day_three_signals), 1)
        self.assertEqual(
            day_three_signals.iloc[0]["candidate_cycle_id"],
            new_cycle["candidate_cycle_id"],
        )

    def test_superseded_cycle_cannot_trigger_md2(self):
        days = trading_days(7)
        boards = board_frame(
            [(days[0], CODE, 3, "测试股"), (days[2], CODE, 3, "测试股")]
        )
        bars = indicator_bars(
            days, rsi={1: 65.0, 2: 75.0, 3: 65.0, 4: 72.0}
        )

        result = replay_one(
            days=days, boards=boards, bars=bars, as_of_index=5
        )
        old_cycle, new_cycle = result.candidate_cycles.to_dict("records")
        md2_signals = result.signals[result.signals["signal_type"] == "MD_2"]

        self.assertEqual(old_cycle["invalid_reason"], "SUPERSEDED_BY_NEW_CYCLE")
        self.assertIsNone(old_cycle["md2_date"])
        self.assertEqual(new_cycle["md2_date"], days[4])
        self.assertEqual(len(md2_signals), 1)
        self.assertEqual(
            md2_signals.iloc[0]["candidate_cycle_id"],
            new_cycle["candidate_cycle_id"],
        )

    def test_historical_md1_is_preserved_after_supersession(self):
        days = trading_days(7)
        boards = board_frame(
            [(days[0], CODE, 3, "测试股"), (days[2], CODE, 3, "测试股")]
        )
        bars = indicator_bars(
            days,
            rsi={3: 65.0, 4: 72.0},
            low={1: 9.0},
        )

        result = replay_one(
            days=days, boards=boards, bars=bars, as_of_index=5
        )
        old_cycle, new_cycle = result.candidate_cycles.to_dict("records")
        old_signals = result.signals[
            result.signals["candidate_cycle_id"]
            == old_cycle["candidate_cycle_id"]
        ]

        self.assertEqual(old_cycle["invalid_reason"], "SUPERSEDED_BY_NEW_CYCLE")
        self.assertEqual(old_cycle["md1_date"], days[1])
        self.assertIsNone(old_cycle["md2_date"])
        self.assertEqual(old_signals["signal_type"].tolist(), ["MD_1"])
        self.assertEqual(new_cycle["md2_date"], days[4])

    def test_multiple_new_cycles_supersede_only_the_latest_cycle(self):
        days = trading_days(8)
        boards = board_frame(
            [
                (days[0], CODE, 3, "测试股"),
                (days[2], CODE, 3, "测试股"),
                (days[4], CODE, 3, "测试股"),
            ]
        )
        bars = indicator_bars(days, low={5: 9.0})

        result = replay_one(
            days=days, boards=boards, bars=bars, as_of_index=6
        )
        cycle_a, cycle_b, cycle_c = result.candidate_cycles.to_dict("records")

        self.assertEqual(
            [
                cycle_a["candidate_cycle_id"],
                cycle_b["candidate_cycle_id"],
                cycle_c["candidate_cycle_id"],
            ],
            [
                f"{CODE}_{days[0].strftime('%Y%m%d')}",
                f"{CODE}_{days[2].strftime('%Y%m%d')}",
                f"{CODE}_{days[4].strftime('%Y%m%d')}",
            ],
        )
        self.assertEqual(cycle_a["invalid_reason"], "SUPERSEDED_BY_NEW_CYCLE")
        self.assertEqual(cycle_a["superseded_date"], days[2])
        self.assertEqual(cycle_b["invalid_reason"], "SUPERSEDED_BY_NEW_CYCLE")
        self.assertEqual(cycle_b["superseded_date"], days[4])
        self.assertEqual(cycle_c["invalid_reason"], "")
        self.assertIsNone(cycle_c["superseded_date"])
        self.assertEqual(cycle_c["md1_date"], days[5])
        self.assertEqual(
            result.signals["candidate_cycle_id"].tolist(),
            [cycle_c["candidate_cycle_id"]],
        )

    def test_supersession_is_applied_only_on_new_cycle_start_date(self):
        days = trading_days(5)
        boards = board_frame(
            [(days[0], CODE, 3, "测试股"), (days[2], CODE, 3, "测试股")]
        )
        bars = indicator_bars(days)

        before = replay_one(
            days=days, boards=boards, bars=bars, as_of_index=1
        )
        on_start = replay_one(
            days=days, boards=boards, bars=bars, as_of_index=2
        )

        self.assertEqual(len(before.candidate_cycles), 1)
        before_cycle = before.candidate_cycles.iloc[0]
        self.assertEqual(before_cycle["invalid_reason"], "")
        self.assertIsNone(before_cycle["superseded_date"])

        self.assertEqual(len(on_start.candidate_cycles), 2)
        old_cycle, new_cycle = on_start.candidate_cycles.to_dict("records")
        self.assertEqual(old_cycle["invalid_reason"], "SUPERSEDED_BY_NEW_CYCLE")
        self.assertEqual(old_cycle["superseded_date"], days[2])
        self.assertEqual(new_cycle["sequence_start_date"], days[2])

    def test_naturally_expired_cycle_is_not_marked_superseded(self):
        days = trading_days(24)
        boards = board_frame(
            [(days[0], CODE, 3, "测试股"), (days[22], CODE, 3, "测试股")]
        )

        result = replay_one(days=days, boards=boards, as_of_index=22)
        old_cycle, new_cycle = result.candidate_cycles.to_dict("records")

        self.assertEqual(old_cycle["current_stage"], "EXPIRED")
        self.assertEqual(old_cycle["invalid_reason"], "")
        self.assertIsNone(old_cycle["superseded_date"])
        self.assertEqual(new_cycle["sequence_start_date"], days[22])

    def test_cycle_fully_invalidated_by_rsi_floor_is_not_marked_superseded(self):
        days = trading_days(7)
        boards = board_frame(
            [(days[0], CODE, 3, "测试股"), (days[3], CODE, 3, "测试股")]
        )
        bars = indicator_bars(days, rsi={1: 59.0})

        result = replay_one(
            days=days, boards=boards, bars=bars, as_of_index=4
        )
        old_cycle, new_cycle = result.candidate_cycles.to_dict("records")

        self.assertEqual(old_cycle["md2_result"], "RSI_BELOW_60")
        self.assertEqual(old_cycle["invalid_reason"], "")
        self.assertIsNone(old_cycle["superseded_date"])
        self.assertEqual(new_cycle["sequence_start_date"], days[3])


if __name__ == "__main__":
    unittest.main()
