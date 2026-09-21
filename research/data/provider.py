from __future__ import annotations

from datetime import date

import pandas as pd

from .adjustment import FACTOR_COLUMNS, adjusted_bars
from .cache import Snapshot
from .contracts import DataContractError, DataRequest
from .validation import available_mask, validate_bars


class ResearchDataProvider:
    def capabilities(self) -> dict[str, bool]:
        return {name: False for name in ("trading_calendar", "security_master", "security_status",
                                        "daily_bars", "adjustment_factors", "official_limit_prices")}

    def _unsupported(self, name):
        raise DataContractError("UNSUPPORTED_CAPABILITY", name)

    def trading_calendar(self, start, end):
        return self._unsupported("trading_calendar")

    def security_master(self, as_of):
        return self._unsupported("historical_security_master")

    def security_status(self, request, as_of):
        return self._unsupported("historical_security_status")

    def daily_bars(self, request):
        return self._unsupported("daily_bars")

    def adjustment_factors(self, request):
        return self._unsupported("adjustment_factors")


class SnapshotProvider(ResearchDataProvider):
    """Legacy adapter over a fixed snapshot; each call returns a causal copy."""
    def __init__(self, snapshot: Snapshot, *, price_anchor: date | None = None):
        self.snapshot = snapshot
        self.price_anchor = price_anchor

    def capabilities(self):
        caps = super().capabilities()
        caps.update(trading_calendar=self.snapshot.payload["metadata"]["calendar_verified"],
                    daily_bars=True,
                    adjustment_factors=self.snapshot.payload["metadata"]["factor_schema"] in {"daily", "effective_events"})
        return caps

    def _check_range(self, codes, start, end):
        bound = self.snapshot.payload["request"]
        if not set(codes) <= set(bound["codes"]):
            raise DataContractError("SYMBOL_MISMATCH", "Security absent from snapshot")
        if end > date.fromisoformat(bound["end"]):
            raise DataContractError("AS_OF_VIOLATION", "Request exceeds snapshot end")
        # Calendar probes may start before the snapshot; callers check actual warmup.

    def trading_calendar(self, start, end):
        self._check_range((), start, end)
        return [date.fromisoformat(d) for d in self.snapshot.payload["calendar"] if start.isoformat() <= d <= end.isoformat()]

    trading_days = trading_calendar

    def adjustment_factors(self, request: DataRequest):
        self._check_range(request.codes, request.start, request.end)
        frame = pd.DataFrame(self.snapshot.payload["factors"], columns=FACTOR_COLUMNS)
        if frame.empty:
            return frame
        return frame.loc[frame.ts_code.isin(request.codes) & (frame.trade_date <= request.end.isoformat())
                         & available_mask(frame, request.end)].copy()

    def daily_bars(self, codes, start_date=None, end_date=None, *, price_adjustment=None, force_refresh=False):
        if force_refresh:
            raise DataContractError("IMMUTABLE_SNAPSHOT", "Refresh creates another snapshot; it cannot change this provider")
        if not isinstance(codes, DataRequest):
            codes = tuple(codes)
            if not codes:
                return pd.DataFrame(columns=["trade_date", "ts_code", "open", "high", "low", "close", "amount", "suspended"])
        request = codes if isinstance(codes, DataRequest) else DataRequest(codes, start_date, end_date)
        self._check_range(request.codes, request.start, request.end)
        frame = self.snapshot.bars()
        mask = (frame.ts_code.isin(request.codes) & frame.trade_date.between(request.start.isoformat(), request.end.isoformat())
                & available_mask(frame, request.end))
        raw = validate_bars(frame.loc[mask], request)
        if price_adjustment is None:
            return raw
        # Non-trading/unknown records never fabricate legacy OHLC observations.
        raw = raw.loc[raw.trading_status.isin(["TRADING", "SUSPENDED"])].copy()
        adjusted = adjusted_bars(raw, self.adjustment_factors(request), mode=price_adjustment,
                                 anchor=self.price_anchor or request.end, as_of=request.end,
                                 factor_schema=self.snapshot.payload["metadata"]["factor_schema"])
        adjusted["trade_date"] = pd.to_datetime(adjusted.trade_date).dt.date
        adjusted["suspended"] = adjusted.suspended.astype(bool)
        return adjusted[["trade_date", "ts_code", "open", "high", "low", "close", "amount", "suspended"]].reset_index(drop=True)

    def close(self):
        pass
