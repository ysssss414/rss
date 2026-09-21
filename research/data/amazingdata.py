from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from research.runs.manifest import digest
from .cache import Snapshot, SnapshotStore
from .contracts import BAR_COLUMNS, SCHEMA_VERSION, DataContractError, DataRequest
from .provider import ResearchDataProvider
from .validation import validate_bars


class SessionInvalidated(ConnectionError):
    """A session failure requiring a fresh provider before retry."""


def checked_sdk_bars(provider, symbol: str, start: date, end: date) -> pd.DataFrame:
    """Keep the proven query_kline path, but validate its mapping before normalization.

    Never call legacy get_daily_bars: it can relabel a different dictionary member.
    """
    if not hasattr(provider, "_ensure_market") or not hasattr(provider, "ad"):
        raise DataContractError("UNSUPPORTED_CAPABILITY", "Identity-preserving SDK access unavailable")
    market = provider._ensure_market()
    response = market.query_kline([symbol], begin_date=int(start.strftime("%Y%m%d")),
                                  end_date=int(end.strftime("%Y%m%d")), period=provider.ad.constant.Period.day.value)
    if not isinstance(response, dict):
        raise DataContractError("SCHEMA_MISMATCH", "Expected security-keyed SDK response")
    if not response:
        return pd.DataFrame(columns=["code", "date", "open", "high", "low", "close", "volume", "amount"])
    if symbol not in response or set(response) != {symbol}:
        raise DataContractError("SYMBOL_MISMATCH", "SDK response keys differ from requested security")
    raw = pd.DataFrame(response[symbol]).copy()
    for column in ("code", "ts_code"):
        if column in raw and (raw[column].isna().any() or not raw[column].eq(symbol).all()):
            raise DataContractError("SYMBOL_MISMATCH", "SDK row identity disagrees with response key")
    if raw.empty:
        return pd.DataFrame(columns=["code", "date", "open", "high", "low", "close", "volume", "amount"])
    raw["code"] = symbol  # Trusted mapping key was checked before adding identity.
    if "kline_time" in raw:
        raw["date"] = pd.to_datetime(raw.kline_time, errors="raise").dt.strftime("%Y-%m-%d")
    elif "date" in raw:
        raw["date"] = pd.to_datetime(raw.date.astype(str), errors="raise").dt.strftime("%Y-%m-%d")
    else:
        if isinstance(raw.index, pd.RangeIndex):
            raise DataContractError("INVALID_DATE", "SDK response lacks date identity")
        raw["date"] = pd.to_datetime(raw.index, errors="raise").strftime("%Y-%m-%d")
    if not raw.date.between(start.isoformat(), end.isoformat()).all():
        raise DataContractError("AS_OF_VIOLATION", "SDK returned out-of-range bars")
    return raw


def canonical_legacy_bars(raw: pd.DataFrame, symbol: str, *, retrieved_at: str,
                          units_verified: bool) -> pd.DataFrame:
    required = {"code", "date", "open", "high", "low", "close", "volume", "amount"}
    if not required.issubset(raw):
        raise DataContractError("SCHEMA_MISMATCH", "Identity-preserving OHLCV response required")
    if not raw.code.eq(symbol).all():
        raise DataContractError("SYMBOL_MISMATCH", "Raw bar identity differs from request")
    frame = raw.rename(columns={"code": "ts_code", "date": "trade_date",
                                **{p: f"raw_{p}" for p in ("open", "high", "low", "close")}}).copy()
    if "trading_status" not in frame:
        frame["trading_status"] = "TRADING"
        frame.loc[pd.to_numeric(frame.volume, errors="coerce").eq(0), "trading_status"] = "UNKNOWN"
    frame["suspended"] = frame.trading_status.map({"TRADING": False, "SUSPENDED": True})
    # This is an EOD price-observation assumption, not verified historical publication time.
    frame["available_at"] = frame.trade_date.astype(str) + "T15:00:00+08:00"
    frame["source"] = "AmazingData"
    frame["schema_version"] = SCHEMA_VERSION
    frame["snapshot_id"] = None
    frame["retrieved_at"] = retrieved_at
    frame["quality_status"] = "PASS" if units_verified else "UNVERIFIED_UNITS"
    frame["volume_unit"] = "share"
    frame["amount_unit"] = "CNY"
    return frame[BAR_COLUMNS]


def validate_legacy_bars(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])
    clean = canonical_legacy_bars(raw, symbol, retrieved_at=datetime.now(timezone.utc).isoformat(), units_verified=False)
    dates = pd.to_datetime(clean.trade_date, errors="raise").dt.date
    clean = validate_bars(clean, DataRequest((symbol,), min(dates), max(dates)))
    if not clean.trading_status.isin(["TRADING", "SUSPENDED"]).all():
        raise DataContractError("UNKNOWN_TRADING_STATUS", "Legacy bars cannot represent unresolved trading status")
    legacy = clean.rename(columns={"trade_date": "date", **{f"raw_{p}": p for p in ("open", "high", "low", "close")}})
    return legacy[["date", "open", "high", "low", "close", "volume", "amount", "suspended"]]


class AmazingDataBridge(ResearchDataProvider):
    """Serial capture using an existing RSS SDK session and a separate snapshot store.

    Units/factor schema require explicit evidence from the operator; no online claims
    about historical security status or factor revisions are made by this bridge.
    """
    def __init__(self, adapter, store: SnapshotStore, *, sdk_version="unverified",
                 factor_schema="unknown", units_verified=False):
        self.adapter = adapter
        self.store = store
        self.sdk_version = sdk_version
        self.factor_schema = factor_schema
        self.units_verified = units_verified

    def capabilities(self):
        caps = super().capabilities()
        caps.update(trading_calendar=True, daily_bars=True,
                    adjustment_factors=self.factor_schema in {"daily", "effective_events"})
        return caps

    def trading_calendar(self, start, end):
        return sorted({pd.Timestamp(str(v)).date() for v in self.adapter.get_trade_calendar()
                       if start <= pd.Timestamp(str(v)).date() <= end})

    def daily_bars(self, request):
        return self.capture(request).bars()

    def adjustment_factors(self, request):
        if not self.capabilities()["adjustment_factors"]:
            return self._unsupported("adjustment_factors")
        return pd.DataFrame(self.capture(request, include_factors=True).payload["factors"])

    def capture(self, request: DataRequest, *, refresh=False, include_factors=False) -> Snapshot:
        if not self.units_verified:
            raise DataContractError("UNSUPPORTED_CAPABILITY", "Verify SDK volume/amount units before research capture")
        key = digest({"provider": "amazingdata-bridge/1", "sdk": self.sdk_version,
                      "codes": sorted(request.codes), "start": request.start, "end": request.end,
                      "factor_schema": self.factor_schema, "include_factors": include_factors})
        cached = None if refresh else self.store.lookup(key)
        if cached is not None:
            return cached
        if include_factors and not self.capabilities()["adjustment_factors"]:
            raise DataContractError("UNSUPPORTED_CAPABILITY", "Factor density/effective-date semantics unverified")
        retrieved = datetime.now(timezone.utc).isoformat()
        parts, factor_parts = [], []
        for code in request.codes:
            raw = self.adapter._call_with_retry(lambda provider: checked_sdk_bars(provider, code, request.start, request.end))
            part = canonical_legacy_bars(raw, code, retrieved_at=retrieved, units_verified=True)
            parts.append(part)
            if include_factors:
                factor = self.adapter.get_backward_factor(code, force_refresh=refresh)
                if code not in factor.columns:
                    raise DataContractError("SYMBOL_MISMATCH", "Factor response lacks requested security")
                canonical_factor = pd.DataFrame({"ts_code": code,
                    "trade_date": pd.to_datetime(factor.index).strftime("%Y-%m-%d"),
                    "factor": factor[code].to_numpy(),
                    "available_at": pd.to_datetime(factor.index).strftime("%Y-%m-%dT15:00:00+08:00")})
                # SDK factor API has no date parameter; capture only this as-of prefix.
                factor_parts.append(canonical_factor.loc[canonical_factor.trade_date.le(request.end.isoformat())])
        snapshot = Snapshot.create(request=request, bars=pd.concat(parts, ignore_index=True),
            calendar=self.trading_calendar(request.start, request.end),
            factors=pd.concat(factor_parts, ignore_index=True) if factor_parts else pd.DataFrame(),
            metadata={"provider_version": "amazingdata-bridge/1", "sdk_version": self.sdk_version,
                      "factor_schema": self.factor_schema if include_factors else "none",
                      "calendar_verified": True, "availability_verified": False,
                      "source": "AmazingData", "retrieved_at": retrieved})
        # Validate factors before persistence; raw-only capture remains supported.
        if include_factors:
            from .provider import SnapshotProvider
            SnapshotProvider(snapshot).daily_bars(request.codes, request.start, request.end, price_adjustment="qfq")
        self.store.save(snapshot)
        if snapshot.coverage_complete:
            self.store.commit_coverage(key, snapshot.snapshot_id)
        return snapshot
