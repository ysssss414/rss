"""Count IPO exchange sessions using a minimal pre-window calendar prefix."""

from __future__ import annotations

from datetime import date


def ipo_session_number(listing_date: date, trade_date: date,
                       calendar: tuple[date, ...]) -> int:
    if tuple(sorted(set(calendar))) != calendar:
        raise ValueError("Exchange calendar must be strictly ordered and unique")
    if listing_date not in calendar or trade_date not in calendar or trade_date < listing_date:
        raise ValueError("Listing or trade date absent from qualified exchange calendar")
    return sum(listing_date <= session <= trade_date for session in calendar)
