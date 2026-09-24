from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from research.amazingdata_batch import (batch_groups, cache_complete, extended_sample,
    factor_quality, normalize_factors, save_json, stage1_eta)
from scripts import optimize_amazingdata_d5 as runner
from scripts.summarize_amazingdata_d5 import compare_factor


class BatchHelperTests(unittest.TestCase):
    def test_extended_sample_preserves_fixed_100_and_balances_blocks(self):
        fixed = []
        universe = []
        for prefix, suffix, board in (("600", "SH", "SSE Main"),
                                      ("000", "SZ", "SZSE Main"),
                                      ("688", "SH", "STAR"),
                                      ("300", "SZ", "ChiNext")):
            codes = [f"{prefix}{n:03d}.{suffix}" for n in range(125)]
            universe.extend(codes)
            fixed.extend({"security_id": code, "exchange": suffix, "board": board,
                          "listing_date": "20200101", "sample_reason": "fixed"}
                         for code in codes[:25])
        first = extended_sample(fixed, universe)
        self.assertEqual(first, extended_sample(fixed, list(reversed(universe))))
        self.assertEqual(first[:100], fixed)
        self.assertEqual(len({row["security_id"] for row in first}), 500)
        for block in range(5):
            self.assertEqual({board: sum(row["board"] == board
                             for row in first[block * 100:(block + 1) * 100])
                             for board in ("SSE Main", "SZSE Main", "STAR", "ChiNext")},
                             {"SSE Main": 25, "SZSE Main": 25, "STAR": 25, "ChiNext": 25})

    def test_groups_normalization_quality_and_eta(self):
        codes = [str(i) for i in range(100)]
        self.assertEqual([len(batch_groups(codes, n)) for n in (20, 50, 100)], [5, 2, 1])
        wide = pd.DataFrame({"A": [1.0, 1.1], "B": [2.0, 2.1]},
                            index=["2024-01-02", "2024-01-03"])
        frame = normalize_factors(wide, ["A", "B"], {"A": "20240101", "B": "20240103"})
        self.assertEqual(len(frame), 3)
        self.assertEqual(factor_quality(frame)["duplicate_security_date"], 0)
        self.assertEqual(len(frame.loc[frame.security_id.eq("B")]), 1)
        eta = stage1_eta(500, 3300, {"D1_basic": 40, "D3_bars": 1800,
                                          "D4_D6_status": 3900})
        self.assertAlmostEqual(eta["optimized_d5_eta_hours"], 5222 / 3600, places=3)
        self.assertGreater(eta["stage1_total_buffered_eta_hours"], eta["stage1_total_eta_hours"])

    def test_checkpoint_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "one.csv.gz"
            cache.write_bytes(b"data")
            from hashlib import sha256
            record = {"status": "COMPLETED", "cache_file": cache.name,
                      "sha256": sha256(b"data").hexdigest()}
            save_json(root / "checkpoint.json", {"records": [record]})
            self.assertTrue(cache_complete(record, root))
            cache.write_bytes(b"changed")
            self.assertFalse(cache_complete(record, root))

    def test_equivalence_is_keyed_not_row_order(self):
        left = pd.DataFrame({"security_id": ["A", "B"],
                             "trade_date": ["2024-01-02", "2024-01-03"],
                             "factor": [1.0, 2.0]})
        same = compare_factor(left, left.iloc[::-1], "same")
        self.assertEqual((same["missing_in_batch"], same["extra_in_batch"],
                          same["value_mismatch"]), (0, 0, 0))
        changed = left.copy()
        changed.loc[1, "factor"] = 3.0
        self.assertEqual(compare_factor(left, changed, "changed")["value_mismatch"], 1)


class AcquisitionTests(unittest.TestCase):
    def test_real_runner_logic_resumes_without_duplicate_remote_calls_and_recovers_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "old"
            old.mkdir()
            codes = [f"600{i:03d}.SH" for i in range(100)]
            with (old / "sample_universe.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["security_id", "exchange", "board",
                    "listing_date", "sample_reason"], lineterminator="\n")
                writer.writeheader()
                writer.writerows({"security_id": code, "exchange": "SH", "board": "SSE Main",
                                  "listing_date": "20200101", "sample_reason": "fixed"} for code in codes)
            calls = []
            logins = []

            class FakeBase:
                def get_backward_factor(self, group, **kwargs):
                    self_outer.assertEqual(kwargs["is_local"], False)
                    calls.append(tuple(group))
                    if len(calls) == 1:
                        raise TypeError("simulated SDK session error")
                    return pd.DataFrame({code: [1.0, 1.1] for code in group},
                                        index=["2024-01-02", "2024-01-03"])

            class FakeAdapter:
                def __init__(self, **kwargs):
                    self.base = FakeBase()
                    self._session_provider = self

            def fake_session(adapter):
                logins.append(1)
                return adapter

            self_outer = self
            with patch.object(runner, "OLD", old), patch.object(runner, "PRIVATE", root / "private"), \
                 patch.object(runner, "AmazingDataAdapter", FakeAdapter), \
                 patch.object(runner, "_install_numba_compat"), patch.object(runner, "session", fake_session), \
                 patch.object(runner.time, "sleep"):
                runner.acquire("batch20_100", None, False, 2)
                checkpoint = root / "private" / "batch20_100" / "checkpoint.json"
                first = json.loads(checkpoint.read_text(encoding="utf-8"))
                self.assertEqual(len([r for r in first["records"].values()
                                      if r["status"] == "COMPLETED"]), 2)
                self.assertEqual(first["records"]["batch_001"]["session_restart_count"], 1)
                runner.acquire("batch20_100", None, True, None)
                final = json.loads(checkpoint.read_text(encoding="utf-8"))
                self.assertEqual(len(calls), 6)  # five batches plus one failed attempt
                self.assertEqual(final["segments"][-1]["skipped_completed_batches"], 2)
                self.assertEqual(final["segments"][-1]["remote_request_attempts"], 3)
                frames = [pd.read_csv(root / "private" / "batch20_100" / r["cache_file"])
                          for r in final["records"].values()]
                output = pd.concat(frames, ignore_index=True)
                self.assertEqual(output.security_id.nunique(), 100)
                self.assertEqual(output.duplicated(["security_id", "trade_date"]).sum(), 0)
                self.assertEqual(len(logins), 3)  # first login, recovery, resumed process
                runner.acquire("batch20_100", None, True, None)
                self.assertEqual(len(calls), 6)
                self.assertEqual(len(json.loads(checkpoint.read_text(encoding="utf-8"))["segments"]), 2)
                manifest = old / "materialize.csv"
                with manifest.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=["security_id", "listing_date"],
                                            lineterminator="\n")
                    writer.writeheader()
                    writer.writerows({"security_id": code, "listing_date": "20200101"}
                                     for code in codes[:3])
                runner.acquire("materialize", 100, False, None, manifest)
                materialized = json.loads((root / "private" / "materialize" /
                                           "checkpoint.json").read_text(encoding="utf-8"))
                self.assertEqual(materialized["config"]["security_count"], 3)
                self.assertEqual(materialized["segments"][0]["remote_request_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
