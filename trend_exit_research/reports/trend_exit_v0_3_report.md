# TREND_EXIT_V0_3：样本区间与主升持仓语义

本轮只验证目标案例对齐、实验趋势资格与持仓观察区间的因果关联。没有入场成交证据、持仓成本或数量，真实浮盈、利润回吐和收益保持空值。缓存价格模型也不构成真实持仓交易。

## 1. 原始案例与数据是否正确对齐

| case_id | 股票 | 目标研究区间 | 状态 | 预热覆盖 | 评价覆盖 | 实际计算 |
| --- | --- | --- | --- | --- | --- | --- |
| KUNLUN_2023_AI | 昆仑万维 | 2023-01-01～2023-12-31 | TARGET_CASE_WINDOW_UNCOVERED | False | False | False |
| GUANGXUN_UNREGISTERED | 光迅科技 | 待登记～待登记 | TARGET_WINDOW_NOT_REGISTERED | False | False | False |
| DONGSHAN_UNREGISTERED | 东山精密 | 待登记～待登记 | TARGET_WINDOW_NOT_REGISTERED | False | False | False |
| BAIWEI_UNREGISTERED | 佰维存储 | 待登记～待登记 | TARGET_WINDOW_NOT_REGISTERED | False | False | False |
| ZHONGJI_UNREGISTERED | 中际旭创 | 待登记～待登记 | TARGET_WINDOW_NOT_REGISTERED | False | False | False |
| XINYISHENG_UNREGISTERED | 新易盛 | 待登记～待登记 | TARGET_WINDOW_NOT_REGISTERED | False | False | False |
| WULIANGYE_2020_2021 | 五粮液 | 2020-01-01～2021-12-31 | TARGET_CASE_WINDOW_UNCOVERED | False | False | False |
| AIER_UNREGISTERED | 爱尔眼科 | 待登记～待登记 | TARGET_WINDOW_NOT_REGISTERED | False | False | False |
| YUANJIE_UNREGISTERED | 源杰科技 | 待登记～待登记 | TARGET_WINDOW_NOT_REGISTERED | False | False | False |
| SHENGYI_UNREGISTERED | 生益科技 | 待登记～待登记 | TARGET_WINDOW_NOT_REGISTERED | False | False | False |
| CHANGCHUAN_UNREGISTERED | 长川科技 | 待登记～待登记 | TARGET_WINDOW_NOT_REGISTERED | False | False | False |
| ZHAOYI_2026_ASOF_20260923 | 兆易创新 | 2026-01-01～2026-09-23 | TARGET_CASE_WINDOW_COVERED | True | False | True |
| ZHAOYI_2025_NOV_COUNTEREXAMPLE | 兆易创新 | 2025-11-01～2025-11-30 | TARGET_CASE_WINDOW_COVERED | True | True | True |
| SANHUA_2025 | 三花智控 | 2025-01-01～2025-12-31 | TARGET_CASE_WINDOW_COVERED | True | True | False |
| WOLONG_2025 | 卧龙电驱 | 2025-01-01～2025-12-31 | TARGET_CASE_WINDOW_COVERED | True | True | False |
| FOXCONN_2025 | 工业富联 | 2025-01-01～2025-12-31 | TARGET_CASE_WINDOW_COVERED | True | True | True |
| WUXI_FORWARD_20261013 | 药明康德 | 2026-10-13～2026-10-13 | TARGET_CASE_WINDOW_UNCOVERED | False | False | False |

原16股名单不变，兆易创新拆成两个独立case，合计17行；9个未指定走势的案例保持未登记，未自动采用其它区间。昆仑万维2023、五粮液2020—2021超出快照，明确为TARGET_CASE_WINDOW_UNCOVERED。332.23只是用户提供的事后分析参考，不进入信号。兆易创新2026区间经用户确认截至2026-09-23，之后没有验证；2025年11月反例单独登记，不能补造此前已有持仓。工业富联保留2025。预热、研究、评价区间分别记录，评价日不新增持仓观察区间或动作意图。

## 2. 哪些确认发生于合格主升观察区间

实验资格需要60个有效观察，MA28和MA55五观察斜率严格为正、MA28高于MA55、近20观察上涨至少15%、收盘不低于MA28、相对MA55不超过30%、距离当时60观察高点不超过10%。参数在任何V0.3行情计算前登记，不做优化，也不声称预测能力。TREND_ELIGIBLE、TREND_NOT_QUALIFIED、TREND_DATA_UNQUALIFIED与原始POSITION_RISK_STATE分列；默认TREND不证明主升，更不授权入场。历史15:30可用时刻仍是假设。

| case | 主升资格日 | 未达资格日 | 数据不足日 |
| --- | --- | --- | --- |
| FOXCONN_2025 | 10 | 173 | 60 |
| ZHAOYI_2025_NOV_COUNTEREXAMPLE | 1 | 19 | 0 |
| ZHAOYI_2026_ASOF_20260923 | 14 | 103 | 60 |

B组仅将研究区间内首次资格日设为收盘参考标记，不生成买入或默认满仓。每个case/确认天数/执行等级最多一个观察区间；同股票新case不继承旧区间。退出后只记录重新评估候选，不能自动恢复持仓。已知停牌暂停，未知缺口/未验证复权使持仓价格路径失效，不能事后桥接。

| case | 执行等级 | 观察起点 | MA28意图 | MA55意图 | 观察模型退出 | 状态 | 重新评估候选 | 退出后延迟交易日 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ZHAOYI_2026_ASOF_20260923 | QUALIFIED_ONLY | 2026-01-07 | 2026-03-04 | 2026-03-25 | — | EXIT_REQUIRED_EXECUTION_UNRESOLVED | 2026-05-12 | — |
| ZHAOYI_2026_ASOF_20260923 | CONDITIONAL_CACHED_LIMITS | 2026-01-07 | 2026-03-04 | 2026-03-25 | 2026-03-26 | EXITED_OBSERVATION_MODEL_ONLY | 2026-05-12 | 29 |
| ZHAOYI_2025_NOV_COUNTEREXAMPLE | QUALIFIED_ONLY | 2025-11-03 | 2025-11-18 | 2025-11-24 | — | EXIT_REQUIRED_EXECUTION_UNRESOLVED | 2026-01-07 | — |
| ZHAOYI_2025_NOV_COUNTEREXAMPLE | CONDITIONAL_CACHED_LIMITS | 2025-11-03 | 2025-11-18 | 2025-11-24 | 2025-11-25 | EXITED_OBSERVATION_MODEL_ONLY | 2026-01-07 | 29 |
| FOXCONN_2025 | QUALIFIED_ONLY | 2025-07-04 | — | — | — | HOLDING_DATA_INVALIDATED_EXPOSURE_UNKNOWN | — | — |
| FOXCONN_2025 | CONDITIONAL_CACHED_LIMITS | 2025-07-04 | — | — | — | HOLDING_DATA_INVALIDATED_EXPOSURE_UNKNOWN | — | — |

## 3. 无持仓前提的重复跌破事件

| case | 事件归类 | 确认事件数（每均线分列） |
| --- | --- | --- |
| FOXCONN_2025 | HOLDING_DATA_UNQUALIFIED | 4 |
| FOXCONN_2025 | NO_QUALIFIED_HOLDING | 6 |
| ZHAOYI_2025_NOV_COUNTEREXAMPLE | LINKED_OBSERVATION_INTENT_ONLY | 2 |
| ZHAOYI_2026_ASOF_20260923 | AFTER_EXIT_INTENT_NO_REENTRY | 3 |
| ZHAOYI_2026_ASOF_20260923 | LINKED_OBSERVATION_INTENT_ONLY | 2 |
| ZHAOYI_2026_ASOF_20260923 | MA28_INTENT_ALREADY_ISSUED | 1 |

A组raw_risk_events保留所有确认、每均线初跌日期、1/2/3确认、等待成本、未来5/10/20/40价格路径、五日重站均线代理；它们是诊断事件，不能计为多笔持仓卖出。B组event_holding_links标记无持仓、重复MA28、清仓意图后重复跌破、数据失效以及MA55覆盖MA28。计数不外推全部A股，也不构成逃顶成功率。

## 4. MA28与MA55在哪个阶段作用

有资格观察区间才关联退出意图；MA28三日确认最多一次降仓意图，仓位上限未知保持null，无法计算减仓数量或宣称减仓成交。MA55三日确认使目标为0，进入EXIT_REQUIRED；同日覆盖MA28。严格早盘约束缺失则执行未解决且真实暴露未知；缓存lane仅以T+1可交易开盘价作观察退出模型，状态直到该日才更新。全部actual_sell_generated为false，modelled_position_trade为false，不能将观察退出代理升级为合格持仓成交。

| case | 等级 | 关联意图 | 确认 | 潜在执行日 | 价格执行模型 | 已知区间高点到潜在执行价 |
| --- | --- | --- | --- | --- | --- | --- |
| ZHAOYI_2026_ASOF_20260923 | QUALIFIED_ONLY | MA28_REDUCE_CAP_INTENT | 2026-03-04 | — | EXECUTION_UNRESOLVED | — |
| ZHAOYI_2026_ASOF_20260923 | QUALIFIED_ONLY | MA55_CLEAR_INTENT | 2026-03-25 | — | EXECUTION_UNRESOLVED | — |
| ZHAOYI_2026_ASOF_20260923 | CONDITIONAL_CACHED_LIMITS | MA28_REDUCE_CAP_INTENT | 2026-03-04 | 2026-03-05 | CONDITIONAL_MODELLED_FILL | -12.98% |
| ZHAOYI_2026_ASOF_20260923 | CONDITIONAL_CACHED_LIMITS | MA55_CLEAR_INTENT | 2026-03-25 | 2026-03-26 | CONDITIONAL_MODELLED_FILL | -19.07% |
| ZHAOYI_2025_NOV_COUNTEREXAMPLE | QUALIFIED_ONLY | MA28_REDUCE_CAP_INTENT | 2025-11-18 | — | EXECUTION_UNRESOLVED | — |
| ZHAOYI_2025_NOV_COUNTEREXAMPLE | QUALIFIED_ONLY | MA55_CLEAR_INTENT | 2025-11-24 | — | EXECUTION_UNRESOLVED | — |
| ZHAOYI_2025_NOV_COUNTEREXAMPLE | CONDITIONAL_CACHED_LIMITS | MA28_REDUCE_CAP_INTENT | 2025-11-18 | 2025-11-19 | CONDITIONAL_MODELLED_FILL | -13.48% |
| ZHAOYI_2025_NOV_COUNTEREXAMPLE | CONDITIONAL_CACHED_LIMITS | MA55_CLEAR_INTENT | 2025-11-24 | 2025-11-25 | CONDITIONAL_MODELLED_FILL | -20.07% |

执行后价格路径仅作事后评价；严格未成交没有执行锚点，下表保持空值。所有5/10/20/40日路径及信号锚定的未成交暴露见holding_action_outcomes.csv。

| case/动作 | 等级 | 后5日 | 后10日 | 后20日 | 后40日 | 40日MAE | 40日期末上涨代理 | 40日状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ZHAOYI_2026_ASOF_20260923/MA28_REDUCE_CAP_INTENT | QUALIFIED_ONLY | — | — | — | — | — | — | NO_EXECUTION_ANCHOR |
| ZHAOYI_2026_ASOF_20260923/MA55_CLEAR_INTENT | QUALIFIED_ONLY | — | — | — | — | — | — | NO_EXECUTION_ANCHOR |
| ZHAOYI_2026_ASOF_20260923/MA28_REDUCE_CAP_INTENT | CONDITIONAL_CACHED_LIMITS | -4.64% | 5.39% | -14.29% | 19.48% | -17.80% | 19.48% | AVAILABLE |
| ZHAOYI_2026_ASOF_20260923/MA55_CLEAR_INTENT | CONDITIONAL_CACHED_LIMITS | -7.84% | -1.09% | 11.08% | — | — | — | UNQUALIFIED_ACTION_PATH |
| ZHAOYI_2025_NOV_COUNTEREXAMPLE/MA28_REDUCE_CAP_INTENT | QUALIFIED_ONLY | — | — | — | — | — | — | NO_EXECUTION_ANCHOR |
| ZHAOYI_2025_NOV_COUNTEREXAMPLE/MA55_CLEAR_INTENT | QUALIFIED_ONLY | — | — | — | — | — | — | NO_EXECUTION_ANCHOR |
| ZHAOYI_2025_NOV_COUNTEREXAMPLE/MA28_REDUCE_CAP_INTENT | CONDITIONAL_CACHED_LIMITS | -1.87% | 3.67% | 0.96% | 40.05% | -12.11% | 40.05% | AVAILABLE |
| ZHAOYI_2025_NOV_COUNTEREXAMPLE/MA55_CLEAR_INTENT | CONDITIONAL_CACHED_LIMITS | 13.78% | 14.03% | 17.50% | 62.96% | 0.00% | 62.96% | AVAILABLE |

价格代理从观察起点收盘参考与此后当日可知高价计算，起点之前的高点不能计为持仓浮盈。高点到下一开盘的差额只用确认日已知高点，禁止纳入执行日稍后高价；真实最大浮盈和利润回吐因成本/数量缺失为空。未知减仓比例不影响价格路径代理，但不能解释为剩余100%风险仓位。未成交暴露用信号收盘锚定的unexecuted_*另记，退出后上涨、MAE、机会成本代理仅在outcomes，不能参与资格或风险判断。

## 5. 仍缺少哪些数据和规则

需补齐2023昆仑、2020—2021五粮液原始行情/交易日历/历史状态/复权事件与因子证据，药明9月24日至10月13日增量，以及可信的历史早盘约束。还需真实或明确授权的入场依据、成本、数量、风险预算和MA28目标上限，明确失败委托处置与重新入场规则，才能计算真正仓位管理收益。本轮未实现自动箱体交易、连续账户回测或参数优化。

有界采集方案见 reports/bounded_acquisition_plan_v0_3.md/json。实际在线采集等待明确授权；底层不能保证证券/日期范围的接口停止。现有update_market_store新日期可全市场扇出、calendar helper过滤掉2024年前日期、因子endpoint无日期参数、旧freeze CLI写死2026-09-23，均不能直接用来补齐新案例。未来只允许独立不可变快照，旧Snapshot和前瞻初始身份保持不变。补取的药明历史必须标记LATE_CAPTURE_NOT_SAME_DAY_PROSPECTIVE。

研究版本TREND_EXIT_V0_3，配置SHA256 `3712e9b219d4eeb5bf89c9287b880d5826e9a315aa9560cbe494d6a323545280`，案例登记SHA256 `b50d46dbe79bc56bf0ea57c62e325a7895838ef29bb227e72ac46d7f0ab1e17c`；基准配置SHA256 `f1826b0b4df15afcc03108cc9f9bf91af48e21a5aa58f47752b1ed8af55263fa`；快照manifest `a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67`。代码、输入、输出身份见run_receipt.json；测试及交付身份另见v0_3_validation_receipt.json。

判定：工程测试以独立验证回执为准；CASE_ALIGNMENT_PARTIAL；HOLDING_SEMANTICS仅验证因果观察关联，真实入场/仓位交易未验证；STRATEGY_EFFECTIVENESS_NOT_YET_ESTABLISHED。

## 交付验证（2026-10-14）

工程：ENGINEERING_PASS，207项通过（原179项+V0.3新增28项），0失败/错误/跳过。独立核对553个资格状态、3318次基准计数、46条执行后价格路径均通过，工业富联原V0.2三日确认事件逐日相同。两次最终完整运行的9张CSV、生成报告和run_receipt共11文件逐字节一致。命令、代码/配置/案例/快照/输出哈希见v0_3_validation_receipt.json。

案例：CASE_ALIGNMENT_PARTIAL；16股原名单保留、17个独立case，3个优先case完成离线计算（两个兆易和工业富联）。昆仑2023、五粮液2020—2021、药明初始观察区间未覆盖，未用2024年后的走势替代；其余未明确区间的股票不参与本轮计算。复核当前市场存储和两个既有raw_daily缓存根，三个补齐证券的旧CSV均未发现，HDF因子缓存缺历史资格，不提升为合格Snapshot；详见local_data_inventory_v0_3.json。

持仓语义：HOLDING_ASSOCIATION_SEMANTICS_PASS，仅指因果观察区间与动作意图的关联；HOLDING_TRADE_SEMANTICS_NOT_ESTABLISHED，入场、数量、比例及严格历史执行资格仍缺。基准三日共有18个原始确认（两个lane各记录一次），其中4个与有效观察区间关联、14个仅为无区间前提/重复/退出后/数据失效诊断。全部实际卖出及实际收益为0条或空值，没有将这些事件变成18笔交易。

兆易2026：1月7日取得观察资格，3月4日MA28确认、3月5日条件开盘价格288.16；3月25日MA55确认、3月26日条件价格268，至确认日已知观察高点331.16，价格高点到潜在退出回落19.07%。上述价格与起点262.5的比值是观察代理，不能称真实浮盈或真实利润回吐。5月12日出现重新评估候选，距离条件观察退出29个交易日，没有自动买回，后续跌破不再生成同一观察区间的新动作。

兆易2025年11月反例：11月3日取得本实验资格，11月18日MA28关联意图、11月24日MA55关联意图，缓存条件价格11月25日185；后40日上涨62.96%，候选直到2026年1月7日出现（29交易日），没有假定在低点买回。仅凭原始均线事件不能称成功逃顶；真实持仓前提仍未证实。

工业富联2025：7月4日取得观察资格，随后未验证复权重启使持仓价格路径失效，未跨该事件虚算最大浮盈或清仓回吐。此前无主升观察区间时的多次均线确认，以及后来路径失效后的确认，均保持诊断身份。补齐复权实施/修订证据是后续验证该例的前置条件。

策略：STRATEGY_EFFECTIVENESS_NOT_YET_ESTABLISHED。实验阈值未寻优，案例人工选择，完整真实生命周期/组合预算/收益系统未实现。计划在线数据采集数为0；24只是提议的Python数据API尝试上界，真实SDK初始化/内部网络上界未证明。因子无日期参数等路径仍停止，等待用户明确授权及边界资格证明之后才可继续。

V0.3源文件均新增，旧41个V0.1/V0.2跟踪文件、原基准配置、原Snapshot、药明初始冻结内容和三个用户Excel哈希均未改变；没有修改Stage 1契约或冻结结论。Draft PR仍为#33、分支codex/trend-exit-research-v0.1，起点6ec68fc。最终交付HEAD以PR分支与提交回执为准，不合并。
