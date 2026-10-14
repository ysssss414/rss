"""Render a bounded, source-backed pilot report from the computed tables."""
from __future__ import annotations

import pandas as pd


def pct(value):
    return "—" if pd.isna(value) else f"{value*100:.2f}%"


def markdown_table(headers, rows):
    return "| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n" + "".join(
        "| " + " | ".join(str(x) for x in row) + " |\n" for row in rows)


def render_report(tables, receipt):
    audit, events, summaries, cohorts, schemes, daily = [tables[name] for name in (
        "coverage_audit.csv", "execution_events.csv", "confirmation_summary.csv", "cohort_comparison.csv",
        "scheme_summary.csv", "daily_signals.csv")]
    conditional = events.loc[events.execution_lane.eq("CONDITIONAL_CACHED_LIMITS")]
    strict = events.loc[events.execution_lane.eq("QUALIFIED_ONLY")]
    baseline = conditional.loc[conditional.confirmation_days.eq(3)]
    text = "# 趋势退出 V0.2 首轮小样本实证报告\n\n"
    text += ("本轮得到人工案例中的因果信号、退出后价格路径及有条件执行对照。严格成交资格仍不足，"
             "不能据此确认退出策略有效，也不能把退出后的上涨假定为能够完美买回。\n\n")
    text += f"- `PILOT_EVIDENCE_AVAILABLE`：四只固定人工案例的描述性证据；严格资格模型成交 {int(strict.execution_price.notna().sum())} 次。\n"
    text += "- `STRATEGY_EFFECTIVENESS_NOT_YET_ESTABLISHED`：没有总体逃顶成功率、最优参数或上线结论。\n"
    text += "- `IMPLEMENTATION_VALIDATED` 的测试验收及交付Git身份另见 `reports/pilot_validation_receipt.json`；本生成器不把完成计算当作测试通过。\n\n"
    text += f"## 数据覆盖审计\n\n登记{len(audit)}只，代码唯一解析{int(audit.resolution_status.eq('RESOLVED').sum())}只，实际原始行情范围逐一列于下表。12个未指定区间的历史登记，本轮按完整冻结快照审计；前三个实证案例也使用该完整区间。工业富联沿用2025年。未事后挑选顶部或截取跌势。\n\n"
    text += markdown_table(["股票", "代码", "实际行情起止", "研究期内有效/日历", "缺口", "起点前无事件预热", "起点MA55", "期内MA55就绪日数", "三日/未来40日输入"], [
        [x.stock_name, x.resolved_code, f"{x.first_available}～{x.last_available}", f"{x.usable_trading_bars}/{x.expected_sessions}", x.missing_reasons or "无",
         x.pre_start_event_free_observations, "足够" if x.ma55_ready_at_first_session else "不足/无数据",
         x.ma55_ready_sessions, f'{"有部分" if x.three_day_input_possible else "无"}/{"有部分" if x.future40_input_possible else "无"}'] for x in audit.itertuples()])
    text += ("\nMA55需55个无歧义的有效价格观察。缺失状态、未验证除权或因子变化会重启预热；不会跨事件用最终复权因子重写历史。完整缺口和首次MA55就绪日期见coverage_audit.csv。起点预热不足的日子不补造指标。\n\n"
             "“有部分”只证明存在可用片段，不表示每个事件均具备未来评价。区间后上市交易日历未提供时记UNKNOWN，药明康德0/0不代表覆盖完成。所有历史证券日缺少真实发布时间和逐日早盘约束资格。换手率不可用；原始成交价与T锚定指标价分别保留。\n\n")
    text += f"快照 `{receipt['source_snapshot_id']}`；manifest SHA256 `{receipt['snapshot_validation']['manifest_sha256']}`。因子来源为快照 `adjustment_factor`（逐证券日期），指纹 `{audit.factor_source_hash.iloc[0]}`。完整bars/status指纹、日期缺口和逐行快照身份见 `coverage_audit.csv`。\n\n"
    text += "## 信号、执行和评价口径\n\n"
    text += ("沿用冻结的 `TREND_EXIT_BASELINE_V0_1`：各T收盘严格低于各自当日MA，连续1/2/3个有效交易日确认，重站归零，已知停牌暂停计数，未知缺口或复权歧义重启。第三日才生成三日确认，MA55优先。指标可读预热，风险状态与计数在研究起点重置。工业富联研究终点后最多40交易日只用于后续评价及按原规则逐日推演的对照延续信号，标记 `OUTCOME_EXTENSION`，不回填2025年信号。\n\n"
             "严格 lane 复用原下一交易日卖出政策，需要合格历史早盘约束。缓存 lane 只做低证据等级的条件诊断：以缓存普通非ST日状态、前收盘、10%/20%率、0.01元tick与上下限相互核对，假设下一非停牌日开盘卖出。它不证明官方历史身份、早盘可得性、盘口流动性或成交量。开盘跌停不假定成交；缺失或除权路径不跳过；不自动重试。委托时刻只是09:15研究模型，实际委托/成交始终为未观察。\n\n")
    text += f"1/2/3日合计{len(strict)}个确认事件；严格 lane 全部未解决，缓存 lane {int(conditional.execution_price.notna().sum())}次条件模型成交、{int(conditional.execution_status.eq('NO_FILL').sum())}次限价不成交、{int(conditional.execution_status.eq('EXECUTION_UNRESOLVED').sum())}次未解决。三日基线含{len(baseline)}个确认事件，MA28 {int(baseline.ma_period.eq(28).sum())}次、MA55 {int(baseline.ma_period.eq(55).sum())}次；两均线同日事件仍分别记录，不算独立持仓交易。\n\n"
    text += ("最近高点为截至信号日可知的60观察最高价；预热不足60时高点回撤为空。执行回撤使用该已知高点与下一日原始开盘价。后续5/10/20/40收益与MAE从执行日后D+1～D+H交易所交易日计算，MAE用后续最低价、最大上涨代理用最高价，排除执行日。跨未验证除权、缺失状态、停牌或右侧不足即不可评价；不以未经确认的原始跨除权收益替代。未成交风险路径另以信号收盘锚定，并显式标记 `unexecuted_*`，不能冒充执行后结果。\n\n")
    text += "## 首次事件时间线（三日基线）\n\n此表按研究区间内时间排序选首个事件，不按未来收益选样；所有事件见 `execution_events.csv`。价格均是未获严格资格的缓存条件模型价。\n\n"
    first = baseline.groupby(["stock_name", "ma_period"], sort=False).head(1)
    text += markdown_table(["股票/均线", "首次跌破", "确认", "条件执行", "开盘价", "执行回撤", "后5日", "后10日", "后20日", "后40日", "40日状态"], [
        [f"{x.stock_name}/MA{x.ma_period}", x.first_break_date, x.confirmation_date, x.execution_date, x.execution_price,
         pct(x.execution_drawdown), pct(x.post_5_return), pct(x.post_10_return), pct(x.post_20_return), pct(x.post_40_return), x.post_40_status] for x in first.itertuples()])
    text += "\n## 1/2/3日确认对照\n\n“重站”指确认后5日内重站对应各T均线的事后代理，并非总体误退出率。不同确认方案的事件集合不同，均值不能直接解释为同一事件的延迟效应；单事件初跌日期和确认期间变化已保留，支持后续按初跌日期配对。\n\n"
    summary = summaries.loc[summaries.execution_lane.eq("CONDITIONAL_CACHED_LIMITS")]
    text += markdown_table(["股票/均线", "天数", "确认数", "5日重站/可评", "初跌到确认均值", "确认回撤均值", "执行回撤均值"], [
        [f"{x.stock_name}/MA{x.ma_period}", x.confirmation_days, x.confirmations,
         f"{x.recross5_count}/{x.recross5_evaluable}", pct(x.mean_confirmation_delay_return),
         pct(x.mean_peak_drawdown_at_confirmation), pct(x.mean_peak_drawdown_at_execution)] for x in summary.itertuples()])
    text += "\n## A/B/C/D同起点事件对照\n\n"
    text += ("每个MA28确认事件假设已有一股趋势仓位，以信号收盘标记共同初始净值1；这不是信号日成交或新增入场。A在MA28后清仓；B在MA28后一次再平衡到剩余25%/50%/75%净值情景、MA55后清仓；C等首次随后MA55确认清仓；D继续持有。MA55更早在同一连续跌破阶段确认的起点不可比，剔除并单列。各事件独立且窗口重叠，不连接为连续账户或假定再入场。退出后继续现金，重新准入候选仅记录，不自动买回。\n\n"
             "B仅在首次模型减仓时满足情景上限，此后价格变化会造成权重漂移；未实现逐日上限维护。无手续费、滑点、资金利息、股息总回报或组合预算。下表最大回撤是共同信号起点之后的收盘净值最大回撤，改善为相对D差额（百分点）；机会成本为 `max(持有收益−方案收益,0)` 的事件均值。它不能与滚动高点到卖出回撤混淆。\n\n")
    scheme = schemes.loc[schemes.confirmation_days.eq(3) & schemes.execution_lane.eq("CONDITIONAL_CACHED_LIMITS") & schemes.horizon.eq(40)]
    text += markdown_table(["股票", "方案/剩余情景", "可评事件", "收益均值", "最大回撤均值", "回撤改善", "相对持有收益差", "期末机会成本均值"], [
        [x.stock_name, f"{x.scheme}/{x.scenario_cap:g}", x.evaluable_cohorts, pct(x.mean_return), pct(x.mean_max_drawdown),
         pct(x.mean_drawdown_improvement), pct(x.mean_return_gap_vs_hold), pct(x.mean_terminal_opportunity_cost)] for x in scheme.itertuples()])
    counts = cohorts.loc[cohorts.confirmation_days.eq(3) & cohorts.execution_lane.eq("CONDITIONAL_CACHED_LIMITS")
        & cohorts.scheme.eq("A_MA28_FULL") & cohorts.horizon.eq(40)].evaluation_status.value_counts().to_dict()
    text += f"\n三日MA28的{sum(counts.values())}个40日对照起点，状态计数：`{counts}`。严格 lane 是否发生合格模型退出需查上文；当前缺早盘资格的事件保留持仓路径，不能把缓存情景回撤改善记到严格结果。完整5/10/20/40及三种确认见 `scheme_summary.csv`、`cohort_comparison.csv`。\n\n"
    text += "## 失败、反例与可用片段\n\n按未来表现排序的下列案例仅用于结果分析，不进入信号选择或参数优化。\n\n"
    evaluable = baseline.loc[baseline.post_40_status.eq("AVAILABLE")]
    examples = pd.concat([evaluable.sort_values("post_40_return", ascending=False).head(3), evaluable.sort_values("post_40_return").head(3)])
    text += markdown_table(["股票/均线", "确认", "条件执行", "40日变化", "40日MAE", "40日最大上涨代理"], [
        [f"{x.stock_name}/MA{x.ma_period}", x.confirmation_date, x.execution_date, pct(x.post_40_return),
         pct(x.post_40_mae), pct(x.post_40_max_upside_proxy)] for x in examples.itertuples()])
    text += ("\n对照表展示退出后路径，三日确认与MA55清仓仍需审视退出后的上行机会成本。评价期可能改变“错误退出”的描述；跨除权路径不可强行评价。研究起点前可能已发生较大下跌，首个确认也不等于首次主跌预警。用户给定的形态描述只作为人工样本来源，没有被代码当成箱体、顶部或趋势标签。箱体自动交易未实现。\n\n")
    recovery = daily.loc[daily.confirmation_days.eq(3) & daily.row_scope.eq("STUDY") & daily.POSITION_RISK_STATE.eq("REENTRY_CANDIDATE")]
    text += markdown_table(["股票", "研究期内重新评估候选日数", "最早候选日"], [
        [name, len(group), group.trade_date.min()] for name, group in recovery.groupby("stock_name", sort=True)])
    text += "\n这是满足已冻结修复条件的候选观察，既非买入授权，也没有假定能以最低价买回。完整每日状态和后续现金路径分别见 `daily_signals.csv` 与 `cohort_daily.csv`。\n\n"
    wuxi = audit.loc[audit.stock_name.eq("药明康德")].iloc[0]
    text += "## 药明康德与数据获取路径\n\n"
    text += f"独立身份 `{wuxi.observation_id}`、初始观察日期{wuxi.observation_start}及初始规则哈希保持冻结。本快照实际最新原始数据为{wuxi.last_available}，观察区间内有效行数{wuxi.usable_trading_bars}，历史发布时间资格缺失，不能把观察前缓存当作前瞻验证。\n\n"
    text += ("2026-10-14交付时16:30上海工作日 heartbeat 为ACTIVE；保存的指令及 `forward.py` 仅发现并验证本地冻结Snapshot，不联网、不刷新，缺新数据安静。可变market_store日期需另行审计；本生成器不读取可变存储。本轮没有尝试SDK登录，不能声称取得新行情的能力已经验证。\n\n"
             "可用路径：已有 `research.data.amazingdata.AmazingDataBridge`/`checked_sdk_bars` 支持显式证券和日期的采集，配合已有SDK状态/因子适配和快照验证，但历史状态、单位、因子比值与发布时间仍需证据。`scripts/update_market_store.py --security` 只限制指定历史修补，新日期 `base_missing` 仍遍历证券主表，因子刷新也可能全市场；因此不得将其当成单股更新命令。本轮未调用任何在线市场数据接口。下一阶段需先批准明确的股票/日期/数据集范围，再解决有界增量路径，生成新的独立已验证快照，禁止覆盖当前快照。历史早盘约束另需逐证券日证据，仓库13个代表证券日的资格不可外推。\n\n")
    text += "## 可复现输出与下一阶段\n\n"
    text += f"规则配置SHA256 `{receipt['risk_config_sha256']}`；pilot配置SHA256 `{receipt['pilot_config_sha256']}`；V0.1提交 `{receipt['v01_release_commit']}`。本次计算Git锚点 `{receipt['git_commit']}`，完整代码与输入输出哈希在 `run_receipt.json`，代码内容哈希负责识别未提交的V0.2实现。\n\n"
    text += "运行 `python -m trend_exit_research.src.pilot`，默认输出 `trend_exit_research/artifacts/pilot_v0_2/`。CSV包括覆盖16行、每日信号、pending/confirmed日志、执行事件、确认摘要、方案摘要和逐日事件净值；最后两项是研究模型，不代表真实交易记录。市场逐日派生产物不提交Git，有界报告和摘要提交。\n\n"
    text += ("进入下一阶段的前置条件：取得有界新数据及可信的历史早盘约束，补充可验证复权事件以减少预热中断；先定义重新准入与失败委托处理，再制定持续仓位上限维护；预先登记独立验证股票/区间并避免根据本轮结果挑选；最后才扩展市场/板块/账户/总风险预算。比例和确认天数保持预先显式情景，不据这四个人工案例选择最优。\n")
    return text
