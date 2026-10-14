"""Offline replay of the real BaoStock smoke receipt; never repeats failed live calls."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

from research.data.cache import SnapshotStore
from research.runs.manifest import canonical_bytes
from .data import DEFAULT_SNAPSHOT, FrozenResearchSnapshot


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "trend_exit_research"
LIVE = MODULE / "artifacts/v0_5_live"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=MODULE / "artifacts/v0_5_replay")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if not out.is_relative_to(MODULE / "artifacts") or not out.name.startswith("v0_5"):
        raise ValueError("Use an independent V0.5 artifacts directory")
    protected = read(MODULE / "artifacts/v0_5_protected_start.json")
    for name, expected in protected["files"].items():
        if file_hash(ROOT/name) != expected:
            raise ValueError(f"PROTECTED_FILE_CHANGED: {name}")
    config = read(MODULE / "config/v0_5.json")
    identity, receipt, smokes = [read(LIVE/name) for name in
        ("acquisition_identity.json", "smoke_receipt.json", "smoke_results.json")]
    if len(smokes) != 2 or any(row["quality"]["valid_bars"] != 0 for row in smokes):
        raise ValueError("Expected both frozen smoke failures; never discard successful raw data")
    if identity["config_sha256"] != file_hash(MODULE / "config/v0_5.json"):
        raise ValueError("REAL_CAPTURE_CONFIG_CHANGED")
    if not (len(receipt["calls"]) == 4 and len(receipt["wire"]) == 4 and
            all(row["method"] == "login" and row["response_bytes"] == 0 for row in receipt["wire"]) and
            all(row["error_code"] == "10002007" for row in receipt["sessions"])):
        raise ValueError("This replay describes the frozen failed smoke; unexpected evidence needs a new report")
    # This branch never promotes absent data to an empty but successful market snapshot.
    with FrozenResearchSnapshot(ROOT / DEFAULT_SNAPSHOT) as original:
        original_validation = dict(original.validation)
    prior_cases = {row["security_id"] if "security_id" in row else row["resolved_code"]: row
        for row in read(MODULE / "reports/v0_4_data_qualification.json")["cases"]}
    rows = []
    for code, (vendor, start, end) in config["authorized"].items():
        attempts = [a for a in receipt["calls"] if a["security_id"] == code]
        old = prior_cases[code]
        rows.append({"security_id": code, "vendor_code": vendor, "stock_name": old["stock_name"],
            "case_id": old["case_id"], "authorized_range": [start, end], "new_raw_rows": 0,
            "new_valid_trading_bars": 0, "new_suspended_rows": 0, "actual_baostock_range": [None, None],
            "raw_data_qualification": "UNQUALIFIED", "continuous_trend_indicators": "UNQUALIFIED",
            "capture_status": "SMOKE_LOGIN_FAILED" if attempts else "NOT_ATTEMPTED_SMOKE_FAILED",
            "full_capture_attempted": False, "sdk_data_api_invocations": 0, "smoke_attempts": len(attempts),
            "case_status": "TARGET_CASE_WINDOW_UNCOVERED", "case_computed": False,
            "legacy_snapshot_actual_data_range": [old["actual_first_date"], old["actual_last_date"]],
            "missingness_denominator": "UNKNOWN_NO_NEW_CALENDAR;ZERO_ROWS_IS_NOT_PROOF_OF_NO_TRADING",
            "retrieved_at": None, "strict_pit_execution": "UNQUALIFIED"})
        if code == "603259.SH":
            rows[-1].update(observation_id=config["observation_id"], original_inception_unchanged=True,
                backfill_rows=0, backfill_retrieved_at=None, prospective_validation=False,
                required_late_capture_label="LATE_CAPTURE_NOT_SAME_DAY_PROSPECTIVE")
    raw_files = {p.relative_to(ROOT).as_posix(): file_hash(p) for p in sorted(LIVE.rglob("*.json"))}
    result = {"version": config["version"], "live_acquisition": "STOP", "failure_reason": "BAOSTOCK_LOGIN_RECEIVE_TIMEOUT",
        "cases": rows, "new_snapshots": [], "sdk_version": identity["installed_baostock_version"],
        "wheel_sha256": identity["wheel_sha256"], "config_sha256": identity["config_sha256"],
        "real_capture_identity": identity, "original_snapshot_validation": original_validation,
        "real_request_attempts": 4, "actual_history_api_invocations": 0, "actual_calendar_api_invocations": 0,
        "tcp_connections_established": 4, "wire_login_requests": 4, "wire_data_requests": 0,
        "wire_response_bytes": 0, "sdk_error_code": "10002007", "elapsed_seconds": receipt["elapsed_seconds"],
        "frozen_evidence_files": raw_files, "protected_files_checked": len(protected["files"]),
        "capture_sources_have_later_guard_updates": {name: file_hash(ROOT/name) != expected
            for name, expected in identity["executed_capture_source_sha256"].items()},
        "current_source_sha256": {p.relative_to(ROOT).as_posix(): file_hash(p) for p in sorted((MODULE / "src").glob("*v0_5.py"))},
        "strategy_effectiveness": "STRATEGY_EFFECTIVENESS_NOT_YET_ESTABLISHED"}
    first = "# TREND_EXIT_V0_5 BaoStock真实采集\n\n**三只股票均未取得真实历史日线。** 昆仑0条、五粮液0条、药明0条。药明与昆仑真实Smoke各进行初始尝试和一次失败重试，均在登录响应阶段失败；五粮液完整采集按任务第4节未启动。结果不能解释为股票无行情。\n\n"
    table = "| 股票 | 实际Smoke尝试 | 实际日线API调用 | 新行情 | 结果 |\n| --- | --- | --- | --- | --- |\n"
    for row in rows:
        table += f"| {row['stock_name']} {row['security_id']} | {row['smoke_attempts']} | 0 | 0 | {row['capture_status']} |\n"
    acquisition = first + table + f"\n4次TCP连接至public-api.baostock.com:10030均成功；4个login报文均已发送，每次等待15秒且响应字节为0，BaoStock返回10002007（网络接收错误）。受控尝试总耗时{receipt['elapsed_seconds']}秒。实际login调用4，history API调用0、calendar API调用0、数据分页请求0、logout API调用0（没有成功会话）。完整采集/日历没有进入。本轮未追加重试，没有获取全市场、因子或其它证券日期。供应商服务故障、线路限制、协议端兼容性等原因无法仅据这些回执确定，未证明BaoStock没有这些股票数据。\n\n"
    acquisition += "## 来源、环境与请求约束\n\n使用[官方PyPI BaoStock 0.9.4](https://pypi.org/project/baostock/0.9.4/)，在研究artifacts/v0_5_env安装；venv只新增baostock，复用基础环境只读pandas 3.0.1，没有升级RSS依赖。包SHA256为 `" + identity["wheel_sha256"] + "`。实际Python、包成员、执行时源码和配置身份见data qualification JSON。\n\n"
    acquisition += "检查安装包的history、resultset、trade dates和socket/login实现：数据请求在发送前包含指定code、fields、start/end、frequency=d、adjustflag=3；翻页保留这些参数，SDK未见自动查询重试。只允许三证券各自授权日期及四个必要API。上限为16次高层尝试、每请求2尝试、每尝试3页/1200行、数据报文48次、每socket 15秒、每查询90秒、整次900秒、每响应8MiB；不会要求证明第三方服务器内部实现。SDK原recv在peer关闭时可空读循环，包装显式拒绝空读并加超时。安装包实际序列化还以纯内存sink验证，没有额外联网。\n\n"
    acquisition += "供应商[API文档](https://github.com/zxygithub/baostock/blob/master/docs/pythonAPI.md)声明价格/amount为CNY、volume为股、turn为%、tradestatus标记交易/停牌。它们是供应商口径声明，未取得真实行作此次单位校验。停牌保留参考价和零量额也不能成为交易观察；潜在preclose/前日raw_close差异只标CORPORATE_ACTION_EVIDENCE_REQUIRED，不推断具体行动或伪造因子。未验证事件不跨段计算。\n\n"
    acquisition += "## 实证与Snapshot结果\n\n真实有效行0，没有新Snapshot/manifest，不创建空成功Snapshot。因未过Smoke，没有连续MA、三日确认、潜在执行价、未来5/10/20/40走势或重新准入结果。无法做跨源边界核验；原AmazingData证据保留，没有额外下载2024年重叠数据。已有V0.4 raw Snapshot契约的供应商字段保留、哈希重放和迟到标记在聚焦测试中检查，合成对象不能充当真实采集。\n\n"
    acquisition += "药明原WUXI_FORWARD_20261013_V0_1及初始回执未改。仍只有原缓存截至2026-09-23，backfill_retrieved_at=null。连接尝试的检索时间不等同于捕获了10月13日行情，不声称之后的前瞻验证；16:30自动检查仍只读缓存。TGW阻断和V0.1—V0.4冻结结果保持不变。\n\n"
    acquisition += "真实证据见artifacts/v0_5_live：smoke_receipt.json、smoke_results.json、4份attempts、4份wire响应记录、acquisition_identity.json。执行后仅增加了防止已结束采集重跑、每请求不可重复、分页单调性和错误日志的保护；未用更新代码重跑网络。JSON同时保留执行时和最终代码哈希。\n\n"
    acquisition += "下一阶段需先定位此执行主机到BaoStock公共端口的登录回包问题；若要重试本轮已耗尽的同一Smoke请求，需明确新的尝试授权。通信恢复且至少一组真实Smoke通过后，再串行取得3个完整日线及缺少的有界calendar，成功者以V0.4契约独立冻结。不能用其它股票、全集请求或无限重试绕过当前失败。测试、Git范围及旧成果校验见reports/v0_5_validation_receipt.json；最小离线回放命令为 `python -m trend_exit_research.src.v0_5`。\n\n"
    acquisition += "判定：BAOSTOCK_LIVE_ACQUISITION_STOP；RAW_DATA_UNQUALIFIED；CONTINUOUS_TREND_INDICATORS_UNQUALIFIED；CASE_ALIGNMENT_PARTIAL；STRATEGY_EFFECTIVENESS_NOT_YET_ESTABLISHED。\n"
    cases = "# TREND_EXIT_V0_5 原案例恢复结果\n\n三股真实新增日线均为0；昆仑2023和五粮液2020—2021均未恢复。以下均为缺失状态，不能视为没有风险事件。\n\n"
    cases += "| 案例 | 目标区间 | 状态 | 风险时间线 |\n| --- | --- | --- | --- |\n"
    for row in rows:
        old = prior_cases[row["security_id"]]
        cases += f"| {row['case_id']} | {old['study_start']}～{old['study_end']} | TARGET_CASE_WINDOW_UNCOVERED | NOT_COMPUTED |\n"
    cases += "\n当日可知高点、MA28/MA55首次及三日确认、两者交易日间隔、条件退出回撤、后5/10/20/40路径和趋势恢复候选均保持null/NOT_COMPUTED，没有用2024年后行情替代。严格PIT/执行资格未取得，无模拟成交或交易收益。\n\n药明初始身份、2026-10-13冻结记录及实际数据截至2026-09-23保留；补取0行、无迟到行情归档、无当天或后续前瞻成功。\n\nCASE_ALIGNMENT_PARTIAL；STRATEGY_EFFECTIVENESS_NOT_YET_ESTABLISHED。\n"
    outputs = {"v0_5_baostock_acquisition_report.md": acquisition.encode("utf-8"),
        "v0_5_data_qualification.json": canonical_bytes(result), "v0_5_case_results.md": cases.encode("utf-8")}
    for name, content in outputs.items():
        SnapshotStore(out)._atomic_write(out/name, content, immutable=True)
    print(json.dumps({"real_rows": {r["security_id"]: 0 for r in rows}, "live_acquisition": "STOP",
        "sdk_login_calls": 4, "sdk_data_calls": 0, "new_snapshots": 0, "protected": len(protected["files"])}))


if __name__ == "__main__":
    main()
