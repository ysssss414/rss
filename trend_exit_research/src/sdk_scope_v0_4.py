"""Offline inspection of vendor wheels. Never import or initialize the vendor SDK."""
from __future__ import annotations

import ast
import dis
from hashlib import sha256
import importlib.util
import marshal
from pathlib import Path
import types
import zipfile


def code_objects(code):
    yield code
    for item in code.co_consts:
        if isinstance(item, types.CodeType):
            yield from code_objects(item)


def read_codes(wheel, member):
    raw = wheel.read(member)
    if raw[:4] != importlib.util.MAGIC_NUMBER:
        raise ValueError("Use the matching Python bytecode version; never execute mismatched SDK code")
    return raw, {c.co_qualname: c for c in code_objects(marshal.loads(raw[16:]))}


def inspect_wheels(sdk_path: Path, tgw_path: Path):
    """Return inspectable Python-layer facts, separately from unproven native scope."""
    selected = {
        "query_api/market_data": ["MarketData.__init__", "MarketData.query_kline",
            "MarketData.query_kline.<locals>.get_kline_data"],
        "query_api/info_data": ["InfoData.__init__", "InfoData.get_history_stock_status"],
        "download_data/download_info_data": ["DownloadInfoData.__init__",
            "DownloadInfoData.download_hist_stock_status",
            "DownloadInfoData.download_hist_stock_status.<locals>.get_hist_stock_status"],
        "query_api/base_data": ["BaseData.__init__"],
        "login/tgw_login": ["login", "set_cfg"],
        "environment": ["QueryPara"],
    }
    functions, members, query_settings = {}, {}, {}
    with zipfile.ZipFile(sdk_path) as wheel:
        if "Version: 1.1.6" not in wheel.read("AmazingData-1.1.6.dist-info/METADATA").decode():
            raise ValueError("This audit is for AmazingData 1.1.6")
        for module, names in selected.items():
            member = f"AmazingData/{module}.pyc"
            raw, codes = read_codes(wheel, member)
            members[member] = sha256(raw).hexdigest()
            for name in names:
                code = codes[name]
                # No module execution, raw disassembly, or demo credential constants in the receipt.
                functions[name] = {"source_member": member, "first_line": code.co_firstlineno,
                    "arguments": list(code.co_varnames[:code.co_argcount]), "referenced_names": list(code.co_names),
                    "integer_constants": [x for x in code.co_consts if type(x) is int],
                    "stored_attributes": sorted({i.argval for i in dis.get_instructions(code) if i.opname == "STORE_ATTR"})}
                if name == "QueryPara":
                    instructions = list(dis.get_instructions(code))
                    for previous, current in zip(instructions, instructions[1:]):
                        if previous.opname == "LOAD_CONST" and current.opname == "STORE_NAME" and type(previous.argval) is int:
                            query_settings[current.argval] = previous.argval
    native_members = {}
    with zipfile.ZipFile(tgw_path) as wheel:
        interface = wheel.read("tgw/interface.py").decode().replace("\r", "")
        binding = wheel.read("tgw/win_py312_x64_package/tgw.py").decode().replace("\r", "")
        calls = {}
        for node in ast.parse(interface).body:
            if isinstance(node, ast.FunctionDef) and node.name in {"Login", "QueryKline", "QueryThirdInfo"}:
                calls[node.name] = sorted({ast.unparse(c.func) for c in ast.walk(node) if isinstance(c, ast.Call)})
        for name in ("tgw/interface.py", "tgw/win_py312_x64_package/tgw.py",
                     "tgw/win_py312_x64_package/_tgw.pyd", "tgw/win_py312_x64_package/tgw.dll"):
            native_members[name] = sha256(wheel.read(name)).hexdigest()
        native_bindings = {name: f"IGMDApi_{name} = _tgw.IGMDApi_{name}" in binding
                           for name in ("Init", "QueryKline", "QueryThirdInfo")}
    return {
        "version": "SDK_SCOPE_AUDIT_V0_4", "sdk_version": "1.1.6", "tgw_version": "1.0.8.7",
        "source_kind": "LOCAL_VENDOR_CP312_WHEEL_NOT_IMPORTED_OR_INSTALLED",
        "sdk_wheel_sha256": sha256(sdk_path.read_bytes()).hexdigest(),
        "tgw_wheel_sha256": sha256(tgw_path.read_bytes()).hexdigest(),
        "sdk_members": members, "functions": functions, "query_settings": query_settings, "tgw_members": native_members,
        "tgw_interface_calls": calls, "opaque_native_bindings": native_bindings,
        "python_bars_request_scope": "SINGLE_INPUT_CODE;CALENDAR_FILTER_BEFORE_REQUEST;DAY_PAGE_1500_SESSIONS",
        "python_status_request_scope": "SINGLE_INPUT_CODE;START_END_IN_THIRD_INFO_PARAMS;TWO_FUNCTION_IDS",
        "status_function_ids": ["A010060002", "A010060005"],
        "python_query_default_failure_retries": query_settings["retry_times"], "python_login_attempt_upper_bound": 6,
        "native_initialization_scope_verified": False, "native_query_scope_verified": False,
        "native_retry_upper_bound": None, "native_request_upper_bound": None,
        "native_scope_evidence": None, "live_sdk_imports": 0, "live_initializations": 0,
        "live_python_data_attempts": 0, "live_market_network_requests": 0,
        "scope_verdict": "STOP_NETWORK_SCOPE_UNQUALIFIED",
        "reason": "Native IGMDApi_Init/QueryKline/QueryThirdInfo terminate in opaque binaries; no version-specific evidence bounds initialization, hidden requests, or native retries. Python arguments and response filtering cannot establish native behavior.",
    }
