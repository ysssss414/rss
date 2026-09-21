from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from three_board_rsi_entry.market_data import (
    AmazingDataAdapter,
    AmazingDataMarketDataProvider,
    DataSourceError,
)

from .helpers import TEST_TEMP_ROOT


SYMBOL = "300308.SZ"


def legacy_raw_bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": [SYMBOL, SYMBOL],
            "date": ["2026-01-01", "2026-01-02"],
            "open": [100.0, 200.0],
            "high": [110.0, 220.0],
            "low": [90.0, 180.0],
            "close": [105.0, 210.0],
            "volume": [1000.0, 2000.0],
            "amount": [100000.0, 400000.0],
        }
    )


class AmazingDataReferenceTests(unittest.TestCase):
    def test_conflicting_duplicate_is_a_data_quality_correction(self):
        raw = pd.concat(
            [
                legacy_raw_bars().iloc[::-1],
                legacy_raw_bars().iloc[[1]].assign(close=211.0),
            ]
        )
        with self.assertRaisesRegex(DataSourceError, "CONFLICTING_DUPLICATE"):
            AmazingDataAdapter._validate_bars(raw, SYMBOL)

    def test_legacy_bar_validation_rejects_missing_amount(self):
        raw = legacy_raw_bars()
        raw.loc[0, "amount"] = None
        with self.assertRaises(DataSourceError):
            AmazingDataAdapter._validate_bars(raw, SYMBOL)

    def test_backward_factor_is_forward_filled_and_normalized_to_end(self):
        bars = pd.DataFrame(
            {
                "trade_date": [
                    date(2026, 1, 1),
                    date(2026, 1, 2),
                    date(2026, 1, 3),
                ],
                "ts_code": [SYMBOL] * 3,
                "open": [100.0, 200.0, 300.0],
                "high": [110.0, 220.0, 330.0],
                "low": [90.0, 180.0, 270.0],
                "close": [105.0, 210.0, 315.0],
                "amount": [1.0, 2.0, 3.0],
                "suspended": [False] * 3,
            }
        )
        factors = pd.DataFrame(
            {SYMBOL: [1.0, 2.0]},
            index=pd.to_datetime(["2026-01-01", "2026-01-03"]),
        )
        adjusted = AmazingDataMarketDataProvider._apply_forward_adjustment(
            bars, factors, SYMBOL
        )
        self.assertEqual(adjusted["close"].tolist(), [52.5, 105.0, 315.0])
        self.assertEqual(adjusted["amount"].tolist(), [1.0, 2.0, 3.0])

    def test_provider_wiring_uses_verified_legacy_provider(self):
        class FakeBase:
            def get_backward_factor(
                self, codes, local_path, is_local=True
            ):
                del local_path, is_local
                return pd.DataFrame(
                    {codes[0]: [1.0, 2.0]},
                    index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
                )

        class FakeProvider:
            last_instance = None

            def __init__(self):
                self.base = FakeBase()
                self.ad = SimpleNamespace(constant=SimpleNamespace(Period=SimpleNamespace(day=SimpleNamespace(value="day"))))
                self.logged_in = False
                self.logged_out = False
                FakeProvider.last_instance = self

            def login(self):
                self.logged_in = True

            def logout(self):
                self.logged_out = True

            def get_trade_calendar(self):
                return [20260101, 20260102]

            def _ensure_market(self):
                return self

            def query_kline(self, codes, **kwargs):
                return {codes[0]: legacy_raw_bars()}

        cache_dir = Path(TEST_TEMP_ROOT) / "reference_adapter_cache"
        provider = AmazingDataMarketDataProvider(
            cache_dir,
            legacy_provider_root=Path.cwd(),
            retry_delay_seconds=0,
        )
        provider._adapter._load_legacy_module = lambda: SimpleNamespace(
            AmazingDataProvider=FakeProvider
        )
        result = provider.daily_bars(
            [SYMBOL],
            date(2026, 1, 1),
            date(2026, 1, 2),
            price_adjustment="qfq",
            force_refresh=True,
        )
        self.assertEqual(result["close"].tolist(), [52.5, 210.0])
        self.assertTrue(FakeProvider.last_instance.logged_in)
        provider.close()
        self.assertTrue(FakeProvider.last_instance.logged_out)

    def test_retry_preserves_failure_until_a_later_success(self):
        adapter = AmazingDataAdapter(
            legacy_provider_root=Path.cwd(),
            cache_dir=Path(TEST_TEMP_ROOT) / "retry_cache",
            retry_count=2,
            retry_delay_seconds=0,
        )

        @contextmanager
        def fake_provider():
            yield object()

        adapter._provider = fake_provider
        attempts = 0

        def operation(_provider):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("temporary")
            return "ok"

        self.assertEqual(adapter._call_with_retry(operation), "ok")
        self.assertEqual(attempts, 2)

    def test_provider_close_contains_unstable_native_logout_exit(self):
        provider = AmazingDataMarketDataProvider(
            Path(TEST_TEMP_ROOT) / "logout_cache",
            legacy_provider_root=Path.cwd(),
        )

        def request_exit():
            raise SystemExit(0)

        provider._adapter.close = request_exit
        provider.close()


if __name__ == "__main__":
    unittest.main()
