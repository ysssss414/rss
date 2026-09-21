# RSS Research Engine — Stage 0

Stage 0 建立数据与复现边界，策略仍由 `three_board_rsi_entry` 执行。
默认 RSI、MD_1 / MD_2、20 日观察期和事件收益规则不变。没有迁移
indicators、replay、backtest，没有实现自动选股或后续研究阶段。

## 入口与兼容范围

原来的 CSV / AmazingData CLI 仍可运行。Research-compatible run 必须通过
`run_with_snapshot` 或 CLI 的 `--research-snapshot` 入口，才能获得不可变数据身份、
研究资格和 manifest。旧的可变 CSV 缓存本身不具备研究复现保证。

已有 snapshot 的离线运行示例（将 ID 替换为保存时返回的真实 SHA-256）：

```powershell
python -B -m three_board_rsi_entry.cli backtest `
  --input examples/backtest_smoke_input.xlsx `
  --start-date 2025-06-30 --as-of 2025-07-07 --outcome-as-of 2025-07-14 `
  --research-cache .cache/research --research-snapshot <snapshot-id> `
  --output-dir outputs/research-example
```

输出目录必须为空或不存在。保留旧 CSV / XLSX 输出，新增
`research_manifest.json` 和 `research_eligibility.csv`。`run` 只计算信号；
`backtest` 才计算后续收益。`--outcome-as-of` 只用于 snapshot 入口，
`--force-refresh` 不能与固定 snapshot 一起使用。

## 正式数据契约

`DataRequest` 使用明确带交易所的证券代码、含首尾的日期范围。
Daily bar 的 schema 为 `daily-bars/1`，唯一键是 `(ts_code, trade_date)`：

| 字段 | 语义 |
|---|---|
| `ts_code`, `trade_date` | 证券身份；ISO 日期 |
| `raw_open/high/low/close` | 原始价格，不接收混放的复权价格 |
| `volume`, `volume_unit` | 股数，单位只能为 `share` |
| `amount`, `amount_unit` | 成交金额，单位只能为 `CNY` |
| `trading_status` | `TRADING / SUSPENDED / NOT_LISTED / DELISTED / UNKNOWN` |
| `suspended` | 布尔或 null；UNKNOWN 必须为 null |
| `available_at` | 必须带时区的可获得时间，按上海 EOD 边界筛选 |
| `source`, `retrieved_at` | 来源和提取时间 |
| `schema_version`, `snapshot_id`, `quality_status` | 版本、快照身份、质量状态 |

交易行要求完整有限的 OHLC、volume、amount。价格必须为正，成交量/额不能为负，
high/low 必须包住 open/close。明确的非交易行可以保留空价格；缺行情不会补成停牌。
UNKNOWN 行保留原状态且不能承诺完整 coverage，进入 legacy 计算时不生成价格观察。

相同键、相同观察值可以去重，snapshot 记录去重数量；提取时间与 snapshot 标签不构成
观察值冲突。任何价格、成交量、金额、状态或 availability 冲突均拒绝。
旧 CSV 也执行相同的数值与冲突检查；旧 CSV 缺 volume 可以继续兼容运行，
但不能直接成为正式 research snapshot。不会补造 volume。

主要错误分类：`SCHEMA_MISMATCH`、`SYMBOL_MISMATCH`、`INVALID_DATE`、
`AS_OF_VIOLATION`、`INVALID_AVAILABILITY`、`CONFLICTING_DUPLICATE`、
`MISSING_VALUE`、`INVALID_NUMBER`、`INVALID_OHLC`、`INVALID_STATUS`、
`UNKNOWN_TRADING_STATUS`、`INVALID_FACTOR`、`UNSUPPORTED_CAPABILITY`。

## Provider 和 AmazingData 边界

Provider 提供 `capabilities`、`trading_calendar`、`security_master`、
`security_status`、`daily_bars`、`adjustment_factors`。
没有可靠来源的 capability 返回 `UNSUPPORTED_CAPABILITY`。
历史 ST、历史证券主数据/上市状态、官方涨停价没有用当前状态替代实现。

Bridge 继续使用 rss 已有 session、BaseData 因子调用和
`_ensure_market().query_kline([symbol], ...)` 日线调用。
它跳过会 fallback 到 dict 第一项的 legacy `get_daily_bars`：
先验证返回 dict 的唯一键与请求一致，再验证存在的内嵌证券字段，最后才补充标准 code。
错误 key 或内嵌 code 都立即失败，不能改标签伪装修复。

串行调用，默认复用一个 session；仅 `ConnectionError` / `TimeoutError` 重试。
显式 `SessionInvalidated` 清除失效 session，再建立新 session。
schema、symbol、factor 错误不重试；未知 SDK 异常归类失败，不能冒充空响应。
真实 SDK 的其他错误码还没有擅自映射为可恢复异常。

凭据继续从原有外部配置/环境读取，不序列化配置对象或异常原文。
错误日志仅记录分类和重试次数。snapshot metadata 采用明确字段白名单；
manifest 仅保存输入文件 hash 与源码相对路径构成的 hash，不保存绝对配置路径。
Numba shim 默认关闭，仅在 AmazingData 的 `--numba-compat` 显式模式启用。
旧 `--no-numba-compat` 参数保留。

Bridge 的正式 capture 默认拒绝未验证单位。需要先有证据确认 volume/amount 单位，
才能传入 `units_verified=True`；不进行猜测换算。复权 capture 还必须明确
`factor_schema='daily'` 或 `'effective_events'`。默认 `unknown` 不允许复权。

Bridge 的 EOD `available_at` 是按行情日期构造的观察时间假设，
`availability_verified=False` 会进入 snapshot，相关样本被排除出合格研究。
它不声称历史数据未经修订，也不声称掌握当时真实发布时间。

## Causality 与复权

`AnalysisWindow` 区分 `calculation_start`、`signal_start/end`、
`decision_as_of`、`outcome_as_of`。现有 pipeline 一次 replay 到 decision cutoff，
所以当前 adapter 的 signal_end 等于 decision_as_of；没有添加另一种策略执行语义。

本版本仅支持日频上海 EOD。数据日期与 `available_at` 均受 decision cutoff 限制，
截止日意味着当天上海时间 24:00 之前。SnapshotProvider 每次返回独立副本。
legacy pipeline 在计算指标前也会检查 provider 是否返回了范围外日期或其他证券。

先固定 decision 输入并计算 indicators / candidates / signals / eligibility，
之后才单独读取 outcome 区间计算事件收益。改变未来价格、因子、状态，或延后
outcome_as_of，不会改变同一 decision cutoff 的上述结果。
完整 snapshot 和截断到 T 的 snapshot 的 decision 输出也一致。

原始价格一直保留，调整结果另加 `open/high/low/close`、`adjustment_ratio`、
`adjustment_mode`、`adjustment_anchor`。`none` 不复权；`qfq` 以明确 anchor 归一化。
日密集因子必须覆盖每个价格日期；事件因子只按明确的生效日期向前延续，
保留早于 bars 起点或落在非行情日期的生效事件。未知密度不套用 ffill。
非正、非有限、冲突、缺覆盖因子明确失败。

Outcome 价格使用同一个 decision anchor，避免跨拆股时把 signal price 和
entry price 放在不同标度。因子原值、schema 和 hash 均随 snapshot 固定。
刷新后引用新因子必须产生新 snapshot/provenance；不覆盖旧 run 的身份。

`available_at` 不能恢复来源没有提供的历史修订版本。Stage 0 的保障是按已提供、
明确标注的数据时间过滤和复现；真正 point-in-time 数据仍取决于来源证据。

## Snapshot / cache

`Snapshot.create` 校验后，将 bars、calendar、factors、请求和来源 metadata
规范化序列化；SHA-256 即 snapshot_id。各组件另有独立 hash。
内部 bar 的 snapshot_id 留空避免自引用，读取时附加所属内容身份。

`SnapshotStore` 的对象写入顺序：同目录临时文件 → flush/fsync → 不覆盖的对象发布。
coverage 必须先加载并验证已落盘对象，确认所有请求证券/交易日都有可信状态且质量 PASS，
再原子替换指针。相同目录内使用 filesystem 原子操作；采用单 writer 模式，
不承诺数据库式多 writer 或所有硬件断电场景的事务保证。

空响应、部分响应、UNKNOWN 会保留在已保存的 snapshot 中；仍存在未解释日期时，
不提交完整 coverage。经过确认的空交易日历可形成合法空覆盖。
没有 cache hit 是 `None`；空响应是有身份的对象；provider failure 是异常，三者不同。
刷新创建新对象并可更新后续请求的 coverage 指针，旧 ID 始终能重新加载。

旧 raw CSV cache 同样先原子持久化行情，再更新 coverage；只覆盖实际持久化日期。
读旧 coverage 时与实际 raw 日期取交集，避免遗留覆盖记录掩盖缺失数据。
`force_refresh` 替换请求日期内的数据，包括清除已无响应的旧行；不能继续把它们认作 covered。

## Manifest 与研究资格

Manifest 包含运行 ID、计算规格 hash、策略版本、Git SHA、工作区状态、源码指纹、
Python/依赖版本、完整 config 和 hash、输入 hash、snapshot_id/data_as_of、
五个时间边界、raw/calendar/factor hashes、复权模式和 anchor、provider/SDK 版本、
quality policy、操作类型、allow_incomplete、状态和创建时间。

`run_id` 每次不同；相同数据、输入、环境、代码和计算规格的 `spec_hash` 相同。
run_id、created_at、结果状态与 eligibility 不进入计算规格 hash。
未来部分发生变化会改变完整 snapshot/spec identity，即使 T 的信号保持一致。

Eligibility 是单独输出，能计算收益不等于研究合格。
每个信号用该信号的 decision prefix 判定；没有信号的 cycle 用本次 decision cutoff。
检查实际计算窗口的 warmup、日历与 availability 证据、缺失日期、UNKNOWN、quality_status
以及人工输入确认完整性。排除原因包括：
`insufficient_warmup`、`incomplete_snapshot`、`unknown_trading_status`、
`data_quality_failure`、`unverified_calendar`、`unverified_historical_availability`、
`incomplete_manual_input`。不使用未来 outcome 或未来状态修正资格。

## 冻结 reference 与回归

主 reference 在任何运行路径改动之前生成：
`baselines/stage0/reference.json`，abs tolerance `1e-10`、rel tolerance `1e-12`。
测试不会重算或覆盖 expected；生成脚本也拒绝已有 reference 和非原始 HEAD runtime。

* 12 组 semantic fixtures 覆盖 RSI 边界、首次 MA5、MD_1 / MD_2、9 日调整、
  20/21 日边界、cycle 替换、停牌/下一可交易开盘、MFE/MAE、不完整 outcome。
* HEAD Excel 与当前用户 Excel 的身份分别记录。HEAD 输入缺有效确认/连板记录，
  不声称其能完成合格回测，也没有将用户修改版变成唯一 baseline。
* 已有历史数据只为 `000020.SZ` 提供可冻结因子，因此保存该证券的显式历史子集：
  1 个 cycle、0 个信号。旧结果可复现，但缺 volume、独立日历及历史状态证据，
  不认定为合格 research fixture。缺 volume 的正式导入分类为
  `LEGACY_ACCEPTED → NEW_REJECTED; reason=SCHEMA_MISMATCH`。
* 补充完整管线参考来自临时目录中的原始 Git HEAD 代码，未使用新代码生成 expected。
  固定 examples 输入及其 hash：2 个信号、2 个事件。
  新 snapshot fixture 将这些合成价格置于因子恒为 1 的合成世界，并显式提供单位；
  不冒充恢复出的真实未复权数据。

比较覆盖 candidate、signal、event 的全部字段和顺序，包括 stable key、MD 类型、
收益、MFE、MAE、incomplete reason、各 horizon/summary 样本数。

## 离线验证

在本机使用已存在的 Python 和 pytest，无需连接行情服务：

```powershell
$env:PYTHONPATH = "$PWD;$PWD\.test_tools"
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -B -m pytest -q --tb=short --basetemp=.test_tmp/stage0_new_run
```

`--basetemp` 应选择新的专用目录，pytest 会清理指定的旧临时目录。
测试采用 tmp_path、monkeypatch、fake session/cache，helpers 导入不创建数据目录。
本次运行结果和逐文件变更见 `stage0_implementation_report.md`。

## 未解决能力

历史 ST、历史 Universe/上市状态、官方涨停价仍 unsupported。
现有本地因子文件不能证明整个 SDK 的因子密度约定或历史修订政策。
零 volume 或缺行不能证明停牌；只有显式可信状态记录才能标为停牌。
没有实际运行 AmazingData 联网 smoke；session 错误分类、字段/单位、历史发布时间
仍须受控环境证据，当前默认拒绝未确认能力。
