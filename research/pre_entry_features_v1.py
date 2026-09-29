"""T-close-and-earlier features for frozen Entry events; no outcome imports."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import math
import statistics
from typing import Mapping, Sequence


FEATURES = (
    "entry_close_vs_ma5", "entry_low_vs_ma5", "intraday_recovery",
    "prior5_raw_return", "trigger_to_entry_raw_return", "entry_rsi14",
    "rsi_change", "amount_ratio_prev5", "prev5_avg_range",
)


class _AsOfBars:
    """Make an accidental H1+ lookup fail even if the caller holds later bars."""

    def __init__(self, bars: Mapping[tuple[str, str], Mapping], day: str):
        self.bars, self.day = bars, day

    def get(self, key: tuple[str, str]) -> Mapping | None:
        if key[1] > self.day:
            raise ValueError("BLOCKED_EDGE_FEATURE_LOOKAHEAD: future bar lookup")
        return self.bars.get(key)


def _positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _bar(bars: Mapping[tuple[str, str], Mapping], code: str, day: str) -> Mapping | None:
    item = bars.get((code, day))
    if item is None or any(_positive(item.get(field)) is None for field in ("open", "high", "low", "close")):
        return None
    if not item["low"] <= item["open"] <= item["high"] or not item["low"] <= item["close"] <= item["high"]:
        return None
    return item


def build_features(
    entries: Sequence[Mapping[str, str]],
    observations: Mapping[tuple[str, str], Mapping[str, str]],
    bars: Mapping[tuple[str, str], Mapping],
    calendar: Sequence[str],
) -> list[dict]:
    """Use only named <=T bar lookups; no outcome, D8, or future-date inputs."""
    if list(calendar) != sorted(set(calendar)):
        raise ValueError("Calendar must be unique and ordered")
    positions = {day: i for i, day in enumerate(calendar)}
    counts = Counter(x["security_id"] for x in entries)
    ordinals: dict[str, int] = defaultdict(int)
    rows = []
    for entry in sorted(entries, key=lambda x: (x["security_id"], x["entry_date"], x["observation_instance_id"])):
        code, day = entry["security_id"], entry["entry_date"]
        source = observations[(code, entry["observation_instance_id"])]
        trigger = source["trigger_date"]
        if day not in positions or trigger not in positions or trigger > day:
            raise ValueError("BLOCKED_EDGE_FEATURE_LOOKAHEAD: invalid source date")
        asof = _AsOfBars(bars, day)
        current = _bar(asof, code, day)
        if (current is None or str(current["close"]) != entry["raw_close"]
                or str(current["low"]) != entry["raw_low"]):
            raise ValueError("BLOCKED_EDGE_POPULATION_DRIFT: frozen Entry versus D3")
        trigger_bar = _bar(asof, code, trigger)
        previous = calendar[max(0, positions[day] - 5):positions[day]]
        prior_bars = [_bar(asof, code, d) for d in previous]
        complete_prior = len(prior_bars) == 5 and all(x is not None for x in prior_bars)
        close, low, ma5 = float(entry["raw_close"]), float(entry["raw_low"]), float(entry["ma5_raw"])
        rsi, trigger_rsi = float(entry["rsi14"]), float(source["rsi14"])
        amount = _positive(current.get("amount"))
        prior_amounts = [_positive(x.get("amount")) for x in prior_bars] if complete_prior else []
        avg_amount = statistics.fmean(prior_amounts) if len(prior_amounts) == 5 and all(
            x is not None for x in prior_amounts) else None
        ordinals[code] += 1
        values = {
            "entry_close_vs_ma5": close / ma5 - 1 if ma5 > 0 else None,
            "entry_low_vs_ma5": low / ma5 - 1 if ma5 > 0 else None,
            "intraday_recovery": close / low - 1,
            "prior5_raw_return": close / prior_bars[0]["close"] - 1 if complete_prior else None,
            "trigger_to_entry_raw_return": close / trigger_bar["close"] - 1 if trigger_bar else None,
            "entry_rsi14": rsi,
            "rsi_change": rsi - trigger_rsi,
            "amount_ratio_prev5": amount / avg_amount if amount is not None and avg_amount else None,
            "prev5_avg_range": statistics.fmean((x["high"] - x["low"]) / x["close"]
                                                  for x in prior_bars) if complete_prior else None,
        }
        if any(v is not None and not math.isfinite(v) for v in values.values()):
            raise ValueError("Nonfinite pre-entry feature")
        rows.append({
            "entry_event_id": sha256(f'{code}|{day}|{entry["observation_instance_id"]}'.encode()).hexdigest(),
            "security_id": code, "entry_date": day,
            "observation_instance_id": entry["observation_instance_id"],
            "entry_type": entry["entry_reasons"], "execution_intent": entry["execution_intent"],
            "observation_strength": f'{source["v3_lookback_hits"]}/5',
            "trigger_provenance": source["trigger_regime_qualification_method"],
            "rsi_provenance": source["rsi_replay_mode"],
            "trigger_date": trigger, "feature_source_max_date": day,
            "first_prior5_date": previous[0] if len(previous) == 5 else None,
            "entry_ordinal_for_security": ordinals[code],
            "security_entry_count": counts[code],
            **values,
        })
    if len(rows) != len(entries) or len({x["entry_event_id"] for x in rows}) != len(rows):
        raise ValueError("BLOCKED_EDGE_POPULATION_DRIFT: feature grain")
    return rows
