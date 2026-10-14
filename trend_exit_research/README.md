# RSS 趋势退出与风险预算研究 V0.1

独立、离线的趋势衰减、退出纪律与风险预算研究模块，允许低仓位和目标仓位为 0。当前交付验证计算、因果性和可复现性，**不证明退出规则有效**。记录下一可交易日卖出模型的条件与事后路径，不发送真实委托，不做组合收益回测，不接入 Stage 1 RSI 生命周期，不改动冻结研究结论。

## 运行

从 RSS 仓库根目录执行，使用 Python 3.10+ 及现有 `pandas`、`duckdb`、`pytest` 依赖：

```powershell
python -m trend_exit_research.src.cli coverage
python -m trend_exit_research.src.cli synthetic
python -m trend_exit_research.src.cli run --max-real 2
python -m trend_exit_research.src.cli forward --as-of 2026-10-13
python -m trend_exit_research.src.cli run --confirm-days 1 --out trend_exit_research/artifacts/confirm1
python -m pytest trend_exit_research/tests -q
```

模块无需安装、联网或 AmazingData 登录；不能刷新缓存或下载行情。`--snapshot` 可指定相同 RSS 格式的冻结 Snapshot；可指定 `--samples`、`--config`、`--risk-config` 和模块 `artifacts/` 内的 `--out`。普通运行会覆盖所选输出目录的派生文件；需要保存不同方案时使用不同 `--out`。`forward` 使用独立不可覆盖归档，默认选择本地日期最新且经过完整校验的冻结 Snapshot。

`coverage` 只读检查所有登记样本；`run` 按 CSV 顺序计算最多两条完整样本区间，不按信号或结果挑选。不能将该命令扩大为全市场筛选。`synthetic` 使用明确标注的人工路径，交易日为合成工作日，不能解释为真实交易日历。

本机 Windows 的 Store Python 启动器不可用，本次使用 Codex 自带 Python 3.12.14，并将缺少的 DuckDB/Pytest 安装到忽略目录 `.cache/trend_exit_runtime/`，未下载市场数据：

```powershell
$env:PYTHONPATH = (Resolve-Path .cache/trend_exit_runtime).Path
$env:PYTHONIOENCODING = 'utf-8'
& 'C:/Users/cjzs0/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -m trend_exit_research.src.cli run
```

## 登记样本

`inputs/manual_samples.csv` 支持名称、带交易所的代码、起止日期、备注、样本来源。代码可为空：仅允许证券主数据中的唯一精确名称匹配；歧义、未知名称、代码与名称不符均进入覆盖报告，不猜测。人工区间可为空；本轮只将 Snapshot 全区间作为覆盖和 smoke 区间，不将它写回人工选择。区间端点只控制输出切片，不重置指标状态。备注和样本来源不进入指标函数，人工顶部信息不能回填信号。

同一股票可追加多个独立日期区间；重复的名称、代码、区间身份被拒绝。追加样本无需改 Python。16 个代码由 `REAL_RESEARCH_SNAPSHOT_V1/security_master` 唯一匹配后登记；该主数据是截止时点快照，不是历史名称/身份全集。新增三个历史案例固定为 2025 年，计算保留 2024 年数据预热；研究结束后行情只可供独立评价。药明康德身份为 `WUXI_FORWARD_20261013_V0_1`、类型 `FORWARD_OBSERVATION`，初始观察日期为 2026-10-13。

## 数据与 PIT

复用 `research.snapshot.FrozenResearchSnapshot` 的每次打开哈希、组成文件和行数校验，读取原始 OHLC、成交额、日状态、D5 后复权因子及冻结交易日历。复用 `research.real_materialization.bar_validation_status`，保留缺失日期并拒绝重复键。每条逐日记录保留原始价格、复权指标价格、Snapshot ID、manifest 哈希、因子来源指纹、配置版本及哈希。

复用 `research.data.adjustment.adjusted_bars`，在 **每个 T** 截断并以 `factor(d)/factor(T)` 重建历史价格。斜率的旧均线也在同一 T 价格尺度上计算；不能直接对不同锚点下的每日均线做价格差。`factor_pit.FactorRatioSnapshot` 用于完整事件证据路径的合成验证，拒绝不能解释的因子比值变化。

真实 Snapshot 没有各证券完整的独立实施/修订证据，也没有可验证的历史发布时刻。真实数据采用保守的 `EVENT_FREE_SEGMENT_ONLY`：当日因子变化、除权或除息标记使历史/信号状态重置，随后重新预热；本轮不跨事件计算非平凡复权比值，不借用 `OUTCOME_ONLY` D8 表作为信号输入。不能据此宣称真实跨除权历史已获 PIT 准入。日期可用性明确标为假设的上海时间 15:30，非历史发布时间证明。

已知停牌日期输出空指标与无信号，跳过价格观察，并中断连续跌破计数；缺失、未知状态、无效 OHLCV/成交额或因子、晚于决策时刻的数据会重置历史和候选状态。不前向填充价格，不桥接缺口。股票上市前日期不在分母中。明确超出 Snapshot 的人工区间标为 `PARTIAL_OUTSIDE_SNAPSHOT`，不进入真实计算。换手率缺失保持空值，覆盖报告披露。

## 候选机制

全部参数为待验证的初始配置，位于 `config/default.json`，未经优化。

| 信号 | 首版操作定义 |
| --- | --- |
| A | 连续 2 个有效观察收盘低于 MA28 的 99%，在该次连续跌破第 2 日首次记录 |
| B | 从 A 当日起累计至少 5 个有效观察日，出现至少 3% 的实际反弹，此后再下跌、同时低于 MA13 和 MA28，且 MA13 的 5 日斜率非正；每个 A 状态最多一次 |
| C | 与 A 相同的确认规则，使用 MA55 |
| D | 首次收盘跌破此前 20 个有效观察的最低价 1%，且当日收盘仍不低于 MA55 |

B 的修复条件是收盘达到 MA13 和 MA28 的较高值；达到后关闭该次 A 状态。A/C 连续计数在收盘恢复到缓冲阈值或停牌时清零；这是连续有效跌破片段的首次，不是整段牛市仅触发一次。D 使用排除当日的滚动低点作为支撑代理，未对它的有效性作经验认证。

记录 MA13/28/55、5 个观察日的相对斜率、MA28/55 距离及距离变化、价格偏离、60 观察日最高价/回撤、5/10/20 日成交额均值和总额、与前一不重叠等长窗口的变化、5/20 均额比。收敛需结合距离正负与变化解读：单独的负变化不等于绝对距离收敛。

局部结构采用收盘反转 4% 的在线极值确认，记录极值日期及 `known_at` 日期；确认前不回填旧行。A 后的反弹高低点也只随当日数据更新。所有候选信号统一等待最大必需观察窗口（默认 60），不足时仍输出可计算指标，但不发候选信号。

## 冻结退出基线与风险状态

`config/risk_baseline.json` 固定 `TREND_EXIT_BASELINE_V0_1`：收盘严格低于各自当日 MA28/55 才计数，零缓冲，第三个连续有效收盘确认，不回填首次跌破日。站回或等于均线清零。已知停牌暂停基线计数，缺失/未知/复权未解决重置；原 A/C 候选的停牌清零口径保留在其独立版本中。默认同时记录 1/2/3 日对照，不自动选最优；`--confirm-days 1/2` 标为独立 scenario，不冒充三日基线。

MA28 确认要求降低仓位上限；MA55 首次跌破进入 `EXIT_PENDING` 并禁止加仓，确认进入 `EXIT_REQUIRED`，趋势目标为 0；同日双确认优先 `CLEAR`。`DEFENSIVE` 修复要求连续 3 个有效日站回 MA28/55 且两条 5 日斜率非负。退出后同样满足修复条件只进入 `REENTRY_CANDIDATE`，保持目标 0，等待另行评估，不自动买入或升为 TREND。

`POSITION_RISK_STATE`、状态有效性、配置键、仓位上限、减仓/清仓标记逐日保留。TREND、DEFENSIVE、BOX_LOW_EXPOSURE、EXIT_PENDING 的最终比例均为 `null`；未知不能解释为 100%。EXIT_REQUIRED 和 REENTRY_CANDIDATE 为 0。数据未就绪时额外施加 0 上限保护。`combine_risk_caps` 接受个股、市场、板块、账户、总预算上限并取最小值，缺失输入明确披露；任一明确 0 即约束为 0，没有必须持仓约束。

首版 TREND 是尚未触发更严约束的默认研究状态，不是经过验证的主升分类器。状态允许评估加仓也不等于获得组合预算或真实交易授权。

箱体仅记录此前 20 个有效观察的高低沿，使用 60 观察日高点距离、已有涨幅和区间宽度做候选筛选，参数显式且未经优化。默认 `box_state_enabled=false`，不会据箱体自动交易。启用候选状态仍无法绕过 MA55 或更低仓位上限。

## 执行与结果评价

`evaluation.py` 单独读取未来数据；`risk.py` 和 `indicators.py` 不读取评价结果。每行评价包含之后 5/10/20 个冻结交易日的价格变化、截至信号日最近 60 观察最高价的确认回撤、下一可交易日及执行回撤。缺口、跨未验证复权事件、停牌路径、样本边界和右侧数据不足显式标记，不虚构结果。

确认后的研究委托意图指向下一已知非停牌交易日，09:15 委托、09:25 开盘模型；遇到未知状态停止，不跳过不明缺口。不自动滚动重试跌停。复用 `research.foundation.NEXT_SESSION_SELL_V1` 对合格的早盘约束区分 `MODELLED_FILL`、`NO_FILL`、`EXECUTION_UNRESOLVED`，不修改原政策。真实日终 D4 上下限不冒充已验证早盘约束，保留 vendor 诊断但真实执行状态为未解决。`actual_execution_status` 始终明确没有真实委托/成交证据。

1/2/3 日对照报告确认次数、之后五日重站对应当日均线的代理、确认时最近高点回撤及首次跌破后价格变化。它是这些人工案例的描述性对照，事件集合不同，不能当作配对因果效应、总体假跌破率或参数最优性证据。

## 前瞻冻结与更新

首次运行创建不可覆盖的 `artifacts/forward/WUXI_FORWARD_20261013_V0_1/initial_receipt.json`，冻结证券、初始日期、实际数据日期、Snapshot、规则和信号代码哈希。每日归档按观察日、Snapshot manifest 和代码身份分目录，保留每日信号和当时可见的后续结果，不能覆盖旧记录。规则变化要求另行审阅并使用新的观察身份。

当前数据只到 2026-09-23，2026-10-13 初始观察归档为 `WAITING_FOR_POST_INCEPTION_DATA`，前瞻逐日行数为 0。不会把 9 月及以前信号称作前瞻。以后延迟捕获的历史日标记为 `LATE_CAPTURE_NOT_SAME_DAY_PROSPECTIVE`，同日捕获才标为 `SAME_DAY_CAPTURE`，仍披露历史发布时间缺少证据。

用户已授权工作日上海时间 16:30 在本聊天自动检查。本地无新数据时安静，仅对实质新数据/信号、失败或需要用户处理时通知；自动任务不联网获取行情。可手动执行同一 `forward --as-of YYYY-MM-DD` 命令。已验证新 Snapshot 需要由其它获授权流程提供。

## 输出与验收

每次普通运行输出七个 CSV：`coverage`、`daily_indicators`、`candidate_signals`、`risk_signals`、`daily_evaluation`、`confirmation_comparison`、`risk_summary`，另有 `research_report.md` 与 `run_receipt.json`。本轮选用 CSV，不增加 Parquet 依赖。逐日信号/风险表和未来评价表分开，参数和快照可相互连接。运行回执包含输入输出 SHA256、模块与复用代码哈希、Python/Pandas、Git HEAD/分支、快照身份、双规则配置和逐表行数。没有可变生成时间，以便相同代码、输入、配置、运行环境产生相同字节。空结果仍有表头。

`artifacts/` 内本地逐日行情派生文件全部忽略，不提交 Git。`reports/` 保存有界的覆盖、验证摘要和样例，不放完整市场数据。仓库默认 pytest 的 `testpaths` 只有 `tests/`，新增测试需显式指定 `trend_exit_research/tests`。

新增测试覆盖四类信号、未来后缀扰动、前缀回放、除权同锚点/事件比值、共同归一化、非共同修订拒绝、缺失/停牌/晚到输入、代码歧义、追加样本独立性、在线极值确认和字节可复现性。最终证据见 `reports/bootstrap_report.md`。`PASS_WITH_LIMITATIONS` 只指本轮工程流程在已披露限制下通过，不是策略效果结论。
