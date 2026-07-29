from __future__ import annotations

import unittest
from pathlib import Path

from three_board_rsi_entry.input_excel import load_input_workbook

from .helpers import CODE, TEST_TEMP_ROOT, trading_days, write_input_workbook


class InputWorkbookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.days = trading_days(5)
        self.path = Path(TEST_TEMP_ROOT) / "input_case.xlsx"

    def _confirmations(self):
        return [(day, 1, "") for day in self.days]

    def _load(self, allow_incomplete: bool = False):
        return load_input_workbook(
            self.path,
            start_date=self.days[0],
            as_of_date=self.days[-1],
            trading_days=self.days,
            allow_incomplete=allow_incomplete,
        )

    def test_duplicate_date_and_stock_is_rejected(self):
        write_input_workbook(
            self.path,
            self._confirmations(),
            [(self.days[0], CODE, 3), (self.days[0], CODE, 3)],
        )
        with self.assertRaisesRegex(ValueError, "Duplicate trade_date/ts_code"):
            self._load()

    def test_board_count_below_three_is_rejected(self):
        write_input_workbook(
            self.path, self._confirmations(), [(self.days[0], CODE, 2)]
        )
        with self.assertRaisesRegex(ValueError, "at least 3"):
            self._load()

    def test_adjacent_board_count_must_increment(self):
        write_input_workbook(
            self.path,
            self._confirmations(),
            [(self.days[0], CODE, 3), (self.days[1], CODE, 3)],
        )
        with self.assertRaisesRegex(ValueError, "increase by 1"):
            self._load()

    def test_strict_mode_rejects_missing_confirmation(self):
        confirmations = self._confirmations()
        confirmations.pop(2)
        write_input_workbook(self.path, confirmations, [])
        with self.assertRaisesRegex(ValueError, self.days[2].isoformat()):
            self._load()

    def test_allow_incomplete_returns_explicit_warning(self):
        confirmations = self._confirmations()
        confirmations.pop(2)
        write_input_workbook(self.path, confirmations, [])
        loaded = self._load(allow_incomplete=True)
        self.assertFalse(loaded.data_complete)
        self.assertEqual(loaded.missing_confirmation_dates, [self.days[2]])
        self.assertIn(self.days[2].isoformat(), loaded.warnings[0])

    def test_cycle_may_begin_at_four_boards(self):
        write_input_workbook(
            self.path, self._confirmations(), [(self.days[0], CODE, 4)]
        )
        loaded = self._load()
        self.assertEqual(int(loaded.boards.iloc[0]["board_count"]), 4)

    def test_future_invalid_rows_do_not_break_historical_replay(self):
        future = self.days[-1]
        write_input_workbook(
            self.path,
            self._confirmations(),
            [(self.days[0], CODE, 3), (future, CODE, 2)],
        )
        loaded = load_input_workbook(
            self.path,
            start_date=self.days[0],
            as_of_date=self.days[-2],
            trading_days=self.days[:-1],
        )
        self.assertEqual(len(loaded.boards), 1)


if __name__ == "__main__":
    unittest.main()
