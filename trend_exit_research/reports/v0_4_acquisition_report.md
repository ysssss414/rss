# TREND_EXIT_V0_4 有界采集预检与交付回执

本轮判定为 **BOUNDED_ACQUISITION_STOP**，具体原因是 `STOP_NETWORK_SCOPE_UNQUALIFIED`。授权范围明确，但现有证据不能合理验证SDK原生初始化及查询的实际网络范围，按用户任务第4节在联网前停止。真实SDK导入、登录/初始化、Python数据接口尝试、市场SDK网络请求均为 **0**；新增真实行情、状态和Snapshot均为 **0**。缺少严格执行字段并非本次停止原因，不能把工程测试通过解释为采集成功。

## 授权范围与实际结果

| 证券/案例 | 本次允许新增日期 | 用途 | 新行情/新状态 | 结果 |
| --- | --- | --- | --- | --- |
| 昆仑万维 300418.SZ / KUNLUN_2023_AI | 2022-07-01～2023-12-31 | 2022下半年预热、2023研究 | 0 / 0 | TARGET_CASE_WINDOW_UNCOVERED |
| 五粮液 000858.SZ / WULIANGYE_2020_2021 | 2019-07-01～2022-03-31 | 2019下半年预热、2020—2021研究、2022一季度评价 | 0 / 0 | TARGET_CASE_WINDOW_UNCOVERED |
| 药明康德 603259.SH / WUXI_FORWARD_20261013 | 2026-09-24～2026-10-13 | 截至初始观察日的历史补取 | 0 / 0 | TARGET_CASE_WINDOW_UNCOVERED |

原冻结Snapshot对这三股实际行情均为2024-01-02～2026-09-23；授权新增区间内已有行情均为0。昆仑2024一季度已有58条行情仅作覆盖核验，没有重新下载，也不能替代2023案例。五粮液2020—2021原走势未恢复。没有新证据，全部均线确认、条件执行价、回撤、未来5/10/20/40日路径和重新准入结果为NOT_COMPUTED_TARGET_UNCOVERED，不是零收益或没有跌破。

## SDK审计依据与边界

读取本地AmazingData **1.1.6 cp312** wheel和TGW **1.0.8.7** wheel，使用Python 3.12读取匹配magic的code object和字节码，并静态解析TGW Python源。没有安装、导入SDK或原生库；没有读取.env或账户值。完整机器回执为 [v0_4_sdk_scope_audit.json](v0_4_sdk_scope_audit.json)，记录成员SHA256、函数原始行号、参数、引用名、写入属性和分页/重试设置。没有把cp312轮子的检查冒充另一个运行环境的实测。

- `MarketData.__init__`只存储calendar及锁；`query_kline`先按begin/end过滤calendar，再按1500日线交易日分页，以传入code构造ReqKline，设置security_code、market_type、begin_date、end_date。本任务每个完整区间少于1500会话，Python日线构造不会自动扩成其它证券。`auto_complete=1`是否影响原生/服务端范围仍无资格证据。
- `InfoData`及`DownloadInfoData`构造在可见Python层没有证券数据获取。传入begin_date后，`get_history_stock_status`走显式日期下载分支，不读取本地缺失后补全分支；下游按code_list构造market_code、start_date、end_date。
- 一个状态接口会分别请求 **A010060002、A010060005** 两个类型，然后合并。这是已知、限定证券日期的Python层分解，不是16次接口调用等于16次网络请求的证据。
- `QueryPara.retry_times=3`：Python查询在首次请求失败后最多另外3次。它可在Python层设置为0以隔离外层一次失败重试，但本轮没有初始化或修改SDK运行状态。登录出现并发限制时有5次额外Login，并设置force_logout，最多6次底层调用。
- RSI原provider的`_ensure_market`还会获取calendar；未调用该初始化链。本轮没有合格的2019—2023离线calendar输入，也不将无限日期元数据请求悄悄加入授权。全市场更新器、证券主数据、无日期因子接口均未调用。

TGW `Login`进入`IGMDApi_Init`；日线及状态进入`IGMDApi_QueryKline`和`IGMDApi_QueryThirdInfo`。SWIG包装将三者直接绑定到 **_tgw.pyd**，相关行为继续在 **tgw.dll** 中执行。现有可见包装和本地资料没有与该版本匹配的证据证明：初始化不抓取范围外证券/历史数据、原生层不会隐藏重试或额外请求、日期不会被扩张。审计未证实发生了全市场扇出；也无法据此宣称没有扇出。仅检查输出范围、计量Python调用或接管Python socket不能证明此原生层行为。

因此止于预检，未启动2026-09-24～09-26药明试调用，未进入完整采集。初始化计数0、内部网络计数0源于整个真实SDK链未执行；运行后的原生请求上界仍UNKNOWN。GitHub PR操作属于交付操作，不计为市场SDK请求。

SDK wheel SHA256：`bebca92db0079ff3aad5bdb328f687b980eba65ee5829722e31862d5572ff69c`。
TGW wheel SHA256：`cc32f4e70a0c1e02a9fe432068a871ab0b9275d7610315143d6f9bb70b0813c9`。
cp312 _tgw.pyd：`1af7212327f5b2e79de92700515bfcb6ec5982346f06de3683fd6218572bf9a0`；tgw.dll：`b412373b2fd315a13eee52d96a2390960e8630badba0f2c8bbf223978c0f92ba`。

## 资格分级及独立Snapshot契约

三个新增目标均为 **EXPLORATORY_DATA_UNQUALIFIED**，首先因为授权区间raw行情和历史状态未取得。原冻结Snapshot加载PASS只表示原对象结构/哈希有效，不能升级为这三个目标的历史PIT或成交资格。

**STRICT_PIT_EXECUTION_UNQUALIFIED**：还缺历史因子/实施及修订事件、状态生效时点、历史早盘交易约束与成交模型证据；raw单位也缺版本对应的独立证据。V0.4不能把未知因子填成1后宣称连续复权价格，不使用已有事后调整资料回填信号。

新增`capture_v0_4`复用RSS DataRequest、numeric/calendar/timestamp验证、canonical JSON及SnapshotStore不可变写入，建立按证券和内容哈希隔离的raw manifest。保留原始OHLCV/amount、交易状态、可用因子/事件证据、真实检索时刻、请求/实际范围、行数、数据集指纹、代码/配置和缺失证据。manifest本身SHA256即snapshot_id；reload核对manifest和所有嵌入数据集指纹/行数。原始证据不设置伪造的历史available_at，不输出连续调整价。没有历史因子只降级严格资格，已通过raw校验的合成测试仍能取得探索级字段资格；它不等同于已建立连续价格研究资格。

真实新Snapshot清单 **[]**，没有空快照、未完成数据的“成功manifest”，也没有新增真实snapshot哈希。仅pytest临时目录有标为SYNTHETIC_TEST_ONLY的测试对象，不能用于真实案例或16:30观察。没有修改旧V1格式、D8契约或前瞻loader，当前raw格式未接入要求更完整证据的旧观察器。

## 药明初始观察和自动检查

保留 `WUXI_FORWARD_20261013_V0_1` 与2026-10-13初始归档，起始数据实际截至2026-09-23。初始回执SHA256 `cf914a490cc3088705da21a1f2dc27b94bf933b03601164a5ea4f7e993401023`，归档与跟踪副本逐字节一致。

本轮未补取，actual_backfill_retrieved_at=null；没有10月13日当天信号或之后的前瞻验证。迟到捕获契约与测试要求真实retrieved_at和 **LATE_CAPTURE_NOT_SAME_DAY_PROSPECTIVE**；不得修改初始记录以隐藏迟到。实际后续观察仍等待可用数据。

周一至周五16:30上海时间的automation配置SHA256仍为 `9e111c8c7335ec80241b957e600bedb6e27e291a5a253ebce9baa2266aa8234a`，只读本地已验证冻结Snapshot、无新数据安静、不联网刷新。没有调用automation更新或更改它的网络权限；当前也没有新Snapshot可供它取得10月13日数据。

## 运行、测试及版本保护

最小命令：`python -m trend_exit_research.src.v0_4 --out trend_exit_research/artifacts/v0_4_final`。本机Python、PYTHONPATH和完整回归命令见 [README_v0_4.md](../README_v0_4.md)。重现目录为 `artifacts/v0_4_repeat`。四份预检输出逐字节一致；只读运行不生成市场数据或模拟成交。

独立验证脚本从同一vendor wheel取选定函数code object，用完全离线fake TGW绑定验证一次日线和两种状态请求实际参数，以及登录失败6次尝试。模块、原生库、credentials loader从未执行。它证明Python语义，与真实/native资格明确分离。保护清单核对 **754个** 既有跟踪/本地结果文件，包含用户3个Excel、全部61个V0.1—V0.3跟踪研究文件；RSS旧Snapshot完整校验 **104个** constituent。没有改公共模块、冻结规则或既有本地产物。

新增测试覆盖单证券日期白名单、禁止范围扩张、超范围/重复响应拒绝、最多2尝试与总16次fake预算、失败smoke不能进入full、scope失败不作transport重试、raw OHLCV/amount、停牌/缺失状态、缺失行情、单位证据、严格资格降级、不可变manifest/reload/篡改拒绝、部分失败隔离和迟到前瞻标签。最终测试数量和JUnit证据见 [v0_4_validation_receipt.json](v0_4_validation_receipt.json)，不将fake预算或字段测试视为真实在线采集通过。

原始Snapshot manifest SHA256：`a41628925227863150aa7785bc4a15e811a55e6bafb3d5136062a277c99d4c67`。
基准配置SHA256：`f1826b0b4df15afcc03108cc9f9bf91af48e21a5aa58f47752b1ed8af55263fa`。
V0.4配置SHA256：`82fe704488a6b7a6bcc47f66f06359c581bc85621107216b3da9af4ba48f84f0`。
范围审计JSON SHA256：`11178223ba7e3ddc4a89a4f41ad8598a383af3e22f1276b0e69e9d3b0468bbff`。
运行回执SHA256：`a29a8037bd98a1cec71e2e41274140a8d088a0d1c69a8c4975e40c7e8d6b0880`。

## Git交付范围与下一步前置条件

起始/保护锚点 `ebd42f4d8cea15e78f11cbe59bf955b511534cb2`，当前feature `codex/trend-exit-research-v0.1`，[Draft PR #33](https://github.com/ysssss414/rss/pull/33)，base保持既有stacked分支。仅新增V0.4文件；没有把用户Excel加入提交，没有改V0.1—V0.3文件。新增文件完整清单、源码/测试/文档哈希及回归结果保存在validation receipt。交付提交身份以Git/PR为准，回执使用不可变父提交锚点和内容哈希，避免把自己的提交哈希写回而形成循环。

最小前置条件是取得版本匹配的TGW原生初始化、查询、自动补全、重试和缓存范围契约/可审计实现，并明确限制或计量它们。可复用合格的离线calendar，或单独明确有界calendar获取方式；补齐query_kline单位证据。继续保持当前3证券日期授权，不用全市场请求作探针，也不为此修改长期自动化。通过scope后再实施药明不超过3自然日的bars/status smoke，失败最多各重试一次；两个接口和数据校验均通过后再按证券分别采集，总Python接口尝试上限16，SDK内部请求另计。原始数据成功者独立冻结；严格执行字段缺失可保留UNQUALIFIED并继续合格的raw探索，不应成为统一阻断。

当前没有新实证结论、优化或交易；后续恢复原目标案例前不能进入仓位管理收益研究。

判定分别为：**BOUNDED_ACQUISITION_STOP**；**EXPLORATORY_DATA_UNQUALIFIED**；**STRICT_PIT_EXECUTION_UNQUALIFIED**；**CASE_ALIGNMENT_PARTIAL**；**STRATEGY_EFFECTIVENESS_NOT_YET_ESTABLISHED**。
