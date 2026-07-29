from __future__ import annotations

import importlib
import logging
import os
import sys
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Iterator

import numpy as np
import pandas as pd

from .input_excel import normalize_ts_code


LOGGER = logging.getLogger(__name__)
BAR_COLUMNS = [
    "trade_date",
    "ts_code",
    "open",
    "high",
    "low",
    "close",
    "amount",
    "suspended",
]
LEGACY_BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]


class DataSourceError(RuntimeError):
    """AmazingData returned invalid data or the verified provider was unavailable."""


def _install_numba_compat() -> None:
    """Install the no-JIT shim verified by the sibling RSI project.

    AmazingData 1.1.6 contains pyc-only operator modules whose ``cache=True``
    decorators cannot be located by real Numba. Daily-bar access only needs the
    decorator semantics, so this compatibility path runs those functions
    without JIT caching.
    """

    module = ModuleType("numba")

    def njit(*args: Any, **kwargs: Any) -> Any:
        if args and callable(args[0]) and len(args) == 1 and not kwargs:
            return args[0]

        def decorator(function: Any) -> Any:
            return function

        return decorator

    module.njit = njit  # type: ignore[attr-defined]
    module.jit = njit  # type: ignore[attr-defined]
    module.prange = range  # type: ignore[attr-defined]
    sys.modules["numba"] = module


def _date_value(value: object) -> date:
    text = str(value).strip()
    digits = text.split(".", 1)[0]
    if digits.isdigit() and len(digits) >= 8:
        return pd.to_datetime(digits[:8], format="%Y%m%d").date()
    return pd.Timestamp(text).date()


def _bool_value(value: object) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "suspended", "停牌"}:
            return True
        if text in {"0", "false", "no", "n", "trading", "交易"}:
            return False
    return bool(value)


def normalize_bars(frame: pd.DataFrame, *, default_code: str | None = None) -> pd.DataFrame:
    data = frame.copy()
    if "trade_date" not in data:
        for alternative in ("kline_time", "date", "datetime"):
            if alternative in data:
                data = data.rename(columns={alternative: "trade_date"})
                break
        else:
            if not isinstance(data.index, pd.RangeIndex):
                data = data.reset_index()
                data = data.rename(columns={data.columns[0]: "trade_date"})
    if "ts_code" not in data:
        if default_code is None:
            raise ValueError("Market data is missing ts_code")
        data["ts_code"] = default_code

    aliases = {
        "suspend_flag": "suspended",
        "is_suspended": "suspended",
    }
    if "amount" not in data:
        for alternative in ("turnover_value", "value_trade"):
            if alternative in data:
                aliases[alternative] = "amount"
                break
    data = data.rename(columns={k: v for k, v in aliases.items() if k in data})
    required = {"trade_date", "ts_code", "open", "high", "low", "close", "amount"}
    missing = sorted(required - set(data.columns))
    if missing:
        raise ValueError(f"Market data missing fields: {', '.join(missing)}")
    if "suspended" not in data:
        data["suspended"] = data[["open", "high", "low", "close"]].isna().all(axis=1)

    data["trade_date"] = data["trade_date"].map(_date_value)
    data["ts_code"] = data["ts_code"].map(normalize_ts_code)
    for column in ["open", "high", "low", "close", "amount"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["suspended"] = data["suspended"].map(_bool_value).astype(bool)
    duplicated = data.duplicated(["trade_date", "ts_code"], keep=False)
    if duplicated.any():
        details = ", ".join(
            f"{row.ts_code} {row.trade_date}"
            for row in data.loc[duplicated].itertuples(index=False)
        )
        raise ValueError(f"Duplicate market bars: {details}")
    return data[BAR_COLUMNS].sort_values(
        ["ts_code", "trade_date"], kind="stable"
    ).reset_index(drop=True)


class MarketDataProvider(ABC):
    @abstractmethod
    def trading_days(self, start_date: date, end_date: date) -> list[date]:
        raise NotImplementedError

    @abstractmethod
    def daily_bars(
        self,
        codes: Iterable[str],
        start_date: date,
        end_date: date,
        *,
        price_adjustment: str,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        raise NotImplementedError

    def close(self) -> None:
        return None


class CsvMarketDataProvider(MarketDataProvider):
    """Offline adapter for deterministic tests and smoke runs.

    The CSV represents prices already adjusted according to its optional
    ``price_adjustment`` column. It must not mix adjustment modes.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        try:
            raw = pd.read_csv(self.path)
        except FileNotFoundError as exc:
            raise ValueError(f"Market-data CSV not found: {self.path}") from exc
        if "price_adjustment" in raw:
            modes = set(raw["price_adjustment"].dropna().astype(str).str.lower())
            if len(modes) > 1:
                raise ValueError("Market-data CSV mixes price_adjustment modes")
            self.price_adjustment = next(iter(modes), None)
        else:
            self.price_adjustment = None
        self._bars = normalize_bars(raw)

    def trading_days(self, start_date: date, end_date: date) -> list[date]:
        dates = sorted(set(self._bars["trade_date"]))
        return [value for value in dates if start_date <= value <= end_date]

    def daily_bars(
        self,
        codes: Iterable[str],
        start_date: date,
        end_date: date,
        *,
        price_adjustment: str,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        del force_refresh
        if self.price_adjustment and self.price_adjustment != price_adjustment:
            raise ValueError(
                f"CSV price_adjustment is {self.price_adjustment!r}, "
                f"but config requests {price_adjustment!r}"
            )
        normalized_codes = {normalize_ts_code(code) for code in codes}
        mask = (
            self._bars["ts_code"].isin(normalized_codes)
            & (self._bars["trade_date"] >= start_date)
            & (self._bars["trade_date"] <= end_date)
        )
        return self._bars.loc[mask].copy().reset_index(drop=True)


class AmazingDataAdapter:
    """Reuse the audited legacy AmazingDataProvider used by the RSI project."""

    def __init__(
        self,
        *,
        legacy_provider_root: Path | str,
        cache_dir: Path | str,
        retry_count: int = 3,
        retry_delay_seconds: float = 1.0,
        use_numba_compat: bool = True,
    ) -> None:
        self.legacy_provider_root = Path(legacy_provider_root).resolve()
        self.cache_dir = Path(cache_dir).resolve()
        self.retry_count = max(1, int(retry_count))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))
        self.use_numba_compat = bool(use_numba_compat)
        self._session_provider: Any | None = None

    def get_trade_calendar(self) -> list[int]:
        values = self._call_with_retry(
            lambda provider: provider.get_trade_calendar()
        )
        return sorted({int(value) for value in values})

    def get_daily_bars(
        self, symbol: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        raw = self._call_with_retry(
            lambda provider: provider.get_daily_bars(
                symbol,
                int(start_date.strftime("%Y%m%d")),
                int(end_date.strftime("%Y%m%d")),
            )
        )
        return self._validate_bars(raw, symbol)

    def get_backward_factor(
        self, symbol: str, *, force_refresh: bool
    ) -> pd.DataFrame:
        def operation(provider: Any) -> pd.DataFrame:
            base = getattr(provider, "base", None)
            if base is None or not hasattr(base, "get_backward_factor"):
                raise DataSourceError(
                    "Verified AmazingDataProvider does not expose "
                    "BaseData.get_backward_factor"
                )
            factor_dir = self.cache_dir / "factors"
            factor_dir.mkdir(parents=True, exist_ok=True)
            raw = base.get_backward_factor(
                [symbol],
                local_path=str(factor_dir) + os.sep,
                is_local=not force_refresh,
            )
            return pd.DataFrame(raw).copy()

        return self._call_with_retry(operation)

    def close(self) -> None:
        if self._session_provider is None:
            return
        self._session_provider.logout()
        self._session_provider = None

    def _call_with_retry(self, operation: Any) -> Any:
        last_error: BaseException | None = None
        for attempt in range(1, self.retry_count + 1):
            try:
                with self._provider() as provider:
                    return operation(provider)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                last_error = exc
                LOGGER.warning(
                    "AmazingData attempt %s/%s failed: %s",
                    attempt,
                    self.retry_count,
                    exc,
                )
                if attempt < self.retry_count and self.retry_delay_seconds:
                    time.sleep(self.retry_delay_seconds)
        assert last_error is not None
        raise DataSourceError(
            f"AmazingData request failed after {self.retry_count} attempts: "
            f"{last_error}"
        ) from last_error

    @contextmanager
    def _provider(self) -> Iterator[Any]:
        if self._session_provider is not None:
            yield self._session_provider
            return
        module = self._load_legacy_module()
        provider_class = getattr(module, "AmazingDataProvider", None)
        if provider_class is None:
            raise DataSourceError(
                "Verified provider module does not contain AmazingDataProvider"
            )
        if self.use_numba_compat:
            _install_numba_compat()
        provider = provider_class()
        provider.login()
        self._session_provider = provider
        try:
            yield provider
        finally:
            # AmazingData 1.1.6 logout is unstable in the audited Windows
            # runtime. Keep the single session until outputs have been written.
            LOGGER.debug("Keeping AmazingData session until provider.close()")

    def _load_legacy_module(self) -> ModuleType:
        provider_file = (
            self.legacy_provider_root / "yh_quant_shape" / "data_provider.py"
        )
        if not provider_file.exists():
            raise DataSourceError(
                f"Verified AmazingDataProvider not found: {provider_file}"
            )
        root_text = str(self.legacy_provider_root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        return importlib.import_module("yh_quant_shape.data_provider")

    @staticmethod
    def _validate_bars(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if not isinstance(raw, pd.DataFrame):
            raise DataSourceError("AmazingData daily bars must be a DataFrame")
        frame = raw.copy()
        missing = set(LEGACY_BAR_COLUMNS) - set(frame.columns)
        if missing:
            raise DataSourceError(
                f"{symbol} daily bars missing fields: {', '.join(sorted(missing))}"
            )
        frame = frame[LEGACY_BAR_COLUMNS].copy()
        frame["date"] = pd.to_datetime(
            frame["date"].astype(str), errors="coerce"
        )
        for column in LEGACY_BAR_COLUMNS[1:]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if frame.empty:
            return frame
        if frame.isna().any().any():
            bad = frame.columns[frame.isna().any()].tolist()
            raise DataSourceError(
                f"{symbol} daily bars contain missing values: {', '.join(bad)}"
            )
        frame = (
            frame.sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True)
        )
        invalid = (
            (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
            | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
            | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
            | (frame[["volume", "amount"]] < 0).any(axis=1)
        )
        if invalid.any():
            dates = frame.loc[invalid, "date"].dt.strftime("%Y-%m-%d").tolist()
            raise DataSourceError(
                f"{symbol} daily bars contain invalid OHLC/trading values: "
                f"{dates[:5]}"
            )
        frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
        return frame


class _RawCsvCache:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, code: str) -> Path:
        safe = "".join(character if character.isalnum() else "_" for character in code)
        return self.directory / f"{safe}.csv"

    def _coverage_path(self, code: str) -> Path:
        safe = "".join(character if character.isalnum() else "_" for character in code)
        return self.directory / f"{safe}.coverage.csv"

    def read(self, code: str) -> pd.DataFrame:
        path = self._path(code)
        if not path.exists():
            return pd.DataFrame(columns=BAR_COLUMNS)
        return normalize_bars(pd.read_csv(path), default_code=code)

    def upsert(self, code: str, new_rows: pd.DataFrame) -> pd.DataFrame:
        current = self.read(code)
        combined = (
            new_rows.copy()
            if current.empty
            else pd.concat([current, new_rows], ignore_index=True)
        )
        combined = combined.drop_duplicates(
            ["trade_date", "ts_code"], keep="last"
        ).sort_values(["ts_code", "trade_date"], kind="stable")
        path = self._path(code)
        serializable = combined.copy()
        serializable["trade_date"] = serializable["trade_date"].map(date.isoformat)
        serializable.to_csv(path, index=False, lineterminator="\n")
        return combined.reset_index(drop=True)

    def read_coverage(self, code: str) -> set[date]:
        path = self._coverage_path(code)
        if not path.exists():
            return set()
        frame = pd.read_csv(path)
        if "trade_date" not in frame:
            return set()
        return {_date_value(value) for value in frame["trade_date"]}

    def mark_covered(self, code: str, covered_dates: Iterable[date]) -> None:
        combined = self.read_coverage(code) | set(covered_dates)
        frame = pd.DataFrame(
            {"trade_date": [value.isoformat() for value in sorted(combined)]}
        )
        frame.to_csv(
            self._coverage_path(code),
            index=False,
            lineterminator="\n",
        )


class AmazingDataMarketDataProvider(MarketDataProvider):
    """Project-facing adapter backed by the RSI project's verified provider."""

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        legacy_provider_root: Path | str | None = None,
        retry_count: int = 3,
        retry_delay_seconds: float = 1.0,
        use_numba_compat: bool = True,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache = _RawCsvCache(self.cache_dir / "raw_daily")
        provider_root = self._resolve_legacy_provider_root(legacy_provider_root)
        self._adapter = AmazingDataAdapter(
            legacy_provider_root=provider_root,
            cache_dir=self.cache_dir,
            retry_count=retry_count,
            retry_delay_seconds=retry_delay_seconds,
            use_numba_compat=use_numba_compat,
        )
        self._calendar_raw: list[int] = []

    @staticmethod
    def _resolve_legacy_provider_root(
        configured: Path | str | None,
    ) -> Path:
        if configured is not None:
            return Path(configured).resolve()
        environment_value = os.getenv("AMAZINGDATA_LEGACY_PROVIDER_ROOT")
        if environment_value:
            return Path(environment_value).resolve()
        sibling = Path(__file__).resolve().parents[2] / "yh"
        provider_file = sibling / "yh_quant_shape" / "data_provider.py"
        if provider_file.exists():
            return sibling
        raise DataSourceError(
            "AmazingData legacy provider root is not configured. Pass "
            "--legacy-provider-root or set AMAZINGDATA_LEGACY_PROVIDER_ROOT."
        )

    def trading_days(self, start_date: date, end_date: date) -> list[date]:
        if not self._calendar_raw:
            self._calendar_raw = self._adapter.get_trade_calendar()
        return [
            _date_value(value)
            for value in self._calendar_raw
            if int(start_date.strftime("%Y%m%d"))
            <= value
            <= int(end_date.strftime("%Y%m%d"))
        ]

    @staticmethod
    def _missing_ranges(requested: list[date], available: set[date]) -> list[tuple[date, date]]:
        ranges: list[tuple[date, date]] = []
        start: date | None = None
        previous: date | None = None
        for value in requested:
            if value in available:
                if start is not None and previous is not None:
                    ranges.append((start, previous))
                    start = None
                previous = value
                continue
            if start is None:
                start = value
            previous = value
        if start is not None and previous is not None:
            ranges.append((start, previous))
        return ranges

    def _fetch_raw_code(
        self, code: str, requested: list[date], force_refresh: bool
    ) -> pd.DataFrame:
        cached = (
            pd.DataFrame(columns=BAR_COLUMNS) if force_refresh else self._cache.read(code)
        )
        covered = set() if force_refresh else self._cache.read_coverage(code)
        if not covered and not cached.empty:
            # Backward compatibility with caches created before coverage files
            # were added: known bar dates are still safe cache hits.
            covered = set(cached["trade_date"])
        ranges = self._missing_ranges(requested, covered)
        fetched_parts: list[pd.DataFrame] = []
        for range_start, range_end in ranges:
            raw = self._adapter.get_daily_bars(code, range_start, range_end)
            if not raw.empty:
                fetched_parts.append(normalize_bars(raw, default_code=code))
            covered_dates = [
                value
                for value in requested
                if range_start <= value <= range_end
            ]
            # Coverage records requested calendar dates without fabricating
            # price rows for suspensions or pre-listing dates.
            self._cache.mark_covered(code, covered_dates)
        if fetched_parts:
            cached = self._cache.upsert(code, pd.concat(fetched_parts, ignore_index=True))
        mask = cached["trade_date"].isin(requested)
        return cached.loc[mask].copy()

    def _apply_qfq(
        self,
        bars: pd.DataFrame,
        codes: list[str],
        *,
        force_refresh: bool,
    ) -> pd.DataFrame:
        adjusted = bars.copy()
        for code in codes:
            mask = adjusted["ts_code"] == code
            code_bars = adjusted.loc[mask].copy()
            if code_bars.empty:
                continue
            factors = self._adapter.get_backward_factor(
                code, force_refresh=force_refresh
            )
            adjusted.loc[mask, ["open", "high", "low", "close"]] = (
                self._apply_forward_adjustment(code_bars, factors, code)[
                    ["open", "high", "low", "close"]
                ].to_numpy()
            )
        return adjusted

    @staticmethod
    def _apply_forward_adjustment(
        raw: pd.DataFrame, factor_raw: pd.DataFrame, symbol: str
    ) -> pd.DataFrame:
        """Apply the RSI project's verified backward-factor normalization."""

        if factor_raw is None or factor_raw.empty:
            raise DataSourceError(f"{symbol} forward-adjustment factors are empty")
        factor = factor_raw.copy()
        if symbol in factor.columns:
            values = pd.to_numeric(factor[symbol], errors="coerce")
        elif len(factor.columns) == 1:
            values = pd.to_numeric(factor.iloc[:, 0], errors="coerce")
        else:
            raise DataSourceError(
                f"{symbol} column not found in forward-adjustment factors"
            )
        factor_dates = pd.to_datetime(
            factor.index.astype(str), errors="coerce"
        )
        if factor_dates.isna().all() and "date" in factor.columns:
            factor_dates = pd.to_datetime(
                factor["date"].astype(str), errors="coerce"
            )
        series = pd.Series(
            values.to_numpy(dtype=float), index=factor_dates
        ).dropna()
        series = series[~series.index.isna()].sort_index()
        series = series[~series.index.duplicated(keep="last")]
        bar_dates = pd.to_datetime(raw["trade_date"])
        aligned = series.reindex(bar_dates).ffill()
        if aligned.isna().any():
            first_missing = bar_dates[aligned.isna().to_numpy()][0]
            raise DataSourceError(
                f"{symbol} has no usable adjustment factor on "
                f"{first_missing:%Y-%m-%d}"
            )
        latest_factor = float(aligned.iloc[-1])
        if not np.isfinite(latest_factor) or latest_factor == 0:
            raise DataSourceError(f"{symbol} latest adjustment factor is invalid")
        ratio = aligned.to_numpy(dtype=float) / latest_factor
        output = raw.copy()
        for column in ("open", "high", "low", "close"):
            output[column] = pd.to_numeric(
                output[column], errors="raise"
            ) * ratio
        return output

    def daily_bars(
        self,
        codes: Iterable[str],
        start_date: date,
        end_date: date,
        *,
        price_adjustment: str,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        normalized_codes = sorted({normalize_ts_code(code) for code in codes})
        requested = self.trading_days(start_date, end_date)
        parts = [
            self._fetch_raw_code(code, requested, force_refresh)
            for code in normalized_codes
        ]
        bars = (
            pd.concat(parts, ignore_index=True)
            if parts
            else pd.DataFrame(columns=BAR_COLUMNS)
        )
        if price_adjustment == "qfq" and not bars.empty:
            bars = self._apply_qfq(
                bars,
                normalized_codes,
                force_refresh=force_refresh,
            )
        elif price_adjustment != "none":
            raise ValueError("price_adjustment must be 'qfq' or 'none'")
        return normalize_bars(bars)

    def close(self) -> None:
        try:
            self._adapter.close()
        except SystemExit:
            # Match the RSI validation scripts: outputs have already been
            # persisted, so an unstable native logout must not erase success.
            LOGGER.warning("AmazingData logout requested process exit")
