"""PIT operational Stage 1 smoke on frozen V1; never reads outcomes or D8."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict, deque
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from research.close_limit_up_v3 import evaluate_close_limit_up_v3
from research.observation_contract_v2 import evaluate_observation_trigger_v2
from research.observation_runtime_v2 import advance_episode_v2
from research.operational_trigger_qualification import (
    VERSION as QUALIFICATION_VERSION, VENDOR_SOURCE, qualify_operational_trigger,
)
from research.provisional_candidates import RsiPrefix, candidate_id_v3
from scripts.build_provisional_observation_candidates import _phase


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data/research_snapshots/REAL_RESEARCH_SNAPSHOT_V1"
CANDIDATES = ROOT / "artifacts/stage1_targeted_regime/provisional_observation_candidate_v3.parquet"
CANDIDATE_RECEIPT = ROOT / "artifacts/stage1_targeted_regime/provisional_candidate_inventory_v3.json"
OFFICIAL = ROOT / "artifacts/stage1_limit_regime/limit_regime_official_evidence_inventory.json"
CONTRACT = ROOT / "artifacts/stage1_operational_trigger_qualification_v1/contract.json"
PREWINDOW = ROOT / "artifacts/stage1_limit_regime/pre_window_calendar_context_v1.parquet"
DEFAULT_OUT = ROOT / "artifacts/stage1_real_strategy_smoke_v1"
SNAPSHOT_HASH = "a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67"


SIGNAL_DATASETS = {"security_master", "trading_calendar", "daily_bars", "daily_status", "adjustment_factor"}


def _path(dataset: str) -> str:
    if dataset not in SIGNAL_DATASETS:
        raise ValueError("Dataset is outside the signal path")
    part = "part.parquet" if dataset in {"security_master", "trading_calendar"} else "**/*.parquet"
    return str(SNAPSHOT / dataset / part).replace("\\", "/")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _rsi(rows: deque[tuple[object, object]]) -> Decimal | None:
    prefix = RsiPrefix()
    value = None
    for close, factor in rows:
        value = prefix.update(close, factor)
    return value


def _official_case(item: dict | None, code: str, day: date) -> tuple[str | None, str | None]:
    if (item is None or item.get("security_id") != code
            or item.get("effective_session") != day.isoformat()
            or item.get("source_publish_date", "9999") >= day.isoformat()
            or not item.get("source_document_id")):
        return None, None
    return item.get("official_rate_after"), item["source_document_id"]


def _source_rows(db: duckdb.DuckDBPyConnection, candidates: list[tuple],
                 cutoff: date, status_overrides: dict[tuple[str, date], bool] | None = None
                 ) -> tuple[dict, dict, tuple[date, ...], dict]:
    calendar = tuple(row[0] for row in db.execute(
        f"SELECT trade_date FROM read_parquet('{_path('trading_calendar')}') "
        "WHERE is_trading_day ORDER BY trade_date").fetchall())
    prefix = {exchange: tuple(row[0] for row in db.execute(
        f"SELECT trade_date FROM read_parquet('{str(PREWINDOW).replace(chr(92), '/')}') "
        "WHERE exchange=? AND is_trading_day ORDER BY trade_date", [exchange]).fetchall())
        for exchange in ("SH", "SZ")}
    windows = {}
    needed = set()
    for identity, code, trigger, encoded, *_ in candidates:
        days = tuple(date.fromisoformat(value) for value in json.loads(encoded))
        if (identity != candidate_id_v3(code, trigger.isoformat()) or len(days) != 5
                or days != tuple(sorted(set(days))) or days[-1] != trigger or trigger > cutoff):
            raise ValueError("Candidate key/window changed")
        windows[identity] = days
        needed.update((code, day) for day in days)
    db.execute("CREATE TEMP TABLE needed (security_id VARCHAR, trade_date DATE)")
    db.executemany("INSERT INTO needed VALUES (?, ?)", sorted(needed))
    triggers = sorted((code, trigger) for _, code, trigger, *_ in candidates)
    if len(set(triggers)) != len(triggers):
        raise ValueError("Duplicate candidate trigger grain")
    db.execute("CREATE TEMP TABLE trigger_keys (security_id VARCHAR, trade_date DATE)")
    db.executemany("INSERT INTO trigger_keys VALUES (?, ?)", triggers)
    trigger_status_rows = db.execute(f"""
        SELECT t.security_id, t.trade_date, s.is_st_sec, s.price_high_lmt_rate
        FROM trigger_keys t
        LEFT JOIN read_parquet('{_path('daily_status')}') s USING (security_id, trade_date)
        ORDER BY t.security_id, t.trade_date
    """).fetchall()
    if len(trigger_status_rows) != len(triggers):
        raise ValueError("Trigger status join changed grain")
    trigger_status = {(code, day): (is_st, rate)
                      for code, day, is_st, rate in trigger_status_rows}
    for key, override in (status_overrides or {}).items():
        if key in trigger_status:
            trigger_status[key] = (override, trigger_status[key][1])
    rows = db.execute(f"""
        WITH valid_bar AS (
            SELECT b.security_id, b.trade_date, b.close, b.high, f.factor,
                   lag(b.close) OVER (PARTITION BY b.security_id ORDER BY b.trade_date) previous_close,
                   lag(f.factor) OVER (PARTITION BY b.security_id ORDER BY b.trade_date) previous_factor
            FROM read_parquet('{_path('daily_bars')}') b
            JOIN read_parquet('{_path('daily_status')}') s USING (security_id, trade_date)
            LEFT JOIN read_parquet('{_path('adjustment_factor')}') f USING (security_id, trade_date)
            WHERE s.is_susp_sec IS NOT TRUE AND b.trade_date <= ?
        )
        SELECT n.security_id, n.trade_date, m.exchange, m.board, m.listing_date,
               b.close, b.high, b.factor, b.previous_close, b.previous_factor,
               s.is_xr_sec, s.is_wd_sec
        FROM needed n
        LEFT JOIN valid_bar b USING (security_id, trade_date)
        LEFT JOIN read_parquet('{_path('daily_status')}') s USING (security_id, trade_date)
        LEFT JOIN read_parquet('{_path('security_master')}') m USING (security_id)
        ORDER BY n.security_id, n.trade_date
    """, [cutoff]).fetchall()
    if len(rows) != len(needed):
        raise ValueError("Missing or duplicate frozen dependency row")
    case_rows = json.loads(OFFICIAL.read_text(encoding="utf-8"))["documents"]
    cases = {(item["security_id"], item["effective_session"]): item for item in case_rows}
    if len(cases) != len(case_rows):
        raise ValueError("Duplicate official effective-day evidence")
    events, details = {}, {}
    for (code, day, exchange, board, listed, close, high, factor, prior_close,
         prior_factor, is_xr, is_wd) in rows:
        if (code, day) not in needed:
            raise ValueError("Unexpected frozen reference row")
        try:
            phase = (_phase(listed, day, exchange, calendar, prefix)
                     if exchange in {"SH", "SZ"} and listed is not None else "UNRESOLVED")
        except ValueError:
            phase = "UNRESOLVED"
        is_st, vendor_rate = trigger_status.get((code, day), (None, None))
        official_rate, official_id = _official_case(
            cases.get((code, day.isoformat())), code, day) if (code, day) in trigger_status else (None, None)
        # This is the explicitly downgraded operational V3 application. The
        # frozen V3 engine is unchanged; no canonical exception claim is made.
        event = evaluate_close_limit_up_v3(
            board=board, listing_phase=phase, close=close, high=high,
            previous_raw_close=prior_close, factor=factor, previous_factor=prior_factor,
            is_xr_sec=is_xr, is_wd_sec=is_wd,
            special_exception="CONFIRMED" if official_rate == "0.05" else "CLEARED")
        events[(code, day)] = event
        details[(code, day)] = (board, phase, is_st, vendor_rate, official_rate, official_id)
    return events, details, calendar, prefix


def calculate(*, cutoff: date = date(2026, 9, 23),
              status_overrides: dict[tuple[str, date], bool] | None = None
              ) -> dict[str, list[dict]]:
    if _sha(SNAPSHOT / "metadata/manifest.json") != SNAPSHOT_HASH:
        raise ValueError("Frozen Snapshot V1 manifest changed")
    receipt = json.loads(CANDIDATE_RECEIPT.read_text(encoding="utf-8"))
    if (_sha(CANDIDATES) != receipt["candidate_parquet_sha256"]
            or receipt["v1_manifest_sha256"] != SNAPSHOT_HASH):
        raise ValueError("Frozen candidate input changed")
    if json.loads(CONTRACT.read_text(encoding="utf-8"))["version"] != QUALIFICATION_VERSION:
        raise ValueError("Operational qualification contract changed")
    db = duckdb.connect()
    db.execute("PRAGMA disable_progress_bar")
    candidates = db.execute(
        f"SELECT candidate_id, security_id, trigger_date, five_observation_dates, "
        f"rsi_filter_status, board FROM read_parquet('{str(CANDIDATES).replace(chr(92), '/')}') "
        "WHERE trigger_date <= ? ORDER BY security_id, trigger_date", [cutoff]).fetchall()
    events, details, calendar, _prefix = _source_rows(db, candidates, cutoff, status_overrides)
    calendar_index = {day: index for index, day in enumerate(calendar)}
    qualification, review, candidate_map = [], [], {}
    for identity, code, trigger, encoded, provisional_rsi_status, board in candidates:
        current = events[(code, trigger)]
        actual_board, phase, is_st, vendor_rate, official_rate, official_id = details[(code, trigger)]
        q = qualify_operational_trigger(
            board=board, trigger_date=trigger, listing_phase=phase, v3=current,
            vendor_is_st=is_st, vendor_limit_rate=vendor_rate, official_rate=official_rate,
            board_conflict=actual_board != board)
        window = tuple(date.fromisoformat(item) for item in json.loads(encoded))
        hits = sum(events[(code, day)].status == "TRUE" for day in window)
        record = {
            "candidate_id": identity, "security_id": code, "trigger_date": trigger.isoformat(),
            "board": board, "v3_trigger_state": current.status,
            "v3_reference_state": current.reference_day.classification,
            "v3_lookback_hits": hits, "provisional_rsi_status": provisional_rsi_status,
            "qualification": q.status, "evidence_level": q.evidence_level,
            "qualification_reason": q.reason, "vendor_is_st": is_st,
            "vendor_limit_rate": vendor_rate, "vendor_source": q.vendor_source,
            "vendor_conflict": q.vendor_conflict, "official_override": q.official_override,
            "official_document_id": official_id, "v3_self_qualification": q.v3_self_qualification,
            "rsi_replay_status": "PENDING", "rsi_replay_mode": "PENDING",
            "rsi14": "",
            "observation_decision": "PENDING", "observation_reason": "PENDING",
        }
        qualification.append(record)
        candidate_map[(code, trigger)] = (record, window, q)
        if q.status == "REVIEW_REQUIRED":
            review.append({
                "candidate_id": identity, "security_id": code, "trigger_date": trigger.isoformat(),
                "vendor_status": is_st, "conflict_reason": q.reason,
                "missing_field": "is_st_sec" if is_st is None else "",
                "v3_state": current.status, "v3_reference_state": current.reference_day.classification,
                "official_document_id": official_id or "", "review_priority":
                    "HIGH" if hits >= 4 else "NORMAL",
            })
    db.execute("CREATE TEMP TABLE candidate_codes (security_id VARCHAR)")
    db.executemany("INSERT INTO candidate_codes VALUES (?)", [(code,) for code in sorted({row[1] for row in candidates})])
    cursor = db.execute(f"""
        SELECT s.security_id, s.trade_date, m.listing_date, m.delisting_date, m.board, s.is_susp_sec,
               b.close, b.high, b.low, f.factor, s.is_xr_sec, s.is_wd_sec
        FROM read_parquet('{_path('daily_status')}') s
        JOIN candidate_codes c USING (security_id)
        LEFT JOIN read_parquet('{_path('security_master')}') m USING (security_id)
        LEFT JOIN read_parquet('{_path('daily_bars')}') b USING (security_id, trade_date)
        LEFT JOIN read_parquet('{_path('adjustment_factor')}') f USING (security_id, trade_date)
        WHERE s.trade_date <= ? ORDER BY s.security_id, s.trade_date
    """, [cutoff])
    observations, lifecycle, entries = [], [], []
    code = None
    history = deque(maxlen=150)
    full_rsi = RsiPrefix()
    last_five = deque(maxlen=5)
    previous_close = previous_factor = None
    previous_episode = None
    observation_count = 0
    for batch in iter(lambda: cursor.fetchmany(10000), []):
        for (security_id, day, listed, delisted, board, suspended, close, high, low, factor, is_xr, is_wd) in batch:
            if security_id != code:
                code, history, full_rsi, last_five = security_id, deque(maxlen=150), RsiPrefix(), deque(maxlen=5)
                previous_close = previous_factor = previous_episode = None
                observation_count = 0
            if suspended is not True:
                history.append((close, factor))
                full_value = full_rsi.update(close, factor)
                observation_count += 1
                if close is not None and factor is not None:
                    last_five.append((Decimal(str(close)), Decimal(str(factor))))
                else:
                    last_five.clear()
            else:
                full_value = None
            candidate = candidate_map.get((security_id, day))
            active = previous_episode is not None and previous_episode.status in {"ACTIVE", "SIGNALLED"}
            if candidate or active:
                full_prefix = listed is not None and listed >= calendar[0]
                mode = "FULL_AVAILABLE_PREFIX" if full_prefix else "TRUNCATED_SLICE_150_NONCANONICAL"
                if suspended is True:
                    rsi = None
                elif full_prefix:
                    rsi = full_value
                elif observation_count >= 120:
                    rsi = _rsi(history)
                else:
                    rsi = None
                if candidate:
                    record, window, q = candidate
                    record["rsi_replay_mode"] = mode
                    record["rsi_replay_status"] = "READY" if rsi is not None else "NOT_READY"
                    record["rsi14"] = str(rsi) if rsi is not None else ""
                    regime = ("QUALIFIED_10PCT" if q.status.startswith("QUALIFIED_") else
                              "REJECTED_NON_10PCT" if q.status == "REJECTED_BY_OPERATIONAL_STATUS"
                              else "UNRESOLVED")
                    decision = evaluate_observation_trigger_v2(
                        board=record["board"], lookback=[events[(security_id, d)] for d in window],
                        trigger_regime=regime, rsi14_status=record["rsi_replay_status"], rsi14=rsi)
                    record["observation_decision"] = decision.status
                    record["observation_reason"] = decision.reason
                else:
                    record = None
                trigger_qualified = record is not None and record["observation_decision"] == "QUALIFIED"
                quote_valid = (suspended is True or all(value is not None for value in (close, high, low, factor))
                               and Decimal(str(low)) > 0 and Decimal(str(factor)) > 0
                               and Decimal(str(low)) <= Decimal(str(close)) <= Decimal(str(high)))
                ma_raw = None
                if not suspended and factor and len(last_five) == 5:
                    ma_raw = sum((price * adj / Decimal(str(factor)) for price, adj in last_five),
                                 Decimal(0)) / 5
                entry_v3 = evaluate_close_limit_up_v3(
                    board=board, listing_phase="NORMAL_LISTED",
                    close=close, high=high, previous_raw_close=previous_close,
                    factor=factor, previous_factor=previous_factor,
                    is_xr_sec=is_xr, is_wd_sec=is_wd, special_exception="CLEARED")
                step = advance_episode_v2(
                    previous=previous_episode, security_id=security_id, day=day,
                    session_index=calendar_index[day], trigger_qualified=bool(trigger_qualified),
                    suspended=suspended is True, quote_valid=quote_valid, rsi=rsi, ma5_raw=ma_raw,
                    raw_low=_decimal(low), raw_close=_decimal(close), indicator_close=_decimal(close),
                    entry_v3_true=entry_v3.status == "TRUE",
                    hard_invalid=is_wd is True or delisted is not None and day >= delisted)
                if step.event != "NOT_ADMITTED":
                    lifecycle.append({"security_id": security_id, "date": day.isoformat(),
                                      "observation_instance_id": step.episode.observation_instance_id,
                                      "status": step.episode.status, "event": step.event})
                if trigger_qualified and step.episode is not None and step.episode.admission_date == day:
                    observations.append({
                        "candidate_id": record["candidate_id"], "security_id": security_id,
                        "trigger_date": day.isoformat(), "observation_instance_id": step.episode.observation_instance_id,
                        "v3_lookback_hits": record["v3_lookback_hits"], "rsi14": str(rsi),
                        "rsi_replay_mode": mode, "trigger_regime_qualification_method": record["qualification"],
                        "trigger_evidence_level": record["evidence_level"],
                        "trigger_vendor_status": record["vendor_is_st"],
                        "trigger_vendor_source": VENDOR_SOURCE,
                        "trigger_vendor_conflict": record["vendor_conflict"],
                        "trigger_official_override": record["official_override"],
                        "trigger_official_document_id": record["official_document_id"],
                        "trigger_v3_self_qualification": record["v3_self_qualification"],
                    })
                if step.entry_reasons:
                    entries.append({"security_id": security_id, "entry_date": day.isoformat(),
                                    "observation_instance_id": step.episode.observation_instance_id,
                                    "entry_reasons": "|".join(step.entry_reasons),
                                    "execution_intent": step.execution_intent,
                                    "entry_v3_state": entry_v3.status,
                                    "rsi14": str(rsi), "ma5_raw": str(ma_raw) if ma_raw else "",
                                    "raw_low": str(low), "raw_close": str(close),
                                    "fill_claim": False})
                previous_episode = step.episode
            if suspended is not True:
                previous_close, previous_factor = close, factor
    for row in qualification:
        if row["observation_decision"] == "PENDING":
            if row["qualification"] != "REVIEW_REQUIRED":
                raise ValueError("Qualified candidate T has no snapshot status row")
            row["rsi_replay_status"] = "NOT_EVALUATED"
            row["rsi_replay_mode"] = "SOURCE_ROW_MISSING"
            row["observation_decision"] = "UNRESOLVED"
            row["observation_reason"] = "TRIGGER_SOURCE_ROW_MISSING"
    return {"qualification": qualification, "review": review,
            "observations": observations, "lifecycle": lifecycle, "entries": entries}


def _write_csv(path: Path, rows: list[dict], fields: tuple[str, ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _prefix_of(rows: dict[str, list[dict]], cutoff: date) -> dict[str, list[dict]]:
    date_fields = {"qualification": "trigger_date", "review": "trigger_date",
                   "observations": "trigger_date", "lifecycle": "date", "entries": "entry_date"}
    return {name: [row for row in values if date.fromisoformat(row[date_fields[name]]) <= cutoff]
            for name, values in rows.items()}


def _case_audit(rows: dict[str, list[dict]]) -> dict:
    def first(name: str, predicate):
        return next((row for row in rows[name] if predicate(row)), None)

    by_security = defaultdict(list)
    for row in rows["observations"]:
        by_security[row["security_id"]].append(row)
    reentry = next((values[:2] for values in by_security.values() if len(values) > 1), None)
    cases = {
        "v3_qualified_trigger": first("qualification", lambda row: row["qualification"] == "QUALIFIED_BY_V3_PRICE_PATH"),
        "operational_qualified_trigger": first("qualification", lambda row: row["qualification"] == "QUALIFIED_BY_OPERATIONAL_STATUS"),
        "operational_rejected_trigger": first("qualification", lambda row: row["qualification"] == "REJECTED_BY_OPERATIONAL_STATUS"),
        "review_required_trigger": rows["review"][0] if rows["review"] else None,
        "formal_4_of_5": first("observations", lambda row: row["v3_lookback_hits"] == 4),
        "formal_5_of_5": first("observations", lambda row: row["v3_lookback_hits"] == 5),
        "entry_a": first("entries", lambda row: "ENTRY_A" in row["entry_reasons"]),
        "entry_b": first("entries", lambda row: "ENTRY_B" in row["entry_reasons"]),
        "limit_up_entry_intent": first("entries", lambda row: row["execution_intent"] == "LIMIT_UP_CLOSE_BOARD_INTENT"),
        "normal_close_entry_intent": first("entries", lambda row: row["execution_intent"] == "NORMAL_CLOSE_INTENT"),
        "expiry": first("lifecycle", lambda row: "EXPIRED" in row["status"]),
        "suspension": first("lifecycle", lambda row: row["event"] == "SUSPENDED_NO_ENTRY_EVALUATION"),
        "nonoverlap_reentry": reentry,
    }
    official = json.loads(OFFICIAL.read_text(encoding="utf-8"))["documents"]
    case = next(item for item in official if item["security_id"] == "600518.SH"
                and item["effective_session"] == "2024-07-04")
    gate_b = json.loads((ROOT / "artifacts/stage1_close_limit_up_v3/gate_b_reconciliation.json")
                        .read_text(encoding="utf-8"))["cases"]
    gate_case = next(item for item in gate_b if item["case"] == "sse_risk_removal_2024_07_04")
    vendor_st, vendor_rate = duckdb.connect().execute(
        f"SELECT is_st_sec, price_high_lmt_rate FROM read_parquet('{_path('daily_status')}') "
        "WHERE security_id='600518.SH' AND trade_date=DATE '2024-07-04'").fetchone()
    if vendor_st is not True or vendor_rate != 0.1 or gate_case["v3_result"] != "TRUE":
        raise ValueError("600518 frozen official/V3/vendor regression changed")
    cases["600518_vendor_conflict_override"] = {
        "security_id": "600518.SH", "date": "2024-07-04",
        "official_document_id": case["source_document_id"],
        "official_rate_after": case["official_rate_after"],
        "vendor_stale_st": vendor_st, "vendor_limit_rate": vendor_rate,
        "v3_status": gate_case["v3_result"],
        "operational_qualification": qualify_operational_trigger(
            board="SSE Main", trigger_date=date(2024, 7, 4), listing_phase="NORMAL_LISTED",
            v3=evaluate_close_limit_up_v3(
                board="SSE Main", listing_phase="NORMAL_LISTED",
                close="2.12", high="2.12", previous_raw_close="1.93",
                factor="48.081333", previous_factor="48.081333",
                is_xr_sec=False, is_wd_sec=False, special_exception="CLEARED"),
            vendor_is_st=vendor_st, vendor_limit_rate=vendor_rate,
            official_rate=case["official_rate_after"]).status,
        "note": "Official dated case, not a provisional trigger or formal Observation sample",
    }
    return {"schema": "real-strategy-smoke-case-audit/1",
            "scope": "Input through each trigger/Entry day only; no outcomes",
            "cases": cases}


def main(out: Path = DEFAULT_OUT) -> None:
    first = calculate()
    second = calculate()
    if first != second:
        raise ValueError("Smoke rerun is not deterministic")
    prefix_dates = (date(2025, 6, 30), date(2026, 7, 6))
    for cutoff in prefix_dates:
        if calculate(cutoff=cutoff) != _prefix_of(first, cutoff):
            raise ValueError(f"PIT prefix invariance failed at {cutoff}")
    future = next(row for row in first["qualification"]
                  if row["trigger_date"] > prefix_dates[0].isoformat()
                  and row["qualification"] == "QUALIFIED_BY_OPERATIONAL_STATUS"
                  and row["vendor_is_st"] is False)
    future_key = (future["security_id"], date.fromisoformat(future["trigger_date"]))
    mutated = calculate(status_overrides={future_key: True})
    if (mutated["qualification"] == first["qualification"]
            or _prefix_of(mutated, prefix_dates[0]) != _prefix_of(first, prefix_dates[0])):
        raise ValueError("Future T-status mutation leaked into prior events")
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, rows in first.items():
        if not rows:
            raise ValueError(f"No schema-bearing rows for {name}")
        path = out / f"{name}.csv"
        _write_csv(path, rows, tuple(rows[0]))
        files[name] = {"path": path.name, "sha256": _sha(path), "count": len(rows)}
    case_path = out / "case_audit.json"
    case_path.write_bytes((json.dumps(_case_audit(first), indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    files["case_audit"] = {"path": case_path.name, "sha256": _sha(case_path)}
    strict = [row for row in first["qualification"] if row["v3_lookback_hits"] >= 4
              and row["rsi_replay_status"] == "READY" and Decimal(row["rsi14"]) > 70]
    episode_by_security = Counter(row["security_id"] for row in first["observations"])
    counts = {"qualification": dict(Counter(row["qualification"] for row in first["qualification"])),
              "provisional_candidates": len(first["qualification"]),
              "strict_v3_rsi_candidates": len(strict),
              "formal_observation_count": len(first["observations"]),
              "formal_observation_securities": len(episode_by_security),
              "nonoverlap_reentry_securities": sum(count > 1 for count in episode_by_security.values()),
              "observation_method": dict(Counter(row["trigger_regime_qualification_method"] for row in first["observations"])),
              "observation_hits": dict(Counter(row["v3_lookback_hits"] for row in first["observations"])),
              "lifecycle": dict(Counter(row["event"] for row in first["lifecycle"])),
              "entry": dict(Counter(reason for row in first["entries"] for reason in row["entry_reasons"].split("|"))),
              "intent": dict(Counter(row["execution_intent"] for row in first["entries"])),
              "rsi_replay": dict(Counter(row["rsi_replay_status"] for row in first["qualification"])),
              "review_reasons": dict(Counter(row["conflict_reason"] for row in first["review"])),
              "review_years": dict(Counter(row["trigger_date"][:4] for row in first["review"])),
              "known_official_false_normal_candidates": sum(
                  row["qualification_reason"] == "OFFICIAL_RISK_WARNING_DAY"
                  and row["vendor_is_st"] is False for row in first["qualification"]),
              "vendor_conflict_candidates": sum(row["vendor_conflict"] for row in first["qualification"])}
    code_files = (ROOT / "research/operational_trigger_qualification.py",
                  ROOT / "research/observation_runtime_v2.py", Path(__file__))
    manifest = {
        "schema": "real-strategy-smoke-run-v1/1", "run_id": "REAL_STRATEGY_SMOKE_RUN_V1",
        "status": "RUN_MATERIALIZED", "snapshot_id": "REAL_RESEARCH_SNAPSHOT_V1",
        "snapshot_manifest_sha256": SNAPSHOT_HASH,
        "contracts": ["CLOSE_LIMIT_UP_10PCT_V3", "OBSERVATION_POOL_CONTRACT_V2",
                      QUALIFICATION_VERSION, "RSI14_PROJECT_V1", "RSI_WARMUP_POLICY_V1",
                      "TRADE_LIFECYCLE_V1", "ENTRY_A_PULLBACK_TO_MA5_V1",
                      "ENTRY_B_RSI_RECROSS_70_V1"],
        "vendor_operational_source": VENDOR_SOURCE,
        "vendor_source_role": "OPERATIONAL_SCREEN_NOT_CANONICAL_HISTORY",
        "rsi_old_listing_mode": "TRUNCATED_SLICE_150_NONCANONICAL; minimum 120",
        "v3_application": "OPERATIONAL_EXCEPTION_CLEARANCE_NOT_CANONICAL_HISTORICAL_FACT",
        "candidate_sha256": _sha(CANDIDATES), "official_inventory_sha256": _sha(OFFICIAL),
        "operational_contract_sha256": _sha(CONTRACT),
        "code_sha256": {path.relative_to(ROOT).as_posix(): _sha(path) for path in code_files},
        "config": {"cutoff": "2026-09-23", "target_boards": ["SSE Main", "SZSE Main"],
                   "truncated_rsi_minimum": 120, "truncated_rsi_target": 150,
                   "lookback_valid_observations": 5, "observation_horizon_exchange_sessions": 7},
        "files": files, "counts": counts, "reruns_identical": True,
        "prefix_invariance_dates": [day.isoformat() for day in prefix_dates],
        "future_status_mutation_key": [future_key[0], future_key[1].isoformat()],
        "future_mutation_prefix_invariant": True,
        "signal_source_datasets": sorted(SIGNAL_DATASETS),
        "outcomes_accessed": False, "d8_accessed": False,
    }
    (out / "run_manifest.json").write_bytes((json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    main(parser.parse_args().output_dir)
