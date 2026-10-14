"""Serial, request-guarded BaoStock raw capture. No other vendor endpoints admitted."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
from pathlib import Path
import socket
import time
from types import SimpleNamespace

import pandas as pd

from research.data.cache import SnapshotStore
from research.runs.manifest import canonical_bytes
from .capture_v0_4 import bounded_request


FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST"
MAP = {"300418.SZ": "sz.300418", "000858.SZ": "sz.000858", "603259.SH": "sh.603259"}


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    SnapshotStore(Path(path).parent)._atomic_write(Path(path), canonical_bytes(value), immutable=True)


class BoundedSocket:
    def __init__(self, owner, *args):
        self.owner, self.sock = owner, socket.socket(*args)
        self.sock.settimeout(owner.config["socket_timeout_seconds"])

    def connect(self, address):
        if address != ("public-api.baostock.com", 10030):
            raise ValueError("UNAUTHORIZED_VENDOR_ENDPOINT")
        record = {"address": list(address), "started_at": now()}
        self.owner.connections.append(record)
        try:
            self.sock.connect(address)
            record["status"] = "CONNECTED"
        except OSError as exc:
            record.update(status=type(exc).__name__, error=str(exc))
            raise

    def send(self, data):
        self.sock.sendall(data)
        return len(data)

    def recv(self, size):
        self.owner.check_time()
        data = self.sock.recv(size)
        if not data:
            raise ConnectionError("BAOSTOCK_PEER_CLOSED_CONNECTION")
        self.owner.received_bytes += len(data)
        if self.owner.received_bytes > self.owner.config["max_response_bytes"]:
            raise ValueError("RESPONSE_BYTE_LIMIT")
        return data

    def close(self):
        self.sock.close()


class CaptureSession:
    def __init__(self, config, output):
        if (Path(output) / "smoke_receipt.json").exists():
            raise ValueError("ONE_TIME_CAPTURE_ALREADY_FINISHED")
        import baostock as bs
        import baostock.util.socketutil as transport
        self.bs, self.config, self.output = bs, config, Path(output)
        if importlib.metadata.version("baostock") != config["baostock_version"]:
            raise ValueError("VENDOR_VERSION_CHANGED")
        self.began, self.deadline = time.monotonic(), None
        self.wire, self.calls, self.connections, self.sessions = [], [], [], []
        self.active, self.received_bytes = None, 0
        self.last_page = 0
        self.logged_in = False
        self.finished = set()
        original = transport.send_msg
        # Only this vendor module's socket factory is replaced; base interpreter is unchanged.
        transport.socket = SimpleNamespace(AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM,
            socket=lambda *args: BoundedSocket(self, *args))
        def checked_send(message):
            self.check_time()
            body = message[21:].split("\x01")
            method = body[0]
            record = {"method": method, "started_at": now(), "request_sha256": sha256(message.encode()).hexdigest()}
            if method == "query_history_k_data_plus":
                kind, code, start, end = self.active
                if kind != "bars" or body[4:10] != [MAP[code], FIELDS, start, end, "d", "3"]:
                    raise ValueError("WIRE_QUERY_SCOPE_VIOLATION")
                page = int(body[2])
                record.update(security_id=code, start=start, end=end, page=page, fields=FIELDS, frequency="d", adjustflag="3")
            elif method == "query_trade_dates":
                kind, code, start, end = self.active
                if kind != "calendar" or body[4:6] != [start, end]:
                    raise ValueError("WIRE_CALENDAR_SCOPE_VIOLATION")
                page = int(body[2])
                record.update(security_id=code, start=start, end=end, page=page)
            elif method in {"login", "logout"}:
                page = None
            else:
                raise ValueError("FORBIDDEN_VENDOR_API")
            if page is not None and (page < 1 or page > config["max_pages_per_attempt"] or
                    page != self.last_page + 1 or
                    sum(r["method"].startswith("query_") for r in self.wire) >= config["max_wire_data_requests"]):
                raise ValueError("WIRE_PAGE_OR_REQUEST_BUDGET")
            if page is not None:
                self.last_page = page
            self.wire.append(record)
            self.received_bytes = 0
            began = time.monotonic()
            response = original(message)
            record.update(elapsed_seconds=round(time.monotonic()-began, 6), response_bytes=self.received_bytes,
                response_sha256=sha256(response.encode()).hexdigest() if response is not None else None)
            # Keep exact protocol responses locally, including failed/empty queries; never publish session headers.
            save(self.output / "wire" / f"response_{len(self.wire):03d}.json", {"request": record, "response": response})
            return response
        transport.send_msg = checked_send

    def check_time(self):
        current = time.monotonic()
        if current-self.began > self.config["total_timeout_seconds"] or self.deadline is not None and current > self.deadline:
            raise TimeoutError("BOUNDED_CAPTURE_TIME_LIMIT")

    def login(self):
        self.deadline = time.monotonic() + self.config["query_timeout_seconds"]
        began = time.monotonic()
        result = self.bs.login()
        self.logged_in = result.error_code == "0"
        self.sessions.append({"operation": "login", "retrieved_at": now(), "error_code": result.error_code,
            "error_msg": result.error_msg, "elapsed_seconds": round(time.monotonic()-began, 6)})
        return self.logged_in

    def query(self, label, kind, code, start, end):
        bounded_request(code, start, end, "bars")
        if kind not in {"bars", "calendar"}:
            raise ValueError("FORBIDDEN_DATASET")
        key = (kind, code, start, end)
        if key in self.finished:
            raise ValueError("REQUEST_RETRY_BUDGET_ALREADY_USED")
        self.finished.add(key)
        for attempt in (1, 2):
            if len(self.calls) >= self.config["max_high_level_data_attempts"]:
                raise ValueError("HIGH_LEVEL_QUERY_BUDGET")
            self.active = (kind, code, start, end)
            self.last_page = 0
            self.deadline = time.monotonic() + self.config["query_timeout_seconds"]
            row = {"label": label, "kind": kind, "security_id": code, "vendor_code": MAP[code],
                "start": start, "end": end, "attempt": attempt, "started_at": now(), "data_api_invoked": False}
            self.calls.append(row)
            began, first_wire = time.monotonic(), len(self.wire)
            try:
                if not self.logged_in and not self.login():
                    raise ConnectionError("LOGIN_FAILED")
                row["data_api_invoked"] = True
                result = self.bs.query_history_k_data_plus(MAP[code], FIELDS, start_date=start, end_date=end,
                    frequency="d", adjustflag="3") if kind == "bars" else self.bs.query_trade_dates(start_date=start, end_date=end)
                rows = []
                while result.error_code == "0" and result.next():
                    self.check_time()
                    rows.append(result.get_row_data())
                    if len(rows) > self.config["max_rows_per_query"]:
                        raise ValueError("ROW_BUDGET")
                row.update(error_code=result.error_code, error_msg=result.error_msg, fields=result.fields, rows=len(rows))
                if result.error_code != "0":
                    raise ConnectionError("VENDOR_QUERY_ERROR")
                frame = pd.DataFrame(rows, columns=result.fields)
                row.update(status="RETURNED" if rows else "EMPTY_RESPONSE", retrieved_at=now())
                save(self.output / "raw" / f"{label}_{attempt}.json", {"request": row, "fields": result.fields, "rows": rows})
                return frame, row
            except (OSError, ConnectionError, TimeoutError) as exc:
                row.update(status="REQUEST_FAILED", exception=type(exc).__name__, error=str(exc), retrieved_at=now())
                self.logged_in = False
            except ValueError as exc:
                row.update(status="SCOPE_OR_DATA_REJECTED", error=str(exc), retrieved_at=now())
                return None, row
            finally:
                row.update(elapsed_seconds=round(time.monotonic()-began, 6), wire_requests=len(self.wire)-first_wire)
                save(self.output / "attempts" / f"{label}_{attempt}.json", row)
            if attempt == 2:
                return None, row

    def close(self):
        if self.logged_in:
            self.deadline = time.monotonic() + self.config["query_timeout_seconds"]
            result = self.bs.logout()
            self.sessions.append({"operation": "logout", "retrieved_at": now(), "error_code": result.error_code,
                "error_msg": result.error_msg})
        import baostock.common.context as context
        if getattr(context, "default_socket", None) is not None:
            context.default_socket.close()

    def receipt(self):
        return {"version": self.config["version"], "sdk_version": importlib.metadata.version("baostock"),
            "calls": self.calls, "wire": self.wire, "connections": self.connections, "sessions": self.sessions,
            "elapsed_seconds": round(time.monotonic()-self.began, 6)}
