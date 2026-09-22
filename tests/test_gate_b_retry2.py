from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest

from scripts.validate_stage1_gate_b_retry2 import (
    ART, ROOT, causal_admitted, input_available_at, load, matching_multipliers,
    normalize_constraint, select_preopen_version, validate, verify_manifest,
)


def test_unit_multiplier_unique_on_each_comparable_exchange_row():
    rows = load("market_unit_reconciliation.json")["samples"]
    for exchange in ("SSE", "SZSE"):
        matched = [r for r in rows if r["exchange"] == exchange and r["reconciliation"] == "MATCH"]
        assert len(matched) >= 3
        assert all(matching_multipliers(r) == {"volume": [1], "amount": [1]} for r in matched)
    outlier = next(r for r in rows if r["id"] == "300750.SZ/2024-09-27")
    assert matching_multipliers(outlier) == {"volume": [], "amount": []}


def test_official_snapshot_bytes_and_rows_match_reconciliation():
    registry = load("source_registry.json")
    snapshots = {s["file"]: s for s in registry["official_snapshots"]}
    for filename, source in snapshots.items():
        assert hashlib.sha256((ART / filename).read_bytes()).hexdigest() == source["sha256"]
    for row in load("market_unit_reconciliation.json")["samples"]:
        security, day = row["id"].split("/")
        data = json.loads((ART / row["official_file"]).read_bytes())
        if row["exchange"] == "SSE":
            found = next(x for x in data["kline"] if str(x[0]) == day.replace("-", ""))
            assert [found[5], found[6]] == [row["official_volume"], row["official_amount"]]
        else:
            found = data[0]["data"][0]
            assert (found["zqdm"], found["jyrq"]) == (security[:6], day)
            assert Decimal(found["cjgs"].replace(",", "")) == Decimal(row["official_volume"])
            assert Decimal(found["cjje"].replace(",", "")) == Decimal(row["official_amount"])


def test_vendor_raw_values_match_frozen_sdk_captures():
    old = json.loads((ROOT / "artifacts/stage1_gate_a/live_core_01.json").read_bytes())
    new = load("live_boundary_2026_py313.json")
    captures = {}
    for document in (old, new):
        for call in document["calls"]:
            if call.get("api") != "bars":
                continue
            for security, table in call.get("response", {}).get("tables", {}).items():
                for sample in table["sample"]:
                    captures[(security, sample["kline_time"][:10])] = sample
    for row in load("market_unit_reconciliation.json")["samples"]:
        security, day = row["id"].split("/")
        raw = captures[security, day]
        assert raw["volume"] == row["vendor_volume"]
        assert Decimal(str(raw["amount"])) == Decimal(row["vendor_amount"])


def base_constraint():
    return {"security_id": "600518.SH", "trade_date": "2024-07-04",
            "reference_price": "1.93", "price_tick": "0.01",
            "limit_up_price": "2.12", "limit_down_price": "1.74",
            "trading_allowed": True, "suspended": False,
            "source": "AmazingData 1.1.6 qualified daily absolute", "source_priority": 2,
            "retrieved_at": "2026-09-22T12:00:00+08:00",
            "raw_provider_value": {"IS_ST_SEC": "1", "PRICE_HIGH_LMT_RATE": "0.10"}}


def verified_authority():
    return {"state": "VERIFIED", "limit_applicable": True, "limit_type": "RATE",
            "limit_up_price": "2.12", "limit_down_price": "1.74"}


def test_unqualified_vendor_prices_never_admitted():
    assert normalize_constraint(base_constraint(), None) is None
    assert normalize_constraint(base_constraint(), {**verified_authority(), "state": "UNVERIFIED"}) is None


def test_absolute_price_not_overridden_by_st_label_or_rate():
    row = base_constraint()
    row["raw_provider_value"] = {"IS_ST_SEC": "1", "PRICE_HIGH_LMT_RATE": "0.05"}
    normalized = normalize_constraint(row, verified_authority())
    assert normalized["limit_up_price"] == "2.12"
    assert normalized["raw_provider_value"] == row["raw_provider_value"]


@pytest.mark.parametrize("change", [
    {"limit_up_price": "0"}, {"limit_up_price": "99999.999"},
    {"limit_down_price": "2.13"}, {"reference_price": "0"},
    {"price_tick": "0"}, {"limit_up_price": "2.125"},
    {"retrieved_at": "2026-09-22T12:00:00"},
])
def test_bad_or_missing_limited_prices_fail_closed(change):
    assert normalize_constraint({**base_constraint(), **change}, verified_authority()) is None


def test_no_limit_requires_independent_authority_and_nulls_sentinel():
    row = {**base_constraint(), "limit_up_price": "0", "limit_down_price": "0"}
    confirmed = {"state": "VERIFIED", "limit_applicable": False, "limit_type": "NONE"}
    result = normalize_constraint(row, confirmed)
    assert result["limit_applicable"] is False
    assert result["limit_up_price"] is None and result["limit_down_price"] is None
    assert result["raw_provider_value"] == row["raw_provider_value"]
    assert normalize_constraint(row, None) is None


def test_suspension_excludes_trading_even_with_theoretical_limits():
    row = {**base_constraint(), "trading_allowed": False, "suspended": True}
    result = normalize_constraint(row, verified_authority())
    assert result["suspended"] is True and result["trading_allowed"] is False


def test_matrix_ipo_exdiv_2026_boundary_and_close_limit():
    cases = {r["case"]: r for r in load("daily_constraint_validation.json")["cases"]}
    assert cases["ipo_no_limit"]["limit_up_price"] is None
    assert cases["ipo_no_limit"]["raw_vendor_rate"] == 999
    assert Decimal(cases["ex_dividend"]["prior_raw_close"]) - Decimal(cases["ex_dividend"]["cash_dividend_per_share"]) == Decimal("1490.624")
    assert cases["ex_dividend"]["reference_price"] == "1490.62"
    assert cases["risk_warning_10_2026"]["vendor_rate"] == "0.05"
    assert cases["risk_warning_10_2026"]["official_rate"] == "0.10"
    assert cases["risk_warning_10_2026"]["limit_up_price"] == "1.62"
    assert cases["risk_warning_introduction"]["result"] == "MISSING"
    assert cases["near_limit"]["close_equals_limit_up"] is False
    assert cases["actual_close_limit"]["close_equals_limit_up"] is True


def test_preopen_latest_version_before_open_wins_and_ambiguous_tie_rejects():
    rows = [
        {"trade_date":"2024-07-04","published_at":"2024-07-04T07:00:00+08:00","source_priority":1,"up":"2.03"},
        {"trade_date":"2024-07-04","published_at":"2024-07-04T08:30:00+08:00","source_priority":1,"up":"2.12"},
        {"trade_date":"2024-07-04","published_at":"2024-07-04T10:00:00+08:00","source_priority":1,"up":"2.20"},
    ]
    assert select_preopen_version(rows, "2024-07-04T09:30:00+08:00")["up"] == "2.12"
    assert select_preopen_version([rows[1], rows[1]], "2024-07-04T09:30:00+08:00") is None


def eod_item(day="2024-07-04", finalized="2024-07-04T15:00:00+08:00"):
    return {"availability_class":"EOD_FINALIZED", "trade_date":day,
            "finalized_at":finalized,"retrieved_at":"2026-09-22T12:00:00+08:00",
            "provider_version":"AmazingData 1.1.6","request_hash":"frozen-request",
            "content_sha256":"frozen-content"}


def test_future_bar_leakage_rejected():
    assert not causal_admitted(eod_item("2024-07-05", "2024-07-05T15:00:00+08:00"),
                               "2024-07-04T15:01:00+08:00")


def test_premature_same_day_eod_rejected():
    assert not causal_admitted(eod_item(), "2024-07-04T14:30:00+08:00")


def test_finalized_eod_admitted_and_derived_max_input_time():
    item = eod_item()
    assert causal_admitted(item, "2024-07-04T15:01:00+08:00")
    derived = {"availability_class":"DERIVED_EOD", "inputs":[item,
               {**item, "finalized_at":"2024-07-04T15:05:00+08:00"}]}
    assert input_available_at(derived).isoformat() == "2024-07-04T15:05:00+08:00"
    assert not causal_admitted(derived, "2024-07-04T15:01:00+08:00")


def test_unknown_context_and_unfrozen_raw_rejected():
    assert not causal_admitted({"availability_class":"CONTEXT_UNVERIFIED"},
                               "2024-07-04T15:01:00+08:00")
    assert not causal_admitted({**eod_item(), "content_sha256":None},
                               "2024-07-04T15:01:00+08:00")


def test_retry2_manifest_and_verdict():
    assert all(verify_manifest().values())
    assert validate() == {"C1":"CLOSED", "C2":"OPEN", "C3":"CLOSED",
                          "A1":"UNTRUSTED", "gate_b":"GATE_B_STOP",
                          "manifest_hashes":len(verify_manifest())}
