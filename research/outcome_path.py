"""Outcome-only price-scale adapter; signal modules must not import this file."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Sequence

from .event_study import PriceBar, PricePath, SHANGHAI
from .factor_pit import CorporateAction, implemented_events


def comparable_forward_path(*, security_id: str, snapshot_id: str, entry_date: date,
                            bars: Sequence[PriceBar], actions: Sequence[CorporateAction],
                            action_source_hash: str, action_chronology_complete: bool) -> PricePath:
    """Map realized post-entry raw prices to entry-date scale, not total return."""
    if (not action_chronology_complete or not bars or
            len(action_source_hash) != 64 or
            any(char not in "0123456789abcdef" for char in action_source_hash)):
        raise ValueError("Incomplete frozen outcome-action source")
    last_day = bars[-1].trade_date
    if entry_date != bars[0].trade_date or tuple(bar.trade_date for bar in bars) != tuple(sorted(set(
            bar.trade_date for bar in bars))):
        raise ValueError("Invalid forward path dates")
    events = tuple(action for action in implemented_events(actions, last_day)
                   if entry_date < action.effective_date <= last_day)
    if not events:
        return PricePath(security_id, snapshot_id, tuple(bars))
    adjusted = []
    for bar in bars:
        scale = Decimal(1)
        for event in events:
            if event.effective_date <= bar.trade_date:
                scale *= event.single_factor
        adjusted.append(PriceBar(
            bar.trade_date, bar.high, bar.low, bar.close, bar.status, scale,
            datetime.combine(bar.trade_date, time(15, 30), SHANGHAI)))
    return PricePath(security_id, snapshot_id, tuple(adjusted),
                     tuple(event.effective_date for event in events), (), True,
                     action_source_hash, Decimal(1),
                     datetime.combine(entry_date, time(15, 30), SHANGHAI))
