"""As-of-T market/style context primitives; deliberately outcome-free."""

from __future__ import annotations

import math
import statistics


PRIMARY_FEATURES = (
    "turnover_20d_percentile", "market_breadth_score",
    "prior_limit_up_next_day_median_return", "high_position_negative_feedback_rate",
    "high_board_advancement_rate", "shortline_style_score", "trend_style_score",
    "style_differential",
)


def trailing_percentile(history: list[float | None], *, width: int, minimum: int) -> float | None:
    """Empirical <= percentile, including T; no future observations."""
    values = [x for x in history[-width:] if x is not None and math.isfinite(x)]
    if len(values) < minimum or history[-1] is None:
        return None
    return sum(x <= history[-1] for x in values) / len(values)


def ratio_slope(history: list[float], lag: int) -> float | None:
    return history[-1] / history[-lag - 1] - 1 if len(history) > lag and history[-lag - 1] > 0 else None


def score(components: list[float | None], minimum: int) -> tuple[float | None, int]:
    present = [x for x in components if x is not None]
    return (statistics.fmean(present) if len(present) >= minimum else None, len(present))


def market_regime(turnover: float | None, breadth: float | None,
                  prior_median: float | None, negative_feedback: float | None) -> tuple[str | None, int]:
    votes = []
    if turnover is not None:
        votes.append(1 if turnover >= 2 / 3 else -1 if turnover <= 1 / 3 else 0)
    if breadth is not None:
        votes.append(1 if breadth > 0 else -1 if breadth < 0 else 0)
    if prior_median is not None:
        votes.append(1 if prior_median > 0 else -1 if prior_median < 0 else 0)
    if negative_feedback is not None:
        votes.append(1 if negative_feedback <= .10 else -1 if negative_feedback >= .30 else 0)
    if len(votes) < 3:
        return None, len(votes)
    total = sum(votes)
    return ("RISK_ON" if total >= 2 else "RISK_OFF" if total <= -2 else "NEUTRAL"), len(votes)


def style_regime(differential: float | None) -> str | None:
    if differential is None:
        return None
    return ("SHORTLINE_DOMINANT" if differential > .10 else
            "TREND_DOMINANT" if differential < -.10 else "MIXED")


def tercile_boundaries(values: list[float]) -> list[float]:
    ordered = sorted(values)
    if len(ordered) < 3:
        raise ValueError("Not enough development observations for context terciles")
    def at(p: float) -> float:
        offset = (len(ordered) - 1) * p
        lo = int(offset)
        return ordered[lo] + (ordered[min(lo + 1, len(ordered) - 1)] - ordered[lo]) * (offset - lo)
    return [at(1 / 3), at(2 / 3)]


class AsOfDaily:
    """Protect Entry assignment against accidental T+1 context lookup."""

    def __init__(self, daily: dict[str, dict], entry_day: str):
        self.daily, self.entry_day = daily, entry_day

    def get(self, day: str) -> dict | None:
        if day > self.entry_day:
            raise ValueError("BLOCKED_CONTEXT_FEATURE_LEAKAGE: future market date")
        return self.daily.get(day)
