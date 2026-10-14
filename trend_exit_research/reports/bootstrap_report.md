# RSS 趋势退出与风险预算 V0.1 验收回执

**PASS_WITH_LIMITATIONS**。基础设施、冻结规则的因果计数、风险状态、独立评价、前瞻身份和重复运行验证通过。**退出有效性及组合收益未验证**；人工案例不能推出总体逃顶成功率。

## 仓库与变更边界

- 仓库：`D:/ej/材料/codex/rss`，remote `https://github.com/ysssss414/rss.git`。
- 起始分支：`codex/rss-stage1-market-style-theme-context`；工作分支：`codex/trend-exit-research-v0.1`。
- 起始及交付 HEAD：`915b8f86a82fffe4ffd6f15d827df2d6200e0a1b`。新模块为未提交工作成果。
- 新增 Git 范围仅 `trend_exit_research/`；没有改动 RSS Stage 1 策略、生命周期、冻结结论、原数据模块、根配置或现有测试。原有三项用户 Excel 工作区状态与文件字节均保留，SHA256 见 `validation_receipt.json`。
- 没有行情下载或缓存刷新；真实指标仍只计算昆仑万维与光迅科技，其余股票只检查覆盖。药明康德目前只冻结身份与数据缺口，不计算初始观察日前的历史信号作为前瞻结果。

## 文件与复用

可提交文件：

```text
trend_exit_research/
  .gitignore
  README.md
  __init__.py
  config/default.json
  config/risk_baseline.json
  inputs/manual_samples.csv
  src/__init__.py
  src/samples.py
  src/data.py
  src/indicators.py
  src/risk.py
  src/evaluation.py
  src/forward.py
  src/synthetic.py
  src/cli.py
  tests/test_research.py
  tests/test_risk_budget.py
  reports/bootstrap_report.md
  reports/sample_coverage.csv
  reports/real_run_receipt.json
  reports/synthetic_run_receipt.json
  reports/validation_receipt.json
  reports/risk_summary.csv
  reports/confirmation_comparison.csv
  reports/forward_initial_receipt.json
  reports/automation_receipt.json
  artifacts/.gitkeep
```

`artifacts/` 计算、验收脚本、JUnit、逐日行情派生文件及前瞻归档全部忽略；仅 `.gitkeep` 可提交。依赖及测试临时文件留在原有忽略目录 `.cache/`、`.test_tmp/`。

复用 `FrozenResearchSnapshot` 的身份/组成文件 SHA256/行数核查与离线主数据、日历、OHLC、状态、因子加载；复用 `bar_validation_status` 检查价格和成交量额；复用 `adjusted_bars` 每个 T 按 `factor(d)/factor(T)` 重建同一锚点历史；复用 `FactorRatioSnapshot` 验证完整事件证据的合成复权路径；复用 `foundation.NEXT_SESSION_SELL_V1` 验证独立研究的卖出条件，没有调用 Stage 1 生命周期或改变其契约。

真实因子缺少逐证券实施/修订证据，因此事件、因子变化、除权标记会重置历史并重新预热。真实数据的 15:30 上海可用时点是假设，非历史发布时间证明。没有把 D8 结果表送入信号或风险状态。

## 样本与覆盖

16 个名称均由冻结证券主数据唯一解析。原 12 个区间仍空，覆盖检查使用 Snapshot 区间 `2024-01-02～2026-09-23`（662 日）；新增三只历史案例固定 2025 年（243 日）；药明康德独立前瞻起点为 2026-10-13。

| 股票 | 代码 | 指定/覆盖区间 | 有效必需数据 | 缺失说明 |
| --- | --- | --- | ---: | --- |
| 昆仑万维 | 300418.SZ | Snapshot 全区间 | 662/662 | 无 |
| 光迅科技 | 002281.SZ | Snapshot 全区间 | 662/662 | 无 |
| 东山精密 | 002384.SZ | Snapshot 全区间 | 662/662 | 无 |
| 佰维存储 | 688525.SH | Snapshot 全区间 | 662/662 | 无 |
| 中际旭创 | 300308.SZ | Snapshot 全区间 | 662/662 | 无 |
| 新易盛 | 300502.SZ | Snapshot 全区间 | 661/662 | 2026-07-10 状态缺失，99.85% |
| 五粮液 | 000858.SZ | Snapshot 全区间 | 662/662 | 无 |
| 爱尔眼科 | 300015.SZ | Snapshot 全区间 | 662/662 | 无 |
| 源杰科技 | 688498.SH | Snapshot 全区间 | 662/662 | 无 |
| 生益科技 | 600183.SH | Snapshot 全区间 | 662/662 | 无 |
| 长川科技 | 300604.SZ | Snapshot 全区间 | 661/662 | 2026-07-10 状态缺失，99.85% |
| 兆易创新 | 603986.SH | Snapshot 全区间 | 662/662 | 无 |
| 三花智控 | 002050.SZ | 2025-01-01～2025-12-31 | 243/243 | 历史数据可向前预热 |
| 卧龙电驱 | 600580.SH | 2025-01-01～2025-12-31 | 243/243 | 历史数据可向前预热 |
| 工业富联 | 601138.SH | 2025-01-01～2025-12-31 | 243/243 | 历史数据可向前预热 |
| 药明康德 | 603259.SH | 自 2026-10-13 前瞻 | 0 日前瞻数据 | 实际最新 2026-09-23，等待新数据 |

全部换手率字段缺失，保持空值。Snapshot 为 `REAL_RESEARCH_SNAPSHOT_V1`，manifest SHA256 `a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67`，104 个组成文件校验通过。

## 已实现规则与因果性

`TREND_EXIT_BASELINE_V0_1` 固定严格 `Close < MA`、零缓冲、三日确认。每日均线来自当天及此前同一 T 锚点的数据。第三个有效收盘才确认；收盘等于或超过均线归零。已知停牌暂停基线有效观察计数，不虚构收盘；未知/缺失/复权歧义重置。原 A/B/C/D 保留其独立候选版本，不能与三日基线混称。

真实确认过程示例：昆仑万维 MA28 从 2024-03-26 连续跌破，第三日 03-28 才确认；MA55 从 2024-04-18 跌破，第三个有效收盘 04-22 才确认。完整计数、当天 Close/MA、初次跌破日期、确认原因及规则/快照身份可在逐日表及风险日志中核对。合成和前缀测试覆盖中途站回、同日双确认、停牌暂停和缺口重置，不回填首次跌破日。

六种 `POSITION_RISK_STATE` 已实现。MA55 pending 禁止加仓；MA55 confirmed 趋势目标为 0，同日优先于 MA28 减仓。修复需连续三日站回两条均线且 5 日斜率非负；退出后的恢复只进入 REENTRY_CANDIDATE，目标仍为 0，不自动买入。数据未就绪施加 0 上限保护。其它状态比例保持 `null`，不冒充 100% 仓位授权。

高位箱体使用此前可见 20 日上下沿及显式候选参数，默认不激活箱体风险状态、不做区间交易。组合接口接受市场、板块、个股、账户、总预算上限取最小值，任一 0 可使目标为 0；缺少输入明确标记。

未来 5/10/20 日只在 `daily_evaluation.csv`；右侧不足、停牌/缺口、未验证复权路径单独标记。执行评价区分确认、次日研究委托意图、MODELLED_FILL/NO_FILL/EXECUTION_UNRESOLVED、无真实成交证据。跳过明确停牌，不跳过未知缺口，不自动重试跌停。真实日终 D4 限价不升级为合格早盘约束，故真实模型成交数为 0，执行回撤保持空值。

## 首轮输出与对照

`artifacts/run/` 有 1,324 行指标/风险、1,324 行独立评价、157 行基线风险日志（pending 与 confirmed），另有原 A/B/C/D 的 82 行日志。候选日志数量不等于卖出次数。

| 股票 | MA28 三日确认 | MA55 三日确认 | 确认日需处理的去重行 | 模型成交 | 执行未解决 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 昆仑万维 | 19 | 16 | 30 | 0 | 30 |
| 光迅科技 | 16 | 6 | 22 | 0 | 22 |

1/2/3 日对照见 `confirmation_comparison.csv`。昆仑万维对应的 MA28/55 确认次数为 34/25、21/16、19/16；光迅科技为 28/13、18/8、16/6。五日内重站均线的事后代理、可评价分母、最近高点回撤及首次跌破后变化也已记录。不同确认方案的事件集合不同，这些数值不是配对因果效应，也不能自动挑选最优天数。

合成输出 135 行，覆盖四类候选信号、三日减仓/清仓确认、两个合格约束下的模型成交；测试另外验证跌停无法成交、早盘约束缺失、已知停牌和未知缺口。合成日期和约束均显式标注为人工数据。

每次普通运行生成七个 CSV、Markdown 报告和 JSON 回执。CSV 为本轮逐日交付格式。回执保存代码、配置、Snapshot、Git HEAD/分支、全部输出 SHA256 及行数。合成及两只真实股票的九个输出文件各重复运行，在独立 Python 进程（PYTHONHASHSEED=11/29）中，字节全部一致。

## 药明康德初始冻结与自动检查

身份 `WUXI_FORWARD_20261013_V0_1`；初始观察及记录日期 `2026-10-13`；实际数据截至 `2026-09-23`；规则 `TREND_EXIT_BASELINE_V0_1`；风险配置 SHA256 `f1826b0b4df15afcc03108cc9f9bf91af48e21a5aa58f47752b1ed8af55263fa`。原始不可覆盖初始回执在 `artifacts/forward/`；可审阅副本为 `reports/forward_initial_receipt.json`。当前为 `WAITING_FOR_POST_INCEPTION_DATA`，前瞻逐日指标、信号、评价均 0 行，不能宣称完成截至 10 月 13 日的行情验证。

每日归档按观察日期、Snapshot 和代码身份分目录，重复相同运行不覆盖旧字节；规则/证券身份冲突会停止。旧日迟到数据标记 `LATE_CAPTURE_NOT_SAME_DAY_PROSPECTIVE`，不冒充同日前瞻结果。

已按用户选择创建本聊天 heartbeat，名称“药明康德离线前瞻观察”，ID `automation`，ACTIVE，周一至周五上海时间 16:30。只读本地合格 Snapshot，无新数据保持安静，仅在实质新数据/信号、失败或需要处理时通知。它不下载行情。自动任务初次排期格式报错后改正，最终创建与读取均成功。电脑需开机、桌面应用运行才能处理本地文件，依据[官方定时任务说明](https://learn.chatgpt.com/docs/automations)。

## 命令与验证

从 RSS 根目录运行；本机 Python 路径与 PYTHONPATH 设置见 README：

```powershell
python -m trend_exit_research.src.cli coverage
python -m trend_exit_research.src.cli synthetic
python -m trend_exit_research.src.cli run --max-real 2
python -m trend_exit_research.src.cli forward --as-of 2026-10-13
python -m pytest trend_exit_research/tests -q
```

- 新增测试 **56 passed，0 skipped，0 failures**：原 34 项与新增风险预算/执行/前瞻/跨进程 22 项。覆盖前缀及未来后缀、T 锚点复权、三日不回填、1/2 日对照、站回归零、停牌/缺口、风险状态、零预算、不可覆盖身份、执行条件及结果评价隔离。
- 现有相关回归 **98 passed，0 skipped，0 failures**，包括 factor PIT、trade lifecycle、研究缓存、D8 Snapshot、Stage 1 foundation。未运行全仓库套件。
- 原有冻结发布验证器：foundation 7、primitives 13、observation/entry 11 个哈希通过。
- 两只真实股票和合成九文件字节复现；前瞻初始回执重复冻结一致；禁用 Python 网络连接和 AmazingData/TGW 导入仍完成验收；用户 Excel SHA 未变。
- 开发过程中暴露的前缀 dtype 推断问题已固定列类型，未降低断言；旧缓存测试需 Windows 硬链接，经自动审批在沙箱外原样通过，没有改缓存实现。

## 未实现及下一阶段前置条件

尚无完整组合回测、账户回撤动态预算、实际市场/板块同步性输入、自动箱体区间交易、仓位比例优化、正式收益回测或交易接入。真实早盘限价/可成交约束和历史发布时刻未验证，真实跨除权窗口也未获逐证券证据准入。其它股票本轮只做覆盖登记。

下一阶段需要先提供药明康德观察起点之后的已验证冻结数据；补齐两只状态缺口；补充历史时点及早盘约束、除权实施/修订证据；再预注册正常分歧对照、评价定义和独立验证切分。具体仓位比例以及组合预算输入须显式确定，不能由这几个案例自动优化。

本轮没有创建或合并 PR。后续可在人工验收后创建 Draft PR，保持既有合并纪律。结论限定为 **PASS_WITH_LIMITATIONS** 的研究工程基础建设。

最终跨进程检查还固定了样本登记字段顺序；相同数据与规则的输出不依赖 Python 集合哈希随机种子。
