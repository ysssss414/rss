"""Offline target-case and holding-association research, never entry execution."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess

import pandas as pd

from .cases_v0_3 import audit_case, read_cases
from .cli import MODULE, ROOT
from .data import DEFAULT_SNAPSHOT, FrozenResearchSnapshot, stock_frame
from .holding_v0_3 import associate_holdings
from .indicators import calculate, config_hash, load_config
from .pilot import event_table, LANES
from .qualification_v0_3 import qualify
from .risk import apply_risk, load_risk_config
from .samples import read_samples


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def load_research_config(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    q, life = value["qualification"], value["lifecycle"]
    if value["version"] != "TREND_EXIT_V0_3" or q["version"] != "TREND_QUALIFICATION_EXPERIMENT_V0_3":
        raise ValueError("Explicit V0.3 experiment identity required")
    if life["version"] != "TREND_HOLDING_ASSOCIATION_V0_3" or life["mode"] != "FIRST_ELIGIBLE_OBSERVATION_NO_ENTRY_FILL":
        raise ValueError("This version supports observation associations only")
    if life["ma28_position_cap"] is not None or life["automatic_reentry"] or life["automatic_retry"]:
        raise ValueError("V0.3 has no authorized sizing, automatic reentry, or retry")
    if value["confirmation_days"] != [1, 2, 3] or value["horizons"] != [5, 10, 20, 40]:
        raise ValueError("Predeclared confirmation and evaluation scenarios required")
    if type(q["min_observations"]) is not int or q["min_observations"] != 60 or type(q["runup_window"]) is not int or not 1 <= q["runup_window"] < 60:
        raise ValueError("Require 60-observation history and causal runup window")
    for key in ("min_runup", "min_ma28_slope", "min_ma55_slope", "min_ma28_over_ma55", "max_close_over_ma55", "max_distance_below_peak"):
        if type(q[key]) not in (float, int) or not 0 <= q[key] < 1:
            raise ValueError("Explicit finite fractional threshold required")
    return value


def paired_waiting_cost(events):
    columns = ["case_id", "security_id", "ma_period", "first_break_date"]
    result = []
    if events.empty:
        return pd.DataFrame(columns=columns)
    for identity, group in events.loc[events.execution_lane.eq("CONDITIONAL_CACHED_LIMITS")].groupby(columns, sort=True):
        row = dict(zip(columns, identity))
        prices = {}
        for n in (1, 2, 3):
            selected = group.loc[group.confirmation_days.eq(n)]
            row[f"confirmation_{n}"] = selected.confirmation_date.iloc[0] if len(selected) else "NOT_CONFIRMED_OR_CENSORED"
            row[f"execution_{n}"] = selected.execution_date.iloc[0] if len(selected) else None
            prices[n] = selected.execution_price.iloc[0] if len(selected) else None
            row[f"conditional_price_{n}"] = prices[n]
        row["execution_price_change_1_to_3"] = prices[3]/prices[1]-1 if pd.notna(prices[1]) and pd.notna(prices[3]) else None
        row["false_break_interpretation"] = "UNCONFIRMED_IS_NOT_PROOF_OF_FALSE_BREAK"
        result.append(row)
    return pd.DataFrame(result)


def render_report(tables, receipt):
    audit, risk, diagnostics, actions, holdings = [tables[k] for k in (
        "case_coverage.csv", "daily_trend_risk.csv", "event_holding_links.csv", "holding_action_outcomes.csv", "holding_intervals.csv")]
    def table(headers, rows):
        return "| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n" + "".join(
            "| " + " | ".join("—" if pd.isna(x) else str(x) for x in row) + " |\n" for row in rows)
    text = "# TREND_EXIT_V0_3：样本区间与主升持仓语义\n\n"
    text += "本轮只验证目标案例对齐、实验趋势资格与持仓观察区间的因果关联。没有入场成交证据、持仓成本或数量，真实浮盈、利润回吐和收益保持空值。缓存价格模型也不构成真实持仓交易。\n\n"
    text += "## 1. 原始案例与数据是否正确对齐\n\n"
    text += table(["case_id", "股票", "目标研究区间", "状态", "预热覆盖", "评价覆盖", "实际计算"], [
        [x.case_id, x.stock_name, f"{x.study_start or '待登记'}～{x.study_end or '待登记'}", x.target_status,
         x.warmup_window_covered, x.evaluation_window_covered, x.computed] for x in audit.itertuples()])
    text += "\n原16股名单不变，兆易创新拆成两个独立case，合计17行；9个未指定走势的案例保持未登记，未自动采用其它区间。昆仑万维2023、五粮液2020—2021超出快照，明确为TARGET_CASE_WINDOW_UNCOVERED。332.23只是用户提供的事后分析参考，不进入信号。兆易创新2026区间经用户确认截至2026-09-23，之后没有验证；2025年11月反例单独登记，不能补造此前已有持仓。工业富联保留2025。预热、研究、评价区间分别记录，评价日不新增持仓观察区间或动作意图。\n\n"
    text += "## 2. 哪些确认发生于合格主升观察区间\n\n"
    text += "实验资格需要60个有效观察，MA28和MA55五观察斜率严格为正、MA28高于MA55、近20观察上涨至少15%、收盘不低于MA28、相对MA55不超过30%、距离当时60观察高点不超过10%。参数在任何V0.3行情计算前登记，不做优化，也不声称预测能力。TREND_ELIGIBLE、TREND_NOT_QUALIFIED、TREND_DATA_UNQUALIFIED与原始POSITION_RISK_STATE分列；默认TREND不证明主升，更不授权入场。历史15:30可用时刻仍是假设。\n\n"
    summary = []
    if not risk.empty:
        for identity, group in risk.loc[risk.confirmation_days.eq(3) & risk.row_scope.eq("STUDY")].groupby("case_id", sort=True):
            summary.append([identity, int(group.trend_qualification.eq("TREND_ELIGIBLE").sum()),
                int(group.trend_qualification.eq("TREND_NOT_QUALIFIED").sum()), int(group.trend_qualification.eq("TREND_DATA_UNQUALIFIED").sum())])
    text += table(["case", "主升资格日", "未达资格日", "数据不足日"], summary)
    text += "\nB组仅将研究区间内首次资格日设为收盘参考标记，不生成买入或默认满仓。每个case/确认天数/执行等级最多一个观察区间；同股票新case不继承旧区间。退出后只记录重新评估候选，不能自动恢复持仓。已知停牌暂停，未知缺口/未验证复权使持仓价格路径失效，不能事后桥接。\n\n"
    selected = holdings.loc[holdings.confirmation_days.eq(3)] if not holdings.empty else holdings
    text += table(["case", "执行等级", "观察起点", "MA28意图", "MA55意图", "观察模型退出", "状态", "重新评估候选", "退出后延迟交易日"], [
        [x.case_id, x.execution_lane, x.association_start, x.ma28_intent_date, x.ma55_intent_date,
         x.observation_exit_date, x.terminal_state, x.first_reentry_candidate, x.reentry_delay_from_observation_exit] for x in selected.itertuples()])
    text += "\n## 3. 无持仓前提的重复跌破事件\n\n"
    links = diagnostics.loc[diagnostics.confirmation_days.eq(3) & diagnostics.execution_lane.eq("QUALIFIED_ONLY")] if not diagnostics.empty else diagnostics
    counts = []
    if not links.empty:
        for (case_id, reason), group in links.groupby(["case_id", "reason"], sort=True):
            counts.append([case_id, reason, len(group)])
    text += table(["case", "事件归类", "确认事件数（每均线分列）"], counts)
    text += "\nA组raw_risk_events保留所有确认、每均线初跌日期、1/2/3确认、等待成本、未来5/10/20/40价格路径、五日重站均线代理；它们是诊断事件，不能计为多笔持仓卖出。B组event_holding_links标记无持仓、重复MA28、清仓意图后重复跌破、数据失效以及MA55覆盖MA28。计数不外推全部A股，也不构成逃顶成功率。\n\n"
    text += "## 4. MA28与MA55在哪个阶段作用\n\n"
    text += "有资格观察区间才关联退出意图；MA28三日确认最多一次降仓意图，仓位上限未知保持null，无法计算减仓数量或宣称减仓成交。MA55三日确认使目标为0，进入EXIT_REQUIRED；同日覆盖MA28。严格早盘约束缺失则执行未解决且真实暴露未知；缓存lane仅以T+1可交易开盘价作观察退出模型，状态直到该日才更新。全部actual_sell_generated为false，modelled_position_trade为false，不能将观察退出代理升级为合格持仓成交。\n\n"
    outcome_rows = []
    baseline_actions = actions.loc[actions.confirmation_days.eq(3)] if not actions.empty else actions
    for x in baseline_actions.itertuples():
        value = getattr(x, "price_giveback_from_known_peak", None)
        outcome_rows.append([x.case_id, x.execution_lane, x.action, x.trade_date, x.execution_date,
            x.execution_status, f"{value*100:.2f}%" if pd.notna(value) else None])
    text += table(["case", "等级", "关联意图", "确认", "潜在执行日", "价格执行模型", "已知区间高点到潜在执行价"], outcome_rows)
    def percent(value):
        return f"{value*100:.2f}%" if pd.notna(value) else None
    text += "\n执行后价格路径仅作事后评价；严格未成交没有执行锚点，下表保持空值。所有5/10/20/40日路径及信号锚定的未成交暴露见holding_action_outcomes.csv。\n\n"
    text += table(["case/动作", "等级", "后5日", "后10日", "后20日", "后40日", "40日MAE", "40日期末上涨代理", "40日状态"], [
        [f"{x.case_id}/{x.action}", x.execution_lane, *[percent(getattr(x,f"post_{h}_return",None)) for h in (5,10,20,40)],
         percent(getattr(x,"post_40_mae",None)), percent(getattr(x,"post_40_terminal_opportunity_proxy",None)),
         x.post_40_status] for x in baseline_actions.itertuples()])
    text += "\n价格代理从观察起点收盘参考与此后当日可知高价计算，起点之前的高点不能计为持仓浮盈。高点到下一开盘的差额只用确认日已知高点，禁止纳入执行日稍后高价；真实最大浮盈和利润回吐因成本/数量缺失为空。未知减仓比例不影响价格路径代理，但不能解释为剩余100%风险仓位。未成交暴露用信号收盘锚定的unexecuted_*另记，退出后上涨、MAE、机会成本代理仅在outcomes，不能参与资格或风险判断。\n\n"
    text += "## 5. 仍缺少哪些数据和规则\n\n"
    text += "需补齐2023昆仑、2020—2021五粮液原始行情/交易日历/历史状态/复权事件与因子证据，药明9月24日至10月13日增量，以及可信的历史早盘约束。还需真实或明确授权的入场依据、成本、数量、风险预算和MA28目标上限，明确失败委托处置与重新入场规则，才能计算真正仓位管理收益。本轮未实现自动箱体交易、连续账户回测或参数优化。\n\n"
    text += "有界采集方案见 reports/bounded_acquisition_plan_v0_3.md/json。实际在线采集等待明确授权；底层不能保证证券/日期范围的接口停止。现有update_market_store新日期可全市场扇出、calendar helper过滤掉2024年前日期、因子endpoint无日期参数、旧freeze CLI写死2026-09-23，均不能直接用来补齐新案例。未来只允许独立不可变快照，旧Snapshot和前瞻初始身份保持不变。补取的药明历史必须标记LATE_CAPTURE_NOT_SAME_DAY_PROSPECTIVE。\n\n"
    text += f"研究版本{receipt['version']}，配置SHA256 `{receipt['config_sha256']}`，案例登记SHA256 `{receipt['cases_sha256']}`；基准配置SHA256 `{receipt['baseline_config_sha256']}`；快照manifest `{receipt['snapshot_validation']['manifest_sha256']}`。代码、输入、输出身份见run_receipt.json；测试及交付身份另见v0_3_validation_receipt.json。\n\n"
    text += "判定：工程测试以独立验证回执为准；CASE_ALIGNMENT_PARTIAL；HOLDING_SEMANTICS仅验证因果观察关联，真实入场/仓位交易未验证；STRATEGY_EFFECTIVENESS_NOT_YET_ESTABLISHED。\n"
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline V0.3 explicit cases and holding associations")
    parser.add_argument("--snapshot", type=Path, default=ROOT / DEFAULT_SNAPSHOT)
    parser.add_argument("--out", type=Path, default=MODULE / "artifacts/v0_3")
    args = parser.parse_args(argv)
    if not args.out.resolve().is_relative_to((MODULE / "artifacts").resolve()):
        parser.error("V0.3 outputs must remain inside module artifacts")
    config = load_research_config(MODULE / "config/v0_3.json")
    baseline = load_risk_config(MODULE / "config/risk_baseline.json")
    candidates = load_config(MODULE / "config/default.json")
    if config_hash(baseline) != "f1826b0b4df15afcc03108cc9f9bf91af48e21a5aa58f47752b1ed8af55263fa":
        raise ValueError("Frozen baseline changed")
    audit, risk_tables, raw_logs, event_tables, daily_holding, links, actions, holdings = [], [], [], [], [], [], [], []
    with FrozenResearchSnapshot(args.snapshot) as snapshot:
        master = snapshot.load_universe()
        samples = read_samples(MODULE / "inputs/manual_samples.csv", master)
        cases = read_cases(MODULE / "inputs/target_cases_v0_3.csv", samples)
        frames = {}
        for case in cases:
            code = case["resolved_code"]
            if code and code not in frames:
                frames[code] = stock_frame(snapshot, case, master)
            frame = frames.get(code, pd.DataFrame())
            record = audit_case(case, frame, snapshot)
            audit.append(record)
            if not case["compute"] or record["target_status"] != "TARGET_CASE_WINDOW_COVERED":
                continue
            selected = frame.loc[frame.trade_date.between(case["warmup_start"], case["evaluation_end"])].reset_index(drop=True)
            features, _ = calculate(selected, candidates)
            features = qualify(features, config["qualification"])
            features = features.loc[features.trade_date.ge(case["study_start"])].reset_index(drop=True)
            for days in config["confirmation_days"]:
                risk, log = apply_risk(features, baseline, confirmation_days=days)
                for table in (risk, log):
                    table["case_id"], table["stock_name"] = case["case_id"], case["stock_name"]
                    table["row_scope"] = table.trade_date.map(lambda d: "STUDY" if d <= case["study_end"] else "EVALUATION_ONLY")
                    table["research_version"], table["research_config_sha256"] = config["version"], config_hash(config)
                risk_tables.append(risk)
                raw_logs.append(log)
                events = event_table(risk, selected, dict(stock_name=case["stock_name"], sample_id=case["case_id"]), config)
                if not events.empty:
                    events = events.loc[events.confirmation_date.le(case["study_end"])].copy()
                    events["case_id"], events["research_version"] = case["case_id"], config["version"]
                    events["actual_trade_generated"] = False
                event_tables.append(events)
                for lane in LANES:
                    d, l, a, h = associate_holdings(risk, selected, case, config, lane=lane)
                    daily_holding.append(d); links.append(l); actions.append(a); holdings.append(h)
            record["computed"] = True
            print(json.dumps(dict(case_id=case["case_id"], status="COMPUTED_WITH_QUALIFICATION_LIMITATIONS")), flush=True)
        def concatenate(tables):
            nonempty = [x for x in tables if not x.empty]
            return pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame(columns=["case_id", "security_id", "trade_date"])
        tables = {"case_coverage.csv": pd.DataFrame(audit), "daily_trend_risk.csv": concatenate(risk_tables),
            "raw_risk_signal_log.csv": concatenate(raw_logs), "raw_risk_events.csv": concatenate(event_tables),
            "daily_holding_associations.csv": concatenate(daily_holding), "event_holding_links.csv": concatenate(links),
            "holding_action_outcomes.csv": concatenate(actions), "holding_intervals.csv": concatenate(holdings)}
        tables["paired_waiting_cost.csv"] = paired_waiting_cost(tables["raw_risk_events.csv"])
        args.out.mkdir(parents=True, exist_ok=True)
        inventory = {}
        for name, table in tables.items():
            table.to_csv(args.out / name, index=False, encoding="utf-8-sig", float_format="%.12g", lineterminator="\n")
            inventory[name] = dict(rows=len(table), sha256=digest(args.out / name))
        receipt = dict(version=config["version"], config=config, config_sha256=config_hash(config),
            cases_sha256=digest(MODULE / "inputs/target_cases_v0_3.csv"), baseline_config_sha256=config_hash(baseline),
            candidate_config_sha256=config_hash(candidates),
            samples_sha256=digest(MODULE / "inputs/manual_samples.csv"), source_snapshot_id=snapshot.snapshot_id,
            snapshot_validation=snapshot.validation, python=platform.python_version(), pandas=pd.__version__,
            git_anchor=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            code_sha256={p.relative_to(ROOT).as_posix():digest(p) for p in sorted((MODULE / "src").glob("*.py"))},
            reused_code_sha256={p:digest(ROOT/p) for p in ("research/snapshot.py", "research/data/adjustment.py",
                "research/factor_pit.py", "research/foundation.py", "research/real_materialization.py")},
            outputs=inventory, no_download=True, no_actual_orders=True, actual_entry_evidence="ABSENT",
            no_actual_portfolio_pnl=True, no_automatic_reentry=True, prior_release="6ec68fcb9d81127972185a7a35a19d5b8866aeba",
            horizon_definition="POST_EXECUTION_D+1_TO_D+H_EXCHANGE_SESSIONS;EVENT_FREE_RAW_PRICE_PROXY",
            interpretation="MANUAL_CASES;OBSERVATION_ASSOCIATIONS_ARE_NOT_ACTUAL_HOLDINGS")
        path = args.out / "trend_exit_v0_3_report.md"
        path.write_text(render_report(tables, receipt), encoding="utf-8")
        inventory[path.name] = dict(sha256=digest(path))
        (args.out / "run_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(dict(status="V0_3_OFFLINE_COMPLETE", output=str(args.out.resolve()), outputs=inventory), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
