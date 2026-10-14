# TREND_EXIT_V0_3

本版本保留V0.1/V0.2原始登记、配置、代码、报告、哈希与本地结果。新入口只做明确目标案例、实验趋势资格和持仓观察区间关联，未实现入场成交、真实持仓数量、资金收益、自动重新入场或全市场计算。

目标登记在 `inputs/target_cases_v0_3.csv`：16股、17个case（兆易2026与2025年11月分开）。预热、目标研究、后续评价分别指定；目标未覆盖输出 `TARGET_CASE_WINDOW_UNCOVERED`，不选择另一走势替代。9只未明确目标的股票保留未登记状态。当前只计算完整覆盖的两个兆易case和工业富联2025，昆仑2023/五粮液2020—2021/药明前瞻初始区间均有缺口。

配置 `config/v0_3.json` 在首次计算前登记实验资格阈值，未寻优。TREND_ELIGIBLE只是计算资格，不是入场或持仓证明；风险状态TREND不能替代资格。首次资格建立收盘参考观察区间，未记录买入。每case/确认天数/执行等级最多一个观察区间，MA28最多一个降仓意图，MA55目标零意图，退出后只记录候选不自动买回。MA28目标上限未知保持null，所有真实成本/数量/浮盈/利润回吐/交易收益保持空值。

严格历史早盘约束与缓存开盘价格模型分列。即使得到合格synthetic模型价，也不意味着没有入场证据的观察区间发生真实持仓交易。未知缺口或未验证除权使观察价格路径失效，不能跨事件推算利润。未来结果只出现在独立outcomes；逐日资格/持仓状态不包含未来收益字段。

在仓库根目录，Windows当前已验证运行环境：

```powershell
$env:PYTHONPATH = (Resolve-Path .cache/trend_exit_runtime).Path
$env:PYTHONIOENCODING = 'utf-8'
$rssResearchPython = 'C:/Users/cjzs0/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $rssResearchPython -m trend_exit_research.src.v0_3
& $rssResearchPython trend_exit_research/tests/verify_v0_3_outputs.py
& $rssResearchPython -m pytest trend_exit_research/tests tests/test_factor_pit.py tests/test_trade_lifecycle.py tests/test_research_cache.py tests/test_d8_snapshot.py tests/test_stage1_foundation.py -q --basetemp=.test_tmp/trend_exit_v03_local
```

独立环境需已有pandas、duckdb、pytest及原RSS代码；无需新第三方依赖。计算不导入在线fetcher，不登录SDK。可用 `--out trend_exit_research/artifacts/v0_3_repeat`重复计算，默认输出 `artifacts/v0_3/`；不会写入或覆盖旧版本输出目录。

九张CSV：case_coverage、daily_trend_risk、raw_risk_signal_log、raw_risk_events、daily_holding_associations、event_holding_links、holding_action_outcomes、holding_intervals、paired_waiting_cost。另有生成报告 `trend_exit_v0_3_report.md` 和run_receipt.json。CSV明确使用case/holding/version/config/snapshot身份连接，不能把重叠事件当独立持仓交易。日终历史发布时间仍未证明，计算上的无未来函数不等于严格历史数据准入。

可审阅的有界采集方案在 `reports/bounded_acquisition_plan_v0_3.md/json`。实际在线采集等待明确授权；因子无日期参数、全市场新日期updater以及不可验证SDK初始化/内部扇出路径停止。新数据需独立不可变、校验过的Snapshot，不覆盖原V1，也不将补取药明数据伪装为当日捕获。原16:30本地观察任务不变、没有新联网权限。

交付结论分开：工程测试；目标案例对齐（当前部分覆盖）；观察持仓语义（可验证）；真实持仓交易语义（缺入场/数量/执行资格）；策略有效性（尚未建立）。
