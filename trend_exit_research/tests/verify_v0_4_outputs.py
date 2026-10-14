"""Independent offline evidence checks, including isolated vendor code on fake TGW.

Only selected function code objects execute. SDK modules, imports, initialization,
credential loaders and native libraries never execute.
"""
from contextlib import redirect_stdout
from datetime import date
from hashlib import sha256
import argparse
import copy
import io
import json
from pathlib import Path
import sys
from types import FunctionType, SimpleNamespace
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import pandas as pd
from trend_exit_research.src.sdk_scope_v0_4 import read_codes
from trend_exit_research.src.v0_4 import MODULE, file_hash, protection_guard


def fake_native_probes(wheel_path):
    code = "603259.SH"
    fake_requests, params, login_calls = [], {}, []
    env = SimpleNamespace(data={}, error_list=[], wait_event=SimpleNamespace(wait=lambda: None),
        refresh_error_list=lambda: None, refresh_event=lambda: None, refresh_data=lambda: None)
    lock = SimpleNamespace(acquire=lambda: None, release=lambda: None)
    def query_kline(req, spi):
        assert req.security_code == "603259" and req.market_type == "SH"
        assert 20260924 <= req.begin_date <= req.end_date <= 20260926
        assert req.cq_flag == 0 and req.auto_complete == 1
        fake_requests.append({"kind": "KLINE", "code": code, "start": req.begin_date, "end": req.end_date})
        env.data[code] = [pd.DataFrame({"kline_time": ["2026-09-24"], "close": [10.]})]
    tgw = SimpleNamespace(MarketType=SimpleNamespace(kSHFE=1, kCFFEX=2, kDCE=3, kCZCE=4, kINE=5),
        ReqKline=SimpleNamespace, QueryKline=query_kline)
    common = {"pd": pd, "copy": copy, "Exception": Exception}
    with zipfile.ZipFile(wheel_path) as wheel:
        _, market = read_codes(wheel, "AmazingData/query_api/market_data.pyc")
        function = FunctionType(market["MarketData.query_kline"], {**common, "tgw": tgw,
            "EnvKline": env, "Period": SimpleNamespace(day=SimpleNamespace(value=1)),
            "QueryPara": SimpleNamespace(req_kline_day_date_len=1500, req_kline_len=500, retry_times=0),
            "get_tgw_type_code": lambda _: ("SH", "603259"), "get_code": lambda *_: code,
            "KlineSpi": lambda req: SimpleNamespace(OnResponse=lambda *_: None),
            "convert_history_kline": lambda c, p, f: f})
        result = function(SimpleNamespace(lock=lock, calendar=[20260923, 20260924, 20260925, 20260928]),
            [code], 20260924, 20260926, 1)
        assert set(result) == {code} and len(fake_requests) == 1
        assert (fake_requests[0]["start"], fake_requests[0]["end"]) == (20260924, 20260925)
        _, status = read_codes(wheel, "AmazingData/download_data/download_info_data.pyc")
        def get_task():
            task = len(params) + 1
            params[task] = {}
            return task
        def set_param(task, key, value):
            params[task][key] = value
        def query_info(task, **kwargs):
            request = params[task]
            assert request["market_code"] == code and request["start_date"] == "20260924" and request["end_date"] == "20260926"
            assert request["function_id"] in {"A010060002", "A010060005"}
            fake_requests.append({"kind": "THIRD_INFO", **request})
            env.data[code] = pd.DataFrame({"TRADE_DATE": [20260924]})
        function = FunctionType(status["DownloadInfoData.download_hist_stock_status.<locals>.get_hist_stock_status"],
            {**common, "EnvHistStockStatus": env, "HistStockStatusSpi": lambda _: SimpleNamespace(OnResponse=lambda *_: None),
                "tgw": SimpleNamespace(GetTaskID=get_task, SetThirdInfoParam=set_param, QueryThirdInfo=query_info)},
            argdefs=("A010060002",))
        for function_id in ("A010060002", "A010060005"):
            result = function([code], {code: "20260924"}, "20260926", function_id)
            assert set(result) == {code}
        _, login = read_codes(wheel, "AmazingData/login/tgw_login.pyc")
        def fake_login(*_):
            login_calls.append("FAKE_LOGIN")
            return False
        def fake_exit(_):
            raise SystemExit
        function = FunctionType(login["login"], {"set_cfg": lambda *_, **kw: (None, None, SimpleNamespace(max_limitation=True)),
            "tgw": SimpleNamespace(Login=fake_login), "time": SimpleNamespace(sleep=lambda _: None), "exit": fake_exit})
        with redirect_stdout(io.StringIO()):
            try:
                function("unit_fixture", "synthetic_placeholder", "offline", 0, None, None)
            except SystemExit:
                pass
        assert len(login_calls) == 6
    return {"isolated_fake_kline_requests": 1, "isolated_fake_status_requests": 2,
        "isolated_fake_login_attempts": 6, "live_native_calls": 0,
        "evidence_level": "PYTHON_FUNCTION_SEMANTICS_ONLY_NOT_NATIVE_NETWORK_QUALIFICATION"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=MODULE / "artifacts/v0_4_final")
    parser.add_argument("--sdk-wheel", type=Path, default=ROOT.parent / "yh/AmazingData_sdk/AmazingData/AmazingData-1.1.6-cp312-none-any.whl")
    args = parser.parse_args()
    receipt = json.loads((args.out / "v0_4_run_receipt.json").read_text(encoding="utf-8"))
    for name, expected in receipt["outputs"].items():
        assert file_hash(args.out/name) == expected, name
    for name, expected in receipt["code_sha256"].items():
        assert file_hash(ROOT/name) == expected, name
    audit = json.loads((args.out / "v0_4_sdk_scope_audit.json").read_text(encoding="utf-8"))
    assert file_hash(args.sdk_wheel) == audit["sdk_wheel_sha256"]
    assert all(audit["opaque_native_bindings"].values())
    assert audit["query_settings"]["retry_times"] == 3
    assert audit["query_settings"]["req_kline_day_date_len"] == 1500
    assert audit["python_login_attempt_upper_bound"] == 6
    assert receipt["bounded_acquisition"] == "STOP" and receipt["live_market_network_requests"] == 0
    assert receipt["new_snapshots"] == []
    qualification = json.loads((args.out / "v0_4_data_qualification.json").read_text(encoding="utf-8"))
    assert len(qualification["cases"]) == 3
    for case in qualification["cases"]:
        assert case["target_status"] == "TARGET_CASE_WINDOW_UNCOVERED" and not case["computed"]
        assert case["newly_captured_bars"] == case["existing_bars_in_new_range"] == 0
    protected = protection_guard()
    automation_path = Path.home() / ".codex/automations/automation/automation.toml"
    original_automation = json.loads((MODULE / "reports/forward_capability_audit.json").read_text(encoding="utf-8"))["automation"]
    assert file_hash(automation_path) == original_automation["settings_sha256"]
    archived = MODULE / "artifacts/forward/WUXI_FORWARD_20261013_V0_1/inception.json"
    if not archived.exists():
        archived = MODULE / "artifacts/forward/WUXI_FORWARD_20261013_V0_1/initial_receipt.json"
    assert file_hash(archived) == file_hash(MODULE / "reports/forward_initial_receipt.json")
    proof = fake_native_probes(args.sdk_wheel)
    proof.update(protected_files=len(protected["files"]), automation_unchanged=True,
        original_forward_inception_unchanged=True, saved_output_hashes_checked=len(receipt["outputs"]))
    print(json.dumps(proof, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
