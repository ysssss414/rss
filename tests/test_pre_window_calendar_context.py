from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pytest

from research.pre_window_calendar_context import ipo_session_number


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/stage1_limit_regime"


def test_minimal_context_and_four_ipo_phase_boundaries():
    receipt = json.loads((OUT / "pre_window_calendar_context_receipt.json").read_text())
    assert receipt["qualification_status"] == "QUALIFIED_MINIMAL_CONTEXT"
    artifact = str(OUT / "pre_window_calendar_context_v1.parquet").replace("\\", "/")
    db = duckdb.connect()
    assert db.execute(f"SELECT count(*) FROM read_parquet('{artifact}')").fetchone()[0] == 7
    assert db.execute(
        f"SELECT count(DISTINCT trade_date) FROM read_parquet('{artifact}')"
    ).fetchone()[0] == 4
    phases = {
        row["security_id"]: [item["ipo_session_number"] for item in row["first_four_v1_sessions"]]
        for row in receipt["listings"]
    }
    assert phases == {
        "301526.SZ": [5, 6, 7, 8],
        "603004.SH": [4, 5, 6, 7],
        "301578.SZ": [3, 4, 5, 6],
        "301566.SZ": [2, 3, 4, 5],
    }


def test_ipo_phase_requires_a_qualified_calendar():
    calendar = (date(2023, 12, 28), date(2023, 12, 29), date(2024, 1, 2))
    assert ipo_session_number(date(2023, 12, 28), date(2024, 1, 2), calendar) == 3
    with pytest.raises(ValueError):
        ipo_session_number(date(2023, 12, 28), date(2024, 1, 3), calendar)
    with pytest.raises(ValueError):
        ipo_session_number(date(2023, 12, 28), date(2024, 1, 2), calendar[::-1])
