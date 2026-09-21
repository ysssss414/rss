from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from research.runs.manifest import canonical_bytes, digest, frame_records
from .contracts import BAR_COLUMNS, DataContractError, DataRequest
from .adjustment import validate_factors
from .validation import validate_bars, validate_calendar, validate_timestamps


METADATA_KEYS = {"provider_version", "sdk_version", "factor_schema", "calendar_verified",
                 "availability_verified", "source", "retrieved_at"}


@dataclass(frozen=True)
class Snapshot:
    content: bytes

    @property
    def snapshot_id(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def payload(self) -> dict:
        return json.loads(self.content)

    @classmethod
    def create(cls, *, request: DataRequest, bars: pd.DataFrame, calendar: list[date],
               factors: pd.DataFrame, metadata: dict):
        if set(metadata) != METADATA_KEYS:
            raise DataContractError("INVALID_METADATA", "Snapshot metadata must use the declared fields")
        if not isinstance(metadata["calendar_verified"], bool) or not isinstance(metadata["availability_verified"], bool):
            raise DataContractError("INVALID_METADATA", "Verification flags must be boolean")
        if metadata["factor_schema"] not in {"daily", "effective_events", "unknown", "none"}:
            raise DataContractError("INVALID_METADATA", "Unsupported factor schema")
        validate_timestamps(pd.Series([metadata["retrieved_at"]]), "retrieved_at")
        clean = validate_bars(bars, request)
        duplicate_count = clean.attrs.get("identical_duplicates_removed", 0)
        clean["snapshot_id"] = None  # Avoid a self-referential content hash.
        days = validate_calendar(calendar, request.start, request.end)
        if not set(clean.trade_date).issubset({d.isoformat() for d in days}):
            raise DataContractError("INVALID_CALENDAR", "Bar date absent from snapshot calendar")
        factor_rows = factors.copy()
        if not factor_rows.empty:
            required = {"ts_code", "trade_date", "factor", "available_at"}
            if set(factor_rows.columns) != required:
                raise DataContractError("INVALID_FACTOR", "Use canonical factor columns")
            if not factor_rows.ts_code.isin(request.codes).all():
                raise DataContractError("SYMBOL_MISMATCH", "Factor security mismatch")
            factor_rows = validate_factors(factor_rows, request.codes, request.end)
        data = {"bars": frame_records(clean), "calendar": [d.isoformat() for d in days],
                "factors": frame_records(factor_rows)}
        payload = {"version": "snapshot/1", "request": {"codes": sorted(request.codes),
                   "start": request.start.isoformat(), "end": request.end.isoformat()},
                   "metadata": metadata, **data, "hashes": {k: digest(v) for k, v in data.items()},
                   "identical_duplicates_removed": duplicate_count}
        return cls(canonical_bytes(payload))

    def bars(self) -> pd.DataFrame:
        frame = pd.DataFrame(self.payload["bars"], columns=BAR_COLUMNS)
        frame["snapshot_id"] = self.snapshot_id
        return frame

    @property
    def coverage_complete(self) -> bool:
        payload = self.payload
        expected = {(c, d) for c in payload["request"]["codes"] for d in payload["calendar"]}
        known = {(b["ts_code"], b["trade_date"]) for b in payload["bars"]
                 if b["trading_status"] != "UNKNOWN" and b["quality_status"] == "PASS"}
        return expected <= known and payload["metadata"]["calendar_verified"]


class SnapshotStore:
    """Immutable objects first, then atomic request coverage pointers. Single writer."""
    def __init__(self, root: Path | str):
        self.root = Path(root)

    @staticmethod
    def _check_id(value: str):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise DataContractError("INVALID_ARGUMENT", "Invalid content identity")

    def _object_path(self, snapshot_id):
        self._check_id(snapshot_id)
        return self.root / "objects" / f"{snapshot_id}.json"

    def _atomic_write(self, path: Path, content: bytes, *, immutable: bool):
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if immutable:
                try:
                    os.link(temporary_path, path)
                except FileExistsError:
                    if path.read_bytes() != content:
                        raise DataContractError("SNAPSHOT_CORRUPT", "Immutable object collision")
            else:
                os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def save(self, snapshot: Snapshot) -> str:
        self._atomic_write(self._object_path(snapshot.snapshot_id), snapshot.content, immutable=True)
        return snapshot.snapshot_id

    def load(self, snapshot_id: str) -> Snapshot:
        try:
            content = self._object_path(snapshot_id).read_bytes()
        except FileNotFoundError as exc:
            raise DataContractError("SNAPSHOT_NOT_FOUND", "Snapshot is not available") from exc
        snapshot = Snapshot(content)
        if snapshot.snapshot_id != snapshot_id:
            raise DataContractError("SNAPSHOT_CORRUPT", "Snapshot checksum mismatch")
        return snapshot

    def commit_coverage(self, key: str, snapshot_id: str):
        self._check_id(key)
        snapshot = self.load(snapshot_id)  # Durable content must exist before coverage.
        if not snapshot.coverage_complete:
            raise DataContractError("INCOMPLETE_RESPONSE", "Cannot cover unresolved dates")
        self._atomic_write(self.root / "coverage" / f"{key}.json",
                           canonical_bytes({"snapshot_id": snapshot_id}), immutable=False)

    def lookup(self, key: str) -> Snapshot | None:
        self._check_id(key)
        path = self.root / "coverage" / f"{key}.json"
        if not path.exists():
            return None
        snapshot = self.load(json.loads(path.read_bytes())["snapshot_id"])
        if not snapshot.coverage_complete:
            raise DataContractError("SNAPSHOT_CORRUPT", "Coverage references unresolved data")
        return snapshot
