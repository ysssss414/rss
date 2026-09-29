"""Descriptive, post-outcome diagnostics for the frozen context contract."""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import random
import statistics


BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 20260929


def bucket(value: float | None, boundaries: list[float]) -> str:
    if value is None:
        return "MISSING"
    return "LOW" if value <= boundaries[0] else "MID" if value <= boundaries[1] else "HIGH"


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def fraction(values: list[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def summary(rows: list[dict]) -> dict:
    tp = [r["tp5_net"] for r in rows if r["tp5_net"] is not None]
    h5 = [r["h5_signal"] for r in rows if r["h5_signal"] is not None]
    tails10 = [r["tail10"] for r in rows if r["tail10"] is not None]
    tails20 = [r["tail20"] for r in rows if r["tail20"] is not None]
    successes = [r["tp5_success"] for r in rows if r["tp5_success"] is not None]
    result = {"n_events": len(rows), "n_securities": len({r["security_id"] for r in rows}),
              "tp5_closed_n": len(tp), "tp5_net_mean": mean(tp),
              "tp5_net_median": statistics.median(tp) if tp else None,
              "tp5_net_positive_fraction": fraction([x > 0 for x in tp]),
              "h5_signal_n": len(h5), "h5_signal_mean": mean(h5),
              "left_tail_h5_10_n": len(tails10), "left_tail_h5_10_fraction": fraction(tails10),
              "left_tail_h5_20_n": len(tails20), "left_tail_h5_20_fraction": fraction(tails20),
              "tp5_success_n": len(successes), "tp5_success_fraction": fraction(successes),
              "failed_rebound_tp5_fraction": fraction([not x for x in successes]),
              "sample_guardrail": "N_LT_10_NO_SHORTLIST" if len(rows) < 10 else
                                  "SMALL_SAMPLE_DESCRIPTIVE_ONLY" if len(rows) < 20 else ""}
    for horizon in (1, 3, 5):
        values = [r[f"time_h{horizon}_net"] for r in rows if r[f"time_h{horizon}_net"] is not None]
        result[f"time_h{horizon}_closed_n"] = len(values)
        result[f"time_h{horizon}_net_mean"] = mean(values)
    return result


def cluster_contrast(rows: list[dict], side: str, favorable: str, adverse: str,
                     contrast_id: str) -> dict:
    relevant = [r for r in rows if r[side] in (favorable, adverse)]
    clusters: dict[str, list[dict]] = defaultdict(list)
    for row in relevant:
        clusters[row["security_id"]].append(row)
    codes = sorted(clusters)
    seed = BOOTSTRAP_SEED + int(sha256(contrast_id.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)

    def difference(sample: list[dict], field: str) -> float | None:
        good = [r[field] for r in sample if r[side] == favorable and r[field] is not None]
        bad = [r[field] for r in sample if r[side] == adverse and r[field] is not None]
        return mean(good) - mean(bad) if good and bad else None

    observed_net = difference(relevant, "tp5_net")
    observed_tail = difference(relevant, "tail10")
    draws_net, draws_tail = [], []
    for _ in range(BOOTSTRAP_DRAWS):
        sampled = [row for _code in codes for row in clusters[codes[rng.randrange(len(codes))]]]
        net, tail = difference(sampled, "tp5_net"), difference(sampled, "tail10")
        if net is not None:
            draws_net.append(net)
        if tail is not None:
            draws_tail.append(tail)

    def ci(values: list[float]) -> list[float] | None:
        if len(values) < BOOTSTRAP_DRAWS * .95:
            return None
        values.sort()
        def q(p: float) -> float:
            at = (len(values) - 1) * p
            lo = int(at)
            return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (at - lo)
        return [q(.025), q(.975)]

    return {"contrast_id": contrast_id, "favorable": favorable, "adverse": adverse,
            "n_favorable": sum(r[side] == favorable for r in relevant),
            "n_adverse": sum(r[side] == adverse for r in relevant),
            "security_clusters": len(codes), "draws": BOOTSTRAP_DRAWS, "seed": seed,
            "tp5_net_mean_difference": observed_net, "tp5_net_difference_ci95": ci(draws_net),
            "left_tail_h5_10_difference": observed_tail, "left_tail_difference_ci95": ci(draws_tail),
            "meaning": "Within-sample security-cluster percentile interval; post-outcome exploratory, not independent validation"}
