# A. Implementation Summary

Stage 0 实施完成，离线验收 PASS。建立了标准行情 schema、显式 provider capability、
证券身份与数值校验、不可变 snapshot、先数据后 coverage 的缓存写入、因子身份与
decision/outcome 分离、run manifest、研究资格及冻结回归。

主 baseline 在运行路径改动之前冻结；完整管线补充参考由原始 Git HEAD 的临时副本生成。
旧合格输入结果保持一致。未改 RSI/MD 业务模块，未开始 Stage 1。

# B. Files Changed

以下 37 个文件均为本次实施代码、测试、reference 或文档。用户原有 Excel 不包含在此清单。

| 文件 | 修改目的 |
|---|---|
| [README.md](D:/ej/材料/codex/rss/README.md) | 增加 Stage 0 文档入口 |
| [pyproject.toml](D:/ej/材料/codex/rss/pyproject.toml) | 将 research 加入 package discovery；不新增运行依赖 |
| [cli.py](D:/ej/材料/codex/rss/three_board_rsi_entry/cli.py) | 增加固定 snapshot 与 outcome cutoff 入口；Numba 改为显式兼容模式 |
| [market_data.py](D:/ej/材料/codex/rss/three_board_rsi_entry/market_data.py) | 证券 identity、统一质量、retry 分类、因子拒绝、原子缓存/coverage；保留 legacy 复权公式 |
| [pipeline.py](D:/ej/材料/codex/rss/three_board_rsi_entry/pipeline.py) | 指标前拒绝越界行情和错误证券 |
| [research/__init__.py](D:/ej/材料/codex/rss/research/__init__.py) | 最小 research package 标识 |
| [research/data/__init__.py](D:/ej/材料/codex/rss/research/data/__init__.py) | data package 标识 |
| [contracts.py](D:/ej/材料/codex/rss/research/data/contracts.py) | schema、状态、error classification、请求与时间窗口 |
| [provider.py](D:/ej/材料/codex/rss/research/data/provider.py) | capability 与固定 snapshot 的 legacy adapter |
| [validation.py](D:/ej/材料/codex/rss/research/data/validation.py) | 单一 OHLCV、duplicate、时间戳、身份、状态、calendar 规则 |
| [adjustment.py](D:/ej/材料/codex/rss/research/data/adjustment.py) | raw/adjusted 分离、明确 factor schema 与 anchor |
| [cache.py](D:/ej/材料/codex/rss/research/data/cache.py) | 内容寻址 snapshot、组件 hash、持久化后 coverage |
| [amazingdata.py](D:/ej/材料/codex/rss/research/data/amazingdata.py) | 保留已验证 SDK 调用路径的 identity-preserving bridge |
| [research/runs/__init__.py](D:/ej/材料/codex/rss/research/runs/__init__.py) | runs package 标识 |
| [manifest.py](D:/ej/材料/codex/rss/research/runs/manifest.py) | canonical serialization、规格 hash、输入与环境 provenance |
| [legacy.py](D:/ej/材料/codex/rss/research/runs/legacy.py) | 连接旧 pipeline、独立 outcome、研究资格与输出 |
| [regression.py](D:/ej/材料/codex/rss/research/runs/regression.py) | 固定 tolerance 全字段比较及质量修正分类 |
| [tests/helpers.py](D:/ej/材料/codex/rss/tests/helpers.py) | 移除导入即创建目录的副作用 |
| [tests/conftest.py](D:/ej/材料/codex/rss/tests/conftest.py) | 将旧测试目录绑定至逐测试 tmp_path |
| [test_market_data.py](D:/ej/材料/codex/rss/tests/test_market_data.py) | 适配真实 identity-preserving 调用，明确冲突 duplicate 质量修正 |
| [test_research_data.py](D:/ej/材料/codex/rss/tests/test_research_data.py) | schema、因子、状态、时间戳、prefix、共同 anchor、CSV 质量检查 |
| [test_research_provider.py](D:/ej/材料/codex/rss/tests/test_research_provider.py) | fake SDK、wrong symbol、空/部分/未知/停牌、retry、失败脱敏 |
| [test_research_cache.py](D:/ej/材料/codex/rss/tests/test_research_cache.py) | 中断、coverage、安全刷新、旧缓存修复、内容校验 |
| [test_research_integration.py](D:/ej/材料/codex/rss/tests/test_research_integration.py) | 完整 snapshot→pipeline→outputs/CLI；原始 HEAD 对照；eligibility/manifest/causality |
| [test_stage0_regression.py](D:/ej/材料/codex/rss/tests/test_stage0_regression.py) | 冻结场景与历史数据逐字段比对，不更新 expected |
| [freeze_stage0_reference.py](D:/ej/材料/codex/rss/scripts/freeze_stage0_reference.py) | 修改 runtime 前使用的一次性冻结脚本，拒绝覆盖和错误源码身份 |
| [reference.json](D:/ej/材料/codex/rss/baselines/stage0/reference.json) | 12 组语义及历史 expected、config、来源身份 |
| [checksums.json](D:/ej/材料/codex/rss/baselines/stage0/checksums.json) | 主 reference 完整性校验 |
| [head_input.xlsx](D:/ej/材料/codex/rss/baselines/stage0/head_input.xlsx) | HEAD 输入的固定副本，与用户输入区分 |
| [historical_input.xlsx](D:/ej/材料/codex/rss/baselines/stage0/historical_input.xlsx) | 000020.SZ 显式历史子集副本 |
| [historical_raw.csv](D:/ej/材料/codex/rss/baselines/stage0/historical_raw.csv) | 固定旧 raw observations |
| [historical_factors.csv](D:/ej/材料/codex/rss/baselines/stage0/historical_factors.csv) | 固定历史因子 |
| [pipeline_reference.json](D:/ej/材料/codex/rss/baselines/stage0/pipeline_reference.json) | 原始 HEAD 完整管线 expected 与 examples 输入 hash |
| [pipeline_reference.sha256](D:/ej/材料/codex/rss/baselines/stage0/pipeline_reference.sha256) | 完整管线 reference 校验 |
| [baselines README](D:/ej/材料/codex/rss/baselines/stage0/README.md) | 冻结文件身份、限制及不可覆盖规则 |
| [research_stage0.md](D:/ej/材料/codex/rss/docs/research_stage0.md) | 正式契约、运行方法、能力限制 |
| [本实施报告](D:/ej/材料/codex/rss/docs/stage0_implementation_report.md) | A–I 验收与逐文件记录 |

calendar 检查合并在 validation 中，没有为了目录草案创建空 calendar 模块。

# C. Data Contract

唯一键 `(ts_code, trade_date)`；raw OHLC 与 adjusted OHLC 分层；volume=share、amount=CNY。
状态包含 UNKNOWN，UNKNOWN 的 suspended 必须为 null。缺行情不补停牌。
显式带时区的 available_at 控制数据可用时间，retrieved_at 记录提取时间。
全部观察值相同的 duplicate 可去重并审计；冲突行 hard fail。

证券 dict key 和内嵌 code 都必须匹配，不允许 fallback 到其他证券。
NaN/Inf、非正价格、负量额、异常 OHLC、非法日期、未来数据及非法因子都有明确拒绝路径。
未支持的历史 capability 返回 UNSUPPORTED_CAPABILITY，不返回伪成功空表。

snapshot 包括 bars/calendar/factors/request/metadata 的内容身份及组件 hash。
Manifest 包括输入/config/代码/依赖/日期/因子/anchor/provider 的计算规格身份。
普通 legacy run 可以继续运行；只有新 snapshot 入口标为 Research-compatible。

# D. Causality Guarantees

Decision 和 outcome 分别读取。SnapshotProvider 先切日期和 available_at，再供给旧指标/回放。
完整 snapshot 与仅提供 T 及以前数据的结果一致；未来 bar、factor、status 变更不改变
T 的 indicators、candidate、signal、eligibility。延长 outcome_as_of 只影响后续事件评价。

Outcome 复权使用 decision anchor；拆股测试验证 signal 与 entry 不会因不同 anchor 错配。
实际 calculation_start 与旧 pipeline 加载的 warmup 起点一致。
未来因子刷新不会修改已存在的 snapshot；旧 ID 可重新加载。

边界：仅日频上海 EOD，不支持盘中可用性；来源未给出历史发布时间或修订版本时不能凭空恢复。
AmazingData bridge 将 availability_verified 标为 false，使相应样本明确不合格。

# E. Regression Results

预先固定 abs tolerance=1e-10、rel tolerance=1e-12；未扩大 tolerance，未覆盖 expected。

| 对照 | Before | After |
|---|---|---|
| 12 组冻结语义 | 原始 runtime 的 expected | 全部字段、stable keys、MD、收益、MFE/MAE、incomplete 和 summary 一致 |
| 固定 examples 完整管线 | 原始 HEAD：2 signals / 2 events | 新 snapshot 入口：2 signals / 2 events；全表比较 PASS |
| 000020.SZ 历史子集 | 1 cycle / 0 signals | legacy 复现一致；正式 schema 因缺 volume 拒绝 |
| HEAD Excel | 缺有效确认和连板信息 | 保留原始身份，不伪装为合格历史基准 |

完整管线示例的 before / after 在固定 tolerance 内分别为：

| 信号 | 5d return | 5d MFE | 5d MAE | 10d classification |
|---|---:|---:|---:|---|
| MD_1，2025-07-01 | 0.092931937172775 | 0.099476439790576 | -0.005235602094241 | INSUFFICIENT_FORWARD_DATA |
| MD_2，2025-07-03 | 0.031746031746032 | 0.037851037851038 | -0.004884004884005 | INSUFFICIENT_FORWARD_DATA |

质量修正单独记录，不强行保留错误结果：
`LEGACY_ACCEPTED → NEW_REJECTED; reason=SCHEMA_MISMATCH`（历史 cache 缺 volume）。
冲突 duplicate、未知状态和错误证券分别按新质量分类拒绝。

# F. Test Results

本次最终实际运行：**147 passed，0 failed，0 error，0 skipped，20.99 秒**。
Python 3.13.14，pytest 9.1.1，pandas 2.2.3，NumPy 2.2.4，openpyxl 3.1.5。
没有新增安装依赖；使用本机既有 Python 与 `.test_tools`。

```powershell
$env:PYTHONPATH = "$PWD;$PWD\.test_tools"
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -B -m pytest -q -x --tb=short --basetemp=.test_tmp/stage0_final_01 --junitxml=.test_tmp/stage0_final_results.xml
```

以下分类互不重复，相加为 147：

| 类别 | 本次结果 | 范围 |
|---|---:|---|
| Unit / cache | 65 PASS | research_data 36、research_cache 6、market_data 6、rsi_indicator 4、three_board_input 7、backtest_summary 6 |
| Fake provider | 13 PASS | research_provider：正常/空/部分/失败/错证券/未来/停牌/UNKNOWN、session、因子 |
| Integration | 15 PASS | research_integration 8、outputs 3、backtest_outputs 3、backtest_cli 1 |
| Deterministic replay / events | 40 PASS | cycle_supersession 7、md1 5、md2 6、replay_no_lookahead 5、three_board_cycles 5、backtest_events 12 |
| Frozen regression | 14 PASS | 12 场景、历史子集、reference/hash；完整 HEAD 管线另计在 integration 内 |
| AmazingData live smoke | NOT RUN | 未连接真实服务，不将 fake tests 当成 SDK 实测 |

测试结果 XML 保存在
[stage0_final_results.xml](D:/ej/材料/codex/rss/.test_tmp/stage0_final_results.xml)。
测试临时目录受 gitignore 排除，不是项目真实行情/输入目录。
`git diff --check` 在仓库正常 CRLF 配置下通过；没有改动仓库换行配置或格式化用户文件。

# G. Open Questions

| 能力 | 当前边界 |
|---|---|
| Historical ST | 没有可靠历史源，unsupported；未用当前 ST 填历史 |
| Historical Universe / listing state | 未实现且 unsupported；仍只处理人工 Excel 中的证券 |
| Limit-up official fields | unsupported；未添加比例推断或自动涨停检测 |
| Factor density / revision | 本地单证券因子不能证明 SDK 全局约定；density 必须显式声明，历史 revision/PIT 尚待来源证据 |
| Suspension classification | 缺行和零 volume 均不能证明停牌；明确状态才可标 SUSPENDED，其余 UNKNOWN/排除 |
| SDK 单位、发布时间、session 错误码 | 未做实际联网 smoke；单位未确认时 capture 默认拒绝，availability 未确认时样本不合格 |

这些能力以 unsupported / unverified 边界保留，不是隐式实现完成。

# H. Git Status

Repo：`D:\ej\材料\codex\rss`。
分支：`main`。HEAD：`fb09873473188a8671d2cff0132b9d932e9c6cb6`，与开始时一致。
没有创建 commit、暂存、reset、stash、clean 或推送。
源码变更仅为 B 中 Stage 0 范围。

以下用户保护文件在开始和最终核验时 SHA-256 完全相同：

| 用户文件 | SHA-256 |
|---|---|
| `inputs/three_board_daily_input.xlsx`（原有 modified） | `38DABDC991D26D4D1B5873BFDCBFB5DD29DB4671FA1760C20D932D3D17D9B9FE` |
| `inputs/three_board_daily_input_2026_backtest.xlsx`（原有 untracked） | `282C4ABDBB5901F5E3EE33512E7256887C1E92AE129662F631397FBD6943CF22` |
| `手工统计.xlsx`（原有 untracked） | `2FDF23576A7D220E994CF74143236A0B3DB34E9E495F769927D0A65C994F8277` |

没有向 `D:\ej\材料\codex\RSI` 或 `D:\ej\材料\codex\yh` 写入。
indicators、replay、backtest、config、models、input_excel 与 HEAD 无 diff。
当前 Git status 的用户 Excel 项是开始时已存在的变更，不是此次实施产生。

# I. Stage 0 Verdict

**PASS**

Stage 0 软件边界与离线验收条件满足：旧合格语义回归通过，证券错配/未来数据/未知状态
有明确阻断或排除，snapshot 与 cache 顺序可验证，manifest 可追溯，147 项测试通过，
受保护文件未变。PASS 不代表 AmazingData 未确认的历史能力已可用于合格研究。
未开始 Stage 1。
