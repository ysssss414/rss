from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from research.amazingdata_benchmark import (completed, endpoint_metrics,
    full_market_eta, save_checkpoint, select_sample)
from scripts.benchmark_amazingdata_100 import quality


class BenchmarkTests(unittest.TestCase):
    def test_selection_is_deterministic_and_stratified(self):
        codes = ([f"600{i:03d}.SH" for i in range(100)] +
                 [f"000{i:03d}.SZ" for i in range(100)] +
                 [f"688{i:03d}.SH" for i in range(100)] +
                 [f"300{i:03d}.SZ" for i in range(100)] +
                 ["600518.SH", "603194.SH", "001331.SZ", "301607.SZ"])
        first = select_sample(codes)
        self.assertEqual(first, select_sample(list(reversed(codes))))
        self.assertEqual(len(first), 100)
        self.assertEqual({row["board"]: sum(item["board"] == row["board"] for item in first)
                          for row in first}, {"SSE Main": 25, "SZSE Main": 25,
                                             "STAR": 25, "ChiNext": 25})
        self.assertIn("603194.SH", [row["security_id"] for row in first])

    def test_metrics_and_batch_eta(self):
        records = {
            "a": {"endpoint": "D3_bars", "elapsed_seconds": 2.0, "rows": 10,
                  "attempts": 2, "status": "SUCCESS", "security_count": 1,
                  "attempt_failures": [{"type": "TimeoutError"}]},
            "b": {"endpoint": "D3_bars", "elapsed_seconds": 3.0, "rows": 20,
                  "attempts": 1, "status": "SUCCESS", "security_count": 1,
                  "attempt_failures": []},
            "c": {"endpoint": "D1_basic", "elapsed_seconds": 4.0, "rows": 80,
                  "attempts": 1, "status": "SUCCESS", "security_count": 80,
                  "attempt_failures": []},
        }
        bars = endpoint_metrics(records, "D3_bars", 2)
        basic = endpoint_metrics(records, "D1_basic", 80)
        self.assertEqual((bars["request_count"], bars["retry_count"], bars["failed_requests"]), (3, 1, 1))
        self.assertEqual(bars["seconds_per_security"], 2.5)
        eta = full_market_eta([basic, bars], 160)
        self.assertEqual(eta["endpoint_eta"]["D1_basic"]["linear_seconds"], 8)
        self.assertEqual(eta["endpoint_eta"]["D3_bars"]["linear_seconds"], 400)
        self.assertEqual(eta["eta_buffered_seconds"], 489.6)
        measured = full_market_eta([basic, bars], 160, total_wall_seconds=20)
        self.assertEqual(measured["eta_linear_seconds"], 32)
        self.assertEqual(measured["eta_buffered_seconds"], 38.4)

    def test_checkpoint_resume_requires_intact_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "one.csv.gz"
            cache.write_bytes(b"data")
            from hashlib import sha256
            state = {"records": {"D3:one": {"status": "SUCCESS", "cache_file": cache.name,
                "sha256": sha256(b"data").hexdigest()}}}
            path = root / "checkpoint.json"
            save_checkpoint(path, state)
            self.assertTrue(completed(state, "D3:one", root))
            cache.write_bytes(b"changed")
            self.assertFalse(completed(state, "D3:one", root))
            self.assertIn("D3:one", path.read_text(encoding="utf-8"))

    def test_repeated_factor_value_is_not_duplicate_date(self):
        frame = pd.DataFrame({"600519.SH": [1.0, 1.0]},
            index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
        self.assertEqual(quality(frame, "D5_factor")["duplicate_rows"], 0)
        frame.index = pd.to_datetime(["2024-01-02", "2024-01-02"])
        self.assertEqual(quality(frame, "D5_factor")["duplicate_factor_date"], 1)

    def test_status_row_without_identity_or_date_is_counted(self):
        frame = pd.DataFrame({"MARKET_CODE": [None], "TRADE_DATE": [None],
                              "HIGH_LIMITED": [None], "LOW_LIMITED": [None],
                              "IS_ST_SEC": [0], "IS_SUSP_SEC": [0]})
        counts = quality(frame, "D4_D6_status")
        self.assertEqual(counts["null_key_rows"], 1)
        self.assertEqual(counts["missing_HIGH_LIMITED"], 1)


if __name__ == "__main__":
    unittest.main()
