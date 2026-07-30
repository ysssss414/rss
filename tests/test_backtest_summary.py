from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from three_board_rsi_entry.backtest import (
    build_signal_summary,
    build_stratified_summary,
)


def summary_events() -> pd.DataFrame:
    rows = []
    values = [
        ("MD_1", "cycle-a", 0.10, 3, 3, 2, "2024-01-02"),
        ("MD_1", "cycle-b", -0.05, 4, 4, 5, "2024-02-02"),
        ("MD_2", "cycle-c", 0.00, 5, 6, 10, "2025-01-02"),
        ("MD_2", "cycle-d", np.nan, 3, 3, 15, "2025-02-02"),
    ]
    for signal_type, cycle_id, value, qualified, maximum, observation, signal_date in values:
        row = {
            "signal_date": pd.Timestamp(signal_date).date(),
            "candidate_cycle_id": cycle_id,
            "signal_type": signal_type,
            "qualified_board_count": qualified,
            "max_board_count": maximum,
            "observation_day": observation,
        }
        for period in (1, 3, 5, 10):
            row[f"return_{period}d"] = value
            row[f"mfe_{period}d"] = value + 0.04 if pd.notna(value) else np.nan
            row[f"mae_{period}d"] = value - 0.03 if pd.notna(value) else np.nan
            row[f"holding_status_{period}d"] = (
                "COMPLETED" if pd.notna(value) else "INSUFFICIENT_FORWARD_DATA"
            )
        rows.append(row)
    return pd.DataFrame(rows)


class SignalSummaryTests(unittest.TestCase):
    def test_summary_metrics_use_only_completed_samples(self):
        events = summary_events()
        first = events.iloc[[0, 2]].copy()
        summary = build_signal_summary(events, first)
        row = summary[
            (summary["sample_scope"] == "ALL_SIGNALS")
            & (summary["holding_period"] == 5)
        ].iloc[0]

        self.assertEqual(row["completed_count"], 3)
        self.assertEqual(row["missing_count"], 1)
        self.assertAlmostEqual(row["win_rate"], 1.0 / 3.0)
        self.assertAlmostEqual(row["mean_return"], 0.05 / 3.0)
        self.assertAlmostEqual(row["median_return"], 0.0)
        self.assertAlmostEqual(row["p25_return"], -0.025)
        self.assertAlmostEqual(row["p75_return"], 0.05)
        self.assertAlmostEqual(row["average_positive_return"], 0.10)
        self.assertAlmostEqual(row["average_negative_return"], -0.05)
        self.assertAlmostEqual(row["payoff_ratio"], 2.0)
        self.assertAlmostEqual(row["return_gt_5pct_rate"], 1.0 / 3.0)
        self.assertAlmostEqual(row["return_lt_minus5pct_rate"], 0.0)

    def test_md1_md2_and_first_signal_scopes_are_separate(self):
        events = summary_events()
        first = events.iloc[[0, 2]].copy()
        summary = build_signal_summary(events, first)
        rows = summary[summary["holding_period"] == 1].set_index("sample_scope")

        self.assertEqual(rows.loc["ALL_SIGNALS", "sample_count"], 4)
        self.assertEqual(rows.loc["MD_1", "sample_count"], 2)
        self.assertEqual(rows.loc["MD_2", "sample_count"], 2)
        self.assertEqual(rows.loc["FIRST_SIGNAL_PER_CYCLE", "sample_count"], 2)

    def test_payoff_ratio_is_missing_without_both_positive_and_negative_samples(self):
        events = summary_events().iloc[[0, 2]].copy()
        summary = build_signal_summary(events, events)
        row = summary[
            (summary["sample_scope"] == "ALL_SIGNALS")
            & (summary["holding_period"] == 10)
        ].iloc[0]

        self.assertTrue(pd.isna(row["payoff_ratio"]))


class StratifiedSummaryTests(unittest.TestCase):
    def test_limited_strata_and_board_buckets_are_correct(self):
        result = build_stratified_summary(summary_events())

        self.assertEqual(
            set(result["dimension"]),
            {
                "signal_type",
                "qualified_board_count",
                "max_board_count",
                "observation_day",
                "signal_year",
            },
        )
        qualified = result[result["dimension"] == "qualified_board_count"]
        self.assertEqual(
            set(qualified["group"]),
            {"3板", "4板", "5板及以上"},
        )
        five_plus = qualified[qualified["group"] == "5板及以上"].iloc[0]
        self.assertEqual(five_plus["sample_count"], 1)

    def test_observation_day_buckets_are_correct(self):
        result = build_stratified_summary(summary_events())
        observation = result[result["dimension"] == "observation_day"]

        self.assertEqual(
            set(observation["group"]),
            {"1—3日", "4—7日", "8—12日", "13—20日"},
        )
        self.assertTrue((observation["sample_count"] == 1).all())

    def test_incomplete_events_do_not_enter_completed_return_metrics(self):
        result = build_stratified_summary(summary_events())
        md2 = result[
            (result["dimension"] == "signal_type")
            & (result["group"] == "MD_2")
        ].iloc[0]

        self.assertEqual(md2["sample_count"], 2)
        self.assertEqual(md2["completed_5d_count"], 1)
        self.assertAlmostEqual(md2["5d_mean_return"], 0.0)


if __name__ == "__main__":
    unittest.main()
