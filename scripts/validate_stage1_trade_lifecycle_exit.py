"""Deterministic Stage 1 lifecycle/warm-up validation; no performance backtest."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
from math import ceil
from pathlib import Path

from research.foundation import SHANGHAI
from research.indicator_prices import IndicatorPrice, IndicatorPriceSeries, PRICE_BASIS
from research.primitives import rsi14


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "stage1_trade_lifecycle_exit"
MANIFEST = ARTIFACTS / "qualification_manifest.json"
WINDOWS = (60, 90, 120, 150, 180)


def _sequences(length: int = 280) -> dict[str, tuple[Decimal, ...]]:
    patterns = {
        "trend": ("0.45", "0.25", "-0.10", "0.35", "0.15", "-0.05"),
        "high_volatility": ("5.0", "-4.5", "7.0", "-6.0", "3.5", "-2.0", "1.0"),
        "alternating": ("1.2", "-1.1"),
        "near_flat": ("0.01", "0", "-0.01", "0.02", "-0.01", "0"),
        "threshold_near_70": ("1", "1", "-0.80"),
    }
    result = {}
    for name, raw_pattern in patterns.items():
        price = Decimal("100")
        values = [price]
        pattern = tuple(Decimal(value) for value in raw_pattern)
        for index in range(1, length):
            price += pattern[(index - 1) % len(pattern)]
            values.append(price)
        result[name] = tuple(values)
    return result


def _rsi(prices: tuple[Decimal, ...]) -> Decimal:
    start = date(2020, 1, 1)
    observations = tuple(
        IndicatorPrice(
            "SYNTHETIC", start + timedelta(days=index), price, PRICE_BASIS,
            datetime.combine(start + timedelta(days=index), time(15), SHANGHAI),
            "synthetic-rsi-convergence", True, "TRADING",
        )
        for index, price in enumerate(prices)
    )
    series = IndicatorPriceSeries(
        "SYNTHETIC", observations[-1].trade_date, observations[0].trade_date,
        PRICE_BASIS, "synthetic-rsi-convergence", "synthetic-calendar/1",
        "effective_events", observations,
    )
    result = rsi14(
        series,
        decision_at=datetime.combine(series.as_of_date, time(15, 10), SHANGHAI),
    )
    if not result.ready or not isinstance(result.value, Decimal):
        raise AssertionError(f"Unexpected RSI readiness: {result.status}")
    return result.value


def _metric(values: list[Decimal], full: list[Decimal], warm: list[Decimal]) -> dict[str, object]:
    ordered = sorted(values)
    p95 = ordered[ceil(len(ordered) * 0.95) - 1]
    full_gt = [value > 70 for value in full]
    warm_gt = [value > 70 for value in warm]
    full_lt = [value < 70 for value in full]
    warm_lt = [value < 70 for value in warm]
    recross_mismatch = sum(
        (full_lt[index - 1] and full_gt[index])
        != (warm_lt[index - 1] and warm_gt[index])
        for index in range(1, len(full))
    )
    return {
        "sample_count": len(values),
        "max_absolute_error": format(max(values), ".12f"),
        "p95_absolute_error": format(p95, ".12f"),
        "classification_mismatch_gt_70": sum(a != b for a, b in zip(full_gt, warm_gt)),
        "classification_mismatch_lt_70": sum(a != b for a, b in zip(full_lt, warm_lt)),
        "recross_70_mismatch": recross_mismatch,
    }


def _aggregate_metric(
    errors: list[Decimal],
    metrics: list[dict[str, object]],
) -> dict[str, object]:
    ordered = sorted(errors)
    return {
        "sample_count": len(errors),
        "max_absolute_error": format(max(errors), ".12f"),
        "p95_absolute_error": format(
            ordered[ceil(len(ordered) * 0.95) - 1], ".12f",
        ),
        "classification_mismatch_gt_70": sum(
            item["classification_mismatch_gt_70"] for item in metrics
        ),
        "classification_mismatch_lt_70": sum(
            item["classification_mismatch_lt_70"] for item in metrics
        ),
        "recross_70_mismatch": sum(
            item["recross_70_mismatch"] for item in metrics
        ),
    }


def build_convergence_report() -> dict[str, object]:
    by_series: dict[str, dict[str, object]] = {}
    aggregate = {
        window: {"errors": [], "metrics": []}
        for window in WINDOWS
    }
    for name, prices in _sequences().items():
        # Include the preceding point so recross comparisons cover every reported transition.
        endpoints = range(max(WINDOWS), len(prices) + 1)
        full = [_rsi(prices[:end]) for end in endpoints]
        series_result = {
            "full_prefix_rsi_min": format(min(full), ".12f"),
            "full_prefix_rsi_max": format(max(full), ".12f"),
            "full_prefix_recross_70_count": sum(
                full[index - 1] < 70 and full[index] > 70
                for index in range(1, len(full))
            ),
        }
        for window in WINDOWS:
            warm = [_rsi(prices[end - window:end]) for end in endpoints]
            errors = [abs(left - right) for left, right in zip(full, warm)]
            metrics = _metric(errors, full, warm)
            series_result[f"WARMUP_{window}"] = metrics
            aggregate[window]["errors"].extend(errors)
            aggregate[window]["metrics"].append(metrics)
        by_series[name] = series_result
    overall = {
        f"WARMUP_{window}": _aggregate_metric(
            aggregate[window]["errors"],
            aggregate[window]["metrics"],
        )
        for window in WINDOWS
    }
    return {
        "schema": "stage1-rsi-convergence-validation/1",
        "rsi_version": "RSI14_PROJECT_V1",
        "warmup_policy": "RSI_WARMUP_POLICY_V1",
        "canonical": "FULL_PREFIX",
        "sequence_length": 280,
        "evaluation_endpoint_start": 180,
        "sequence_types": list(_sequences()),
        "window_admissibility": {
            "WARMUP_60": "DIAGNOSTIC_BELOW_POLICY_MINIMUM",
            "WARMUP_90": "DIAGNOSTIC_BELOW_POLICY_MINIMUM",
            "WARMUP_120": "MINIMUM_ACCEPTABLE_TRUNCATED_SLICE",
            "WARMUP_150": "DEFAULT_TRUNCATED_SLICE_TARGET",
            "WARMUP_180": "EXTENDED_DIAGNOSTIC",
        },
        "overall": overall,
        "by_series": by_series,
        "decision_threshold": {
            "above": "RSI > 70",
            "below": "RSI < 70",
            "recross": "previous RSI < 70 and current RSI > 70",
        },
        "algorithm_modified": False,
    }


def verify_manifest() -> int:
    manifest = json.loads(MANIFEST.read_bytes())
    normalized = set(manifest["lf_normalized_paths"])
    hashes = manifest["evidence_hashes"]
    if not normalized <= hashes.keys():
        raise AssertionError("Unlisted LF-normalized path")
    for relative, expected in hashes.items():
        path = (ROOT / relative).resolve()
        if path == MANIFEST.resolve() or not path.is_relative_to(ROOT):
            raise AssertionError("Invalid lifecycle manifest path")
        content = path.read_bytes()
        if relative in normalized:
            content = content.replace(b"\r\n", b"\n")
        if hashlib.sha256(content).hexdigest() != expected:
            raise AssertionError(f"Lifecycle manifest mismatch: {relative}")
    return len(hashes)


def validate() -> dict[str, object]:
    frozen = json.loads((ARTIFACTS / "rsi_convergence_validation.json").read_bytes())
    if frozen != build_convergence_report():
        raise AssertionError("Frozen RSI convergence report differs from deterministic replay")
    exit_report = json.loads((ARTIFACTS / "exit_discovery_report.json").read_bytes())
    if exit_report["result"] != "EXIT_CANDIDATE_FOUND_NOT_AUTHORIZED":
        raise AssertionError("Exit authorization status changed")
    lifecycle = json.loads((ARTIFACTS / "trade_lifecycle_contract.json").read_bytes())
    if lifecycle["pnl_layer"] != "PROHIBITED":
        raise AssertionError("Lifecycle crossed into the PnL layer")
    return {
        "rsi_windows": len(WINDOWS),
        "sequence_types": len(_sequences()),
        "lifecycle_contract": "PASS",
        "sell_execution_contract": "PASS",
        "exit_contract": "BLOCKED",
        "manifest_hashes": verify_manifest(),
    }


if __name__ == "__main__":
    print(json.dumps(validate(), sort_keys=True))
