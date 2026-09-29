"""Descriptive diagnostics for frozen Entry/Exit outcomes; no rule search."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from hashlib import sha256
import json
import math
import random
import statistics
from typing import Mapping, Sequence

from research.exit_pnl_v1 import upper_tick


EXIT_IDS = ("TIME_H1", "TIME_H3", "TIME_H5", "TP5_H5", "TP10_H5",
            "TP5_SL5_H5", "TP10_SL10_H5", "TP10_SL5_H5")
LAYERS = ("EXECUTABLE_PNL_BASELINE_RESEARCH_ASSUMPTION", "CONDITIONAL_ON_FILL_ONLY")
BOOTSTRAP_SEED = 20260929
BOOTSTRAP_DRAWS = 2000


def finite(value: object) -> float | None:
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def quantile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    at = (len(ordered) - 1) * p
    lo = int(at)
    return ordered[lo] + (ordered[min(lo + 1, len(ordered) - 1)] - ordered[lo]) * (at - lo)


def distribution(values: Sequence[float]) -> dict:
    data = [float(x) for x in values if finite(x) is not None]
    result = {"n": len(data), "mean": statistics.fmean(data) if data else None,
              "median": statistics.median(data) if data else None}
    result.update({f"p{int(p * 100)}": quantile(data, p) for p in (.1, .25, .5, .75, .9)})
    if len(data) >= 3:
        mean = result["mean"]
        variance = statistics.fmean((x - mean) ** 2 for x in data)
        result["population_skewness"] = (statistics.fmean((x - mean) ** 3 for x in data)
                                         / variance ** 1.5 if variance > 0 else None)
    else:
        result["population_skewness"] = None
    return result


def trade_summary(rows: Sequence[Mapping]) -> dict:
    closed = [r for r in rows if r["outcome_status"] == "CLOSED"]
    gross = [float(r["gross_price_return"]) for r in closed]
    net = [float(r["net_price_return"]) for r in closed]
    result = {"n_total": len(rows), "n_closed": len(closed),
              "n_unresolved_or_censored": len(rows) - len(closed),
              **{f"gross_{k}": v for k, v in distribution(gross).items()},
              **{f"net_{k}": v for k, v in distribution(net).items()},
              "net_positive_fraction": sum(x > 0 for x in net) / len(net) if net else None,
              "net_loss_le_5_fraction": sum(x <= -.05 for x in net) / len(net) if net else None,
              "net_loss_le_10_fraction": sum(x <= -.10 for x in net) / len(net) if net else None,
              "net_loss_le_20_fraction": sum(x <= -.20 for x in net) / len(net) if net else None,
              "net_gain_ge_5_fraction": sum(x >= .05 for x in net) / len(net) if net else None,
              "net_gain_ge_10_fraction": sum(x >= .10 for x in net) / len(net) if net else None,
              "holding_mean": statistics.fmean(float(r["holding_sessions"]) for r in closed) if closed else None,
              "exit_reason_counts": json.dumps(dict(sorted(Counter(r["exit_reason"] for r in closed).items())),
                                               sort_keys=True, separators=(",", ":")),
              "small_n_warning": "DESCRIPTIVE_N_LT_20" if len(rows) < 20 else ""}
    return result


def cluster_bootstrap(rows: Sequence[Mapping], *, layer: str, exit_id: str) -> dict:
    closed = [r for r in rows if r["outcome_status"] == "CLOSED"]
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in closed:
        grouped[row["security_id"]].append(float(row["net_price_return"]))
    codes = sorted(grouped)
    seed = BOOTSTRAP_SEED + int(sha256(f"{layer}|{exit_id}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    estimates = []
    for _ in range(BOOTSTRAP_DRAWS):
        sampled = [grouped[codes[rng.randrange(len(codes))]] for _ in codes]
        estimates.append(sum(sum(group) for group in sampled) / sum(len(group) for group in sampled))
    return {"population_layer": layer, "exit_contract_id": exit_id,
            "n_closed": len(closed), "security_clusters": len(codes), "draws": BOOTSTRAP_DRAWS,
            "seed": seed, "observed_net_mean": statistics.fmean(float(r["net_price_return"]) for r in closed),
            "bootstrap_median_estimate": statistics.median(estimates),
            "ci90_low": quantile(estimates, .05), "ci90_high": quantile(estimates, .95),
            "ci95_low": quantile(estimates, .025), "ci95_high": quantile(estimates, .975),
            "interpretation": "within-sample security-cluster resampling; not out-of-sample or causal evidence"}


def target_first_net(row: Mapping, *, tp: Decimal, costs: Mapping) -> float:
    """Optimistic same-bar upper bound; primary trade row remains STOP_FIRST."""
    if not row["same_bar_ambiguity"] or row["exit_reason"] != "SAME_BAR_AMBIGUOUS_STOP_FIRST":
        return float(row["net_price_return"])
    scale = Decimal(str(row["exit_price_comparable"])) / Decimal(str(row["exit_price_raw"]))
    fill = Decimal(str(row["fill_price"]))
    target_raw = upper_tick(fill * (1 + tp) / scale)
    gross = target_raw * scale / fill - 1
    buy = Decimal(str(costs["buy_commission_bps"])) + Decimal(str(costs["buy_transfer_bps"]))
    sell = (Decimal(str(costs["sell_commission_bps"])) + Decimal(str(costs["sell_transfer_bps"]))
            + Decimal(str(costs["sell_stamp_duty_bps"])))
    return float(gross - buy / 10000 - (1 + gross) * sell / 10000)


def bin_index(value: object, boundaries: Sequence[float]) -> int | None:
    number = finite(value)
    if number is None:
        return None
    return sum(number >= bound for bound in boundaries)


def outcome_labels(event: Mapping, tp5: Mapping) -> dict:
    h5 = finite(event.get("h5_signal_forward_close_return")) if event.get("h5_outcome_status") == "AVAILABLE" else None
    h5_mfe = finite(event.get("h5_signal_forward_mfe")) if h5 is not None else None
    closed = tp5["outcome_status"] == "CLOSED"
    return {
        "LEFT_TAIL_H5_10": h5 <= -.10 if h5 is not None else None,
        "LEFT_TAIL_H5_20": h5 <= -.20 if h5 is not None else None,
        "FAILED_REBOUND_TP5": not str(tp5["exit_reason"]).startswith("TP_") if closed else None,
        "TP5_THEN_DEEP_REVERSAL": h5_mfe >= .05 and h5 <= -.05 if h5_mfe is not None else None,
    }


def diagnostic_summary(rows: Sequence[Mapping]) -> dict:
    def rate(name: str) -> tuple[int, float | None]:
        values = [r[name] for r in rows if r[name] is not None]
        return len(values), sum(values) / len(values) if values else None

    result = {"n_total": len(rows)}
    for horizon in (1, 3, 5):
        values = [r[f"h{horizon}_return"] for r in rows if r[f"h{horizon}_return"] is not None]
        result[f"h{horizon}_n"] = len(values)
        result[f"h{horizon}_mean"] = statistics.fmean(values) if values else None
        result[f"h{horizon}_median"] = statistics.median(values) if values else None
    values = [r["tp5_net"] for r in rows if r["tp5_net"] is not None]
    result["tp5_closed_n"] = len(values)
    result["tp5_net_mean"] = statistics.fmean(values) if values else None
    for name in ("LEFT_TAIL_H5_10", "LEFT_TAIL_H5_20", "FAILED_REBOUND_TP5", "TP5_THEN_DEEP_REVERSAL"):
        n, fraction = rate(name)
        result[name.lower() + "_n"] = n
        result[name.lower() + "_fraction"] = fraction
    result["small_n_warning"] = "DESCRIPTIVE_N_LT_20" if len(rows) < 20 else ""
    return result
