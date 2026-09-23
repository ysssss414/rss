"""Causal Stage 1 entry signals; an intent is never an execution fill."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from .foundation import UniverseRecord
from .indicator_prices import PRICE_BASIS
from .primitives import PrimitiveValue


SHANGHAI = timezone(timedelta(hours=8))
ENTRY_A = "ENTRY_A_PULLBACK_TO_MA5_V1"
ENTRY_B = "ENTRY_B_RSI_RECROSS_70_V1"


@dataclass(frozen=True)
class EntryPriceEvidence:
    security_id: str
    trade_date: date
    source_snapshot_id: str
    raw_low: Decimal
    raw_close: Decimal
    indicator_close: Decimal
    factor_t: Decimal
    anchor_factor_t: Decimal
    raw_available_at: datetime
    indicator_available_at: datetime
    factor_available_at: datetime
    factor_source_hash: str
    pit_verified: bool
    price_basis: str = PRICE_BASIS

    def raw_equivalent(self, adjusted_price: Decimal, *, decision_at: datetime) -> Decimal:
        """Convert T-anchored adjusted MA5 to T raw scale using only PIT T factors."""
        timestamps = (self.raw_available_at, self.indicator_available_at,
                      self.factor_available_at, decision_at)
        if (not self.security_id or not self.source_snapshot_id
                or any(item.utcoffset() is None for item in timestamps)
                or decision_at.astimezone(SHANGHAI).date() != self.trade_date
                or decision_at.astimezone(SHANGHAI).time() < time(15)
                or self.raw_available_at.astimezone(SHANGHAI).date() != self.trade_date
                or self.raw_available_at.astimezone(SHANGHAI).time() < time(15)
                or self.indicator_available_at.astimezone(SHANGHAI).date() != self.trade_date
                or self.indicator_available_at.astimezone(SHANGHAI).time() < time(15)
                or any(item > decision_at for item in timestamps[:-1])
                or self.price_basis != PRICE_BASIS or self.pit_verified is not True
                or len(self.factor_source_hash) != 64
                or any(char not in "0123456789abcdef" for char in self.factor_source_hash)
                or any(not value.is_finite() or value <= 0 for value in
                       (self.raw_low, self.raw_close, self.indicator_close,
                        self.factor_t, self.anchor_factor_t, adjusted_price))
                or self.raw_low > self.raw_close):
            raise ValueError("Invalid or unavailable T price/factor evidence")
        ratio = self.factor_t / self.anchor_factor_t
        # PIT_ADJUSTED_CLOSE_V1 always anchors on T; a different anchor is another contract.
        if ratio != 1 or self.indicator_close != self.raw_close * ratio:
            raise ValueError("T price scale disagrees with PIT_ADJUSTED_CLOSE_V1")
        return adjusted_price / ratio


@dataclass(frozen=True)
class EntrySignal:
    observation_instance_id: str
    observation_contract_version: str
    entry_contract_versions: tuple[str, ...]
    primitive_versions: tuple[str, ...]
    data_snapshot_id: str
    signal_date: date
    signal_at: datetime
    signal_reasons: tuple[str, ...]
    execution_intent_type: str
    factor_source_hash: str
    execution_policy_id: str = "NEXT_SESSION_V1"
    execution_fidelity_gap: bool = False


@dataclass(frozen=True)
class EntryEvaluation:
    status: str
    signal: EntrySignal | None
    reason: str | None = None


def evaluate_entries(*, pool: object, universe: UniverseRecord, rsi: PrimitiveValue,
                     ma: PrimitiveValue | None, close_limit: PrimitiveValue,
                     prices: EntryPriceEvidence, decision_at: datetime) -> EntryEvaluation:
    """Evaluate A and B independently; retain all signal reasons and no fill claim."""
    if not universe.eligible:
        return EntryEvaluation("NOT_READY", None, "UNIVERSE_INELIGIBLE")
    if (pool.status != "ACTIVE" or pool.security_id != universe.security_id
            or prices.security_id != universe.security_id or prices.trade_date != universe.trade_date
            or prices.source_snapshot_id != universe.data_snapshot_id):
        return EntryEvaluation("INVALID", None, "CONFLICTING_SIGNAL_CONTEXT")
    for value, expected in ((rsi, "RSI14_PROJECT_V1"),
                            (close_limit, "IS_CLOSE_LIMIT_UP_V1")):
        if (value.primitive_id != expected or value.as_of_date != universe.trade_date
                or value.source_snapshot_id != universe.data_snapshot_id):
            return EntryEvaluation("INVALID", None, "CONFLICTING_PRIMITIVE")
        if value.status != "READY":
            return EntryEvaluation("NOT_READY" if value.status == "NOT_ENOUGH_HISTORY" else "INVALID",
                                   None, value.reason or value.status)
        if (value.available_at is None or value.available_at.utcoffset() is None
                or value.available_at > decision_at
                or value.available_at.astimezone(SHANGHAI).date() != universe.trade_date
                or value.available_at.astimezone(SHANGHAI).time() < time(15)):
            return EntryEvaluation("INVALID", None, "LATE_PRIMITIVE")
    try:
        prices.raw_equivalent(prices.indicator_close, decision_at=decision_at)
    except (ValueError, AttributeError):
        return EntryEvaluation("INVALID", None, "INVALID_PRICE_LINEAGE")
    if (not isinstance(rsi.value, Decimal) or not rsi.value.is_finite()
            or not Decimal(0) <= rsi.value <= Decimal(100)
            or not isinstance(close_limit.value, bool)):
        return EntryEvaluation("INVALID", None, "INVALID_PRIMITIVE_VALUE")
    reasons = []
    if rsi.value > 70 and not pool.has_seen_rsi_below_70:
        if ma is None:
            return EntryEvaluation("NOT_READY", None, "MA5_UNAVAILABLE")
        if (ma.primitive_id != "MA5_SMA_V1" or ma.as_of_date != universe.trade_date
                or ma.source_snapshot_id != universe.data_snapshot_id):
            return EntryEvaluation("INVALID", None, "CONFLICTING_MA5")
        if ma.ready:
            if (not isinstance(ma.value, Decimal) or not ma.value.is_finite()
                    or ma.available_at is None or ma.available_at.utcoffset() is None
                    or ma.available_at > decision_at
                    or ma.available_at.astimezone(SHANGHAI).date() != universe.trade_date
                    or ma.available_at.astimezone(SHANGHAI).time() < time(15)):
                return EntryEvaluation("INVALID", None, "INVALID_MA5")
            try:
                ma_raw = prices.raw_equivalent(ma.value, decision_at=decision_at)
            except ValueError:
                return EntryEvaluation("INVALID", None, "INVALID_MA5_SCALE")
            if prices.raw_low <= ma_raw <= prices.raw_close:
                reasons.append(ENTRY_A)
        elif ma.status == "NOT_ENOUGH_HISTORY":
            return EntryEvaluation("NOT_READY", None, "MA5_NOT_READY")
        else:
            return EntryEvaluation("INVALID", None, "INVALID_MA5")
    if (pool.has_seen_rsi_below_70 and pool.previous_valid_rsi is not None
            and pool.previous_valid_rsi < 70 and rsi.value > 70
            and pool.previous_valid_indicator_close is not None
            and prices.indicator_close > pool.previous_valid_indicator_close):
        reasons.append(ENTRY_B)
    if not reasons:
        return EntryEvaluation("READY", None)
    board = close_limit.value is True
    versions = ("RSI14_PROJECT_V1", "IS_CLOSE_LIMIT_UP_V1",
                "LIMIT_UP_COUNT_5_V1", PRICE_BASIS)
    if ENTRY_A in reasons:
        versions += ("MA5_SMA_V1",)
    signal = EntrySignal(
        pool.observation_instance_id, "OBSERVATION_POOL_V1", tuple(reasons),
        versions,
        universe.data_snapshot_id, universe.trade_date, decision_at, tuple(reasons),
        "LIMIT_UP_CLOSE_BOARD_INTENT" if board else "NORMAL_CLOSE_INTENT",
        prices.factor_source_hash,
        execution_fidelity_gap=board)
    return EntryEvaluation("READY", signal)
