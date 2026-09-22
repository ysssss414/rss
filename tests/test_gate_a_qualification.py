"""Offline qualification checks over sanitized live captures. No SDK login."""
import json
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts.analyze_gate_a_evidence import analyze, call_map, load_evidence, rows
from scripts.qualify_amazingdata_gate_a import summarize_frame, validate_plan
from research.data.amazingdata import checked_sdk_bars
from research.data.contracts import DataContractError


@pytest.fixture(scope="module")
def evidence():
    return load_evidence()


def test_live_calendar_and_listing_boundaries(evidence):
    result = analyze(*evidence)
    assert all(v for k, v in result["checks"].items() if k.startswith("calendar_"))
    assert result["checks"]["historical_delisting_membership"]
    assert result["checks"]["historical_ipo_membership"]


def test_live_bars_are_valid_and_repeated_request_is_deterministic(evidence):
    result = analyze(*evidence)
    assert result["checks"]["all_captured_bar_rows_valid"]
    assert result["checks"]["repeated_bars_equal"]


@pytest.mark.parametrize("mutation", ["none", "wrong_key", "wrong_embedded_code", "future_date"])
def test_live_capture_offline_identity_mutation(evidence, mutation):
    call = call_map(evidence[0])["bars_normal"]
    frame = pd.DataFrame(rows(call, "000001.SZ")).drop(columns="observed_index")
    key = "000001.SZ"
    expected = None
    if mutation == "wrong_key":
        key, expected = "000002.SZ", "SYMBOL_MISMATCH"
    elif mutation == "wrong_embedded_code":
        frame["code"], expected = "000002.SZ", "SYMBOL_MISMATCH"
    elif mutation == "future_date":
        frame.loc[0, "kline_time"], expected = "2024-01-15T00:00:00", "AS_OF_VIOLATION"
    market = SimpleNamespace(query_kline=lambda *args, **kwargs: {key: frame.copy()})
    provider = SimpleNamespace(_ensure_market=lambda: market, ad=SimpleNamespace(constant=SimpleNamespace(Period=SimpleNamespace(day=SimpleNamespace(value=10000)))))
    if expected:
        with pytest.raises(DataContractError, match=expected):
            checked_sdk_bars(provider, "000001.SZ", date(2024, 1, 8), date(2024, 1, 12))
    else:
        assert len(checked_sdk_bars(provider, "000001.SZ", date(2024, 1, 8), date(2024, 1, 12))) == 5


def test_source_conflict_is_reported_instead_of_declaring_verified(evidence):
    result = analyze(*evidence)
    assert not result["checks"]["st_effective_date_matches_announcement"]
    assert result["checks"]["st_single_day_recheck_matches_initial"]
    assert result["checks"]["explicit_suspension_matches_announcement"]
    assert result["checks"]["suspension_has_no_fabricated_bar"]
    assert {r["date"] for r in result["limit_rate_price_mismatches"]} == {"20240703"}


def test_no_limit_sentinel_is_not_a_real_upper_limit_and_reference_is_separate(evidence):
    result = analyze(*evidence)
    assert result["ipo_zero_limit_dates"] == ["20241224", "20241225", "20241226", "20241227", "20241230"]
    assert result["checks"]["ex_dividend_reference_differs_from_yesterday_close"]


def test_same_day_factor_refresh_does_not_establish_pit(evidence):
    result = analyze(*evidence)
    assert result["checks"]["factor_refresh_same_observed_hash"]
    assert result["factor_revision_pit"] == result["historical_availability_pit"] == "UNVERIFIED"


def test_diagnostic_allowlist_omits_secret_values_and_arbitrary_indexes():
    frame = pd.DataFrame({"code": ["000001.SZ"], "close": [10], "password": ["sentinel-password"], "token": ["sentinel-token"]}, index=["sentinel-account"])
    encoded = json.dumps(summarize_frame(frame))
    assert "sentinel" not in encoded


@pytest.mark.parametrize("change", ["too_many_calls", "long_range", "too_many_symbols", "unknown_api", "credential_field"])
def test_diagnostic_request_budget_is_enforced(change):
    plan = [{"id": "small", "api": "bars", "codes": ["000001.SZ"], "start": 20240108, "end": 20240112}]
    if change == "too_many_calls":
        plan *= 41
    elif change == "long_range":
        plan[0]["end"] = 20250101
    elif change == "too_many_symbols":
        plan[0]["codes"] = [f"{n:06d}.SZ" for n in range(9)]
    elif change == "credential_field":
        plan[0]["password"] = "must-not-be-recorded"
    else:
        plan[0]["api"] = "scan_market"
    with pytest.raises(ValueError):
        validate_plan(plan)
