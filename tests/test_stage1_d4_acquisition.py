import copy

import pytest

from scripts import validate_stage1_d4_acquisition as acquisition


def test_acquisition_stop_artifacts_are_consistent():
    assert acquisition.validate() == {"validation": "PASS", "gate_result": "STAGE1_D4_AUTHORITY_ACQUISITION_STOP", "raw_files": 0, "pending_exchange_days": 8}


def test_no_raw_sample_is_admitted():
    registry = acquisition.read("acquisition_registry.json")
    assert all(not s["sample_acquired"] and not s["raw_sha256"] for s in registry["sources"])


def test_boundary_and_control_dates_are_requested_for_both_exchanges():
    matrix = acquisition.read("source_request_matrix.json")
    for exchange, dataset in (("SSE", "cpxx0201MMDD.txt + cpxx0202MMDD.txt"), ("SZSE", "cashauctionparams_YYYYMMDD.xml")):
        request = next(r for r in matrix["requests"] if r["exchange"] == exchange and r["dataset"] == dataset)
        assert request["target_dates"] == matrix["target_dates"]
        assert request["access_status"] == "AUTHORIZATION_REQUIRED"


def test_fabricated_acquired_sample_is_rejected(monkeypatch):
    registry = copy.deepcopy(acquisition.read("acquisition_registry.json"))
    registry["sources"][0]["sample_acquired"] = True
    original = acquisition.read
    monkeypatch.setattr(acquisition, "read", lambda name: registry if name == "acquisition_registry.json" else original(name))
    with pytest.raises(ValueError, match="unfrozen acquired sample"):
        acquisition.validate()
