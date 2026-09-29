from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json

import pytest

from research.entry_event_outcome_v1 import HORIZONS, evaluate_event
from scripts import run_entry_event_study_v1 as run


def fixture():
    sessions = tuple(f"2026-08-{i:02d}" for i in range(1, 26))
    bars = {("TEST.SH", day): {"close": 100.0, "high": 105.0, "low": 95.0}
            for day in sessions}
    statuses = {("TEST.SH", day): {"is_susp_sec": False, "is_wd_sec": False}
                for day in sessions}
    return sessions, bars, statuses


def evaluate(sessions, bars, statuses, **kw):
    return evaluate_event(security_id="TEST.SH", entry_date=kw.pop("entry_date", sessions[0]),
                          observation_id="frozen-observation", anchor=kw.pop("anchor", 100),
                          sessions=sessions, bars=bars, statuses=statuses,
                          actions=kw.pop("actions", {}), exclusions=kw.pop("exclusions", {}), **kw)


def test_horizons_anchor_window_and_mfe_mae_dates():
    sessions, bars, statuses = fixture()
    bars[("TEST.SH", sessions[1])] = {"close": 110.0, "high": 112.0, "low": 99.0}
    bars[("TEST.SH", sessions[3])] = {"close": 90.0, "high": 92.0, "low": 80.0}
    event, path = evaluate(sessions, bars, statuses)
    assert [event[f"h{h}_session_date"] for h in HORIZONS] == [sessions[h] for h in HORIZONS]
    assert event["signal_anchor_close"] == 100
    assert "fill_price" not in event
    assert event["h1_signal_forward_close_return"] == pytest.approx(.10)
    assert event["h1_signal_forward_mfe"] == pytest.approx(.12)
    assert event["h1_signal_forward_mae"] == pytest.approx(-.01)
    assert event["h3_signal_forward_mfe"] == pytest.approx(.12)
    assert event["h3_mfe_source_date"] == sessions[1]
    assert event["h3_signal_forward_mae"] == pytest.approx(-.20)
    assert event["h3_mae_source_date"] == sessions[3]
    assert len(path) == 20 and all(x["session_date"] > sessions[0] for x in path)


def test_suspension_no_forward_fill_and_valid_session_excursion():
    sessions, bars, statuses = fixture()
    bars.pop(("TEST.SH", sessions[3]))
    statuses[("TEST.SH", sessions[3])]["is_susp_sec"] = True
    event, path = evaluate(sessions, bars, statuses)
    assert event["h3_outcome_status"] == "NO_VALID_QUOTE"
    assert event["h3_missing_reason"] == "SUSPENDED"
    assert event["h3_signal_forward_close_return"] is None
    assert event["h3_valid_session_count"] == 2
    assert event["h3_signal_forward_mfe"] is not None
    assert event["h5_outcome_status"] == "AVAILABLE"
    assert path[2]["comparable_close"] is None


def test_each_horizon_is_right_censored_independently():
    sessions, bars, statuses = fixture()
    event, path = evaluate(sessions, bars, statuses, entry_date=sessions[-3])
    assert event["h1_outcome_status"] == "AVAILABLE"
    assert event["h3_outcome_status"] == "RIGHT_CENSORED"
    assert event["h20_signal_forward_mfe"] is None
    assert path[2]["session_date"] is None


def test_canonical_d8_scales_future_ohlc_but_not_anchor():
    sessions, bars, statuses = fixture()
    bars[("TEST.SH", sessions[3])] = {"close": 50, "high": 55, "low": 45}
    action = {sessions[3]: {"single_factor": 2, "known_date": sessions[2],
                            "event_kind": "DIVIDEND", "source_event_hash": "a" * 64}}
    event, path = evaluate(sessions, bars, statuses, actions=action)
    assert event["signal_anchor_close"] == 100
    assert path[2]["raw_close"] == 50
    assert path[2]["comparable_close"] == 100
    assert path[2]["comparable_high"] == 110
    assert event["h3_signal_forward_close_return"] == 0


def test_excluded_d8_event_fails_closed_from_effective_day():
    sessions, bars, statuses = fixture()
    event, path = evaluate(sessions, bars, statuses,
                           exclusions={sessions[3]: "DIVIDEND_REVISION_HISTORY_UNRESOLVED"})
    assert event["h1_outcome_status"] == "AVAILABLE"
    assert event["h3_outcome_status"] == "ADJUSTMENT_UNRESOLVED"
    assert event["h5_signal_forward_close_return"] is None
    assert event["h5_signal_forward_mfe"] is None
    assert path[4]["comparable_close"] is None


def test_delisting_has_no_imputed_terminal_loss_and_wd_quote_survives():
    sessions, bars, statuses = fixture()
    statuses[("TEST.SH", sessions[1])]["is_wd_sec"] = True
    quoted, _ = evaluate(sessions, bars, statuses)
    assert quoted["h1_outcome_status"] == "AVAILABLE"
    terminal, _ = evaluate(sessions, bars, statuses, delisting_date=sessions[3])
    assert terminal["h3_outcome_status"] == "TERMINAL_NO_QUOTE"
    assert terminal["h3_signal_forward_close_return"] is None


def test_future_bar_mutation_changes_outcome_not_frozen_signal():
    sessions, bars, statuses = fixture()
    event_before, _ = evaluate(sessions, bars, statuses)
    changed = deepcopy(bars)
    changed[("TEST.SH", sessions[3])]["close"] = 104
    event_after, _ = evaluate(sessions, changed, statuses)
    assert event_before["entry_event_id"] == event_after["entry_event_id"]
    assert event_before["signal_anchor_close"] == event_after["signal_anchor_close"]
    assert event_before["h3_signal_forward_close_return"] != event_after["h3_signal_forward_close_return"]


def test_frozen_population_and_wrong_manifest_hash(monkeypatch, tmp_path):
    manifest, entries, observations = run.validate_population()
    assert len(entries) == 505 and len(observations) == 993
    assert {x["entry_reasons"] for x in entries} == {
        "ENTRY_A_PULLBACK_TO_MA5_V1", "ENTRY_B_RSI_RECROSS_70_V1"}
    assert manifest["counts"]["review_reasons"]["REFERENCE_OR_V3_UNRESOLVED"] == 21
    copied = tmp_path / "smoke"
    copied.mkdir()
    for name in ("entries.csv", "observations.csv", "run_manifest.json"):
        (copied / name).write_bytes((run.SMOKE / name).read_bytes())
    altered = json.loads((copied / "run_manifest.json").read_text(encoding="utf-8"))
    altered["files"]["entries"]["sha256"] = "0" * 64
    (copied / "run_manifest.json").write_text(json.dumps(altered), encoding="utf-8")
    monkeypatch.setattr(run, "SMOKE", copied)
    with pytest.raises(ValueError, match="BLOCKED_EVENT_POPULATION_MISMATCH"):
        run.validate_population()
    altered["files"]["entries"]["sha256"] = run.ENTRY_SHA
    altered["config"]["observation_horizon_exchange_sessions"] = 8
    (copied / "run_manifest.json").write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(ValueError, match="BLOCKED_EVENT_POPULATION_MISMATCH"):
        run.validate_population()


def test_prespecified_group_keys_and_signal_firewall():
    rows = [{"entry_type": "ENTRY_A_PULLBACK_TO_MA5_V1", "observation_pattern": "4/5",
             "execution_intent": "NORMAL_CLOSE_INTENT",
             "trigger_qualification_method": "QUALIFIED_BY_V3_PRICE_PATH",
             "rsi_provenance": "TRUNCATED_SLICE_150_NONCANONICAL"},
            {"entry_type": "ENTRY_B_RSI_RECROSS_70_V1", "observation_pattern": "5/5",
             "execution_intent": "LIMIT_UP_CLOSE_BOARD_INTENT",
             "trigger_qualification_method": "QUALIFIED_BY_OPERATIONAL_STATUS",
             "rsi_provenance": "FULL_AVAILABLE_PREFIX"}]
    groups = run._groups(rows)
    assert len(groups) == 10 and all(len(value) == 1 for value in groups.values())
    assert len(groups["TRIGGER_QUALIFICATION:QUALIFIED_BY_OPERATIONAL_STATUS"]) == 1
    for name in ("observation_runtime_v2.py", "entry_signals.py", "operational_trigger_qualification.py"):
        source = (Path(__file__).resolve().parents[1] / "research" / name).read_text(encoding="utf-8")
        assert "entry_event_outcome_v1" not in source
        assert "outcome_path" not in source
        assert "from .d8 import" not in source


def test_committed_receipts_reconcile_without_private_vendor_rows():
    root = Path(__file__).resolve().parents[1] / "artifacts/stage1_entry_event_study_v1"
    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    coverage = json.loads((root / "entry_event_outcome_coverage.json").read_text(encoding="utf-8"))
    quality = json.loads((root / "entry_event_study_quality_receipt.json").read_text(encoding="utf-8"))
    cases = json.loads((root / "entry_event_study_case_audit.json").read_text(encoding="utf-8"))
    assert manifest["entry_count"] == quality["entry_rows"] == 505
    assert quality["status"] == "PASS" and quality["daily_path_rows"] == 10100
    for horizon in HORIZONS:
        item = coverage[f"H{horizon}"]
        assert item["total"] == 505
        assert (item["available"] + item["right_censored"] + item["no_valid_quote"]
                + item["other_missing"]) == 505
    assert coverage["H20"]["adjustment_unresolved"] == 3
    assert all(value is not None for value in cases["cases"].values())
    weak = cases["cases"]["large_mfe_weak_close"]["horizons"]["H20"]
    recovery = cases["cases"]["large_mae_recovery"]["horizons"]["H20"]
    assert weak["signal_forward_mfe"] >= .05 and weak["signal_forward_close_return"] <= 0
    assert recovery["signal_forward_mae"] <= -.05 and recovery["signal_forward_close_return"] > 0
    for name, expected in manifest["public_artifacts"].items():
        assert sha256((root / name).read_bytes()).hexdigest() == expected
    assert set(manifest["private_artifacts"]) == {
        "entry_event_outcome_v1.parquet", "entry_forward_path_h1_h20_v1.parquet"}
    assert not list(root.glob("*.parquet"))
