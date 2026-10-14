"""CSV registration and exact, ambiguity-rejecting master-data resolution."""
from __future__ import annotations

import csv
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re


def read_samples(path: Path, master) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"stock_name", "stock_code", "analysis_start", "analysis_end", "notes"}
        if not required <= set(reader.fieldnames or ()):
            raise ValueError("Manual sample CSV schema incomplete")
        rows = list(reader)
    result = []
    seen = set()
    for row in rows:
        if None in row:
            raise ValueError("Malformed manual sample CSV row")
        item = {key: (row.get(key) or "").strip() for key in sorted(required | {"sample_source", "sample_type", "observation_start", "observation_id"})}
        item["sample_type"] = item["sample_type"] or "HISTORICAL_CASE"
        if item["sample_type"] not in {"HISTORICAL_CASE", "FORWARD_OBSERVATION"}:
            raise ValueError("Unknown sample_type")
        if item["sample_type"] == "FORWARD_OBSERVATION":
            if (not item["observation_id"] or not item["observation_start"] or
                    date.fromisoformat(item["observation_start"]).isoformat() != item["observation_start"] or
                    item["analysis_start"] != item["observation_start"]):
                raise ValueError("Forward observation requires identity and matching initial/start date")
        if not item["stock_name"]:
            raise ValueError("Sample stock_name is required")
        for key in ("analysis_start", "analysis_end"):
            if item[key] and date.fromisoformat(item[key]).isoformat() != item[key]:
                raise ValueError("Use ISO sample dates")
        if item["analysis_start"] and item["analysis_end"] and item["analysis_start"] > item["analysis_end"]:
            raise ValueError("Reversed sample interval")
        matches = master.loc[master.security_name.eq(item["stock_name"]), "security_id"].unique().tolist()
        supplied = item["stock_code"]
        if supplied:
            if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", supplied):
                raise ValueError("Use exchange-qualified stock_code")
            status = "RESOLVED" if supplied in matches else "CODE_NAME_MISMATCH"
        else:
            status = "RESOLVED" if len(matches) == 1 else "AMBIGUOUS" if matches else "NAME_NOT_FOUND"
        item.update(resolution_status=status, resolved_code=(supplied or matches[0]) if status == "RESOLVED" else "",
                    code_candidates="|".join(sorted(matches)), sample_source=item["sample_source"] or "manual_csv")
        # Row order, notes, and future additions never enter signal identity or state.
        key = (item["stock_name"], supplied, item["analysis_start"], item["analysis_end"])
        if key in seen:
            raise ValueError("Duplicate sample identity; use a distinct research interval")
        seen.add(key)
        item["sample_id"] = sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16]
        result.append(item)
    return result
