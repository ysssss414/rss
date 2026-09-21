# A. Environment

**Gate verdict: STOP。数据方向：PATH B（有条件的 Hybrid Data Layer）。**

本次完成了受控 live qualification 与离线验证，但 Kline 单位没有足够证据，
历史 ST 的生效日也与发行人公告冲突。现有证据足以排除直接按已验证原生 PIT 数据进入
Stage 1 的做法，尚不足以放行正式数据实现。没有实施 Stage 1 策略。

| 项目 | 本次实际环境 |
|---|---|
| Repository | `D:\ej\材料\codex\rss` |
| 运行时分支 | `codex/rss-research-stage0-foundation` |
| Stage 0 baseline commit | `d856f6f84911e55e9ed6208d3610272cc323ee4d` |
| Stage 0 tag | 无 |
| Python / AmazingData | 3.13.14 / 1.1.6 |
| 凭据来源类型 | external config；未输出或保存内容/绝对凭据路径 |
| Compatibility | 独立诊断进程显式启用 Numba shim |
| 初始工作区 | 仅三个已知用户 Excel modified/untracked，无未知源码改动 |

已确认 Stage 0 的 reference、hash 文件、代码和测试均在该 commit 中。
前置实际测试：147 passed。用户文件开始/结束 SHA-256 见末尾保护记录。

# B. Live Smoke Summary

使用 7 只真实证券和 1 个无效代码。行情/状态请求均为不超过 21 天的固定窗口；
没有自动扫描候选、下载全市场多年 bars 或进行批量 benchmark。
为检查历史 Universe，查询了四个单日代码表，仅保存 count/hash/样本 membership。
因子 API 不提供日期参数，故只对两只股票取其接口返回的完整因子列，完整缓存留在
gitignored 临时目录，artifact 仅保留小样本和 hash。

| 统计 | 结果 |
|---|---:|
| 已完成 SDK 调用（含两次 login、空返回、异常） | 33 |
| 到达进程预算时仍在进行的因子调用 | 1，结果未采用 |
| 已完成调用耗时之和 | 428.415 秒，不含中断调用的未知剩余耗时及解释器开销 |
| 诊断层自动 retry | 0 |
| SDK 内部网络请求数/内部 retry | 未暴露，UNVERIFIED |
| 正常 raw daily rows | 42，含重复请求的 5 行 |
| 经 Stage 0 checked adapter 的 rows | 5 |
| 历史状态 rows | 32，含独立单日复查 |
| 两次完整因子返回 | 每次 8,730 日期 × 2 证券 |

其余返回规模：四次日历共 26,943 个日期元素，当前代码表 5,566 个代码，
当前证券信息 10,460 行，四次历史代码表共 16,778 个代码元素，stock_basic 7 行。
这些是不同类型返回的计数，不能当成下载了对应数量的日线。

首轮进程设置 300 秒上限，到达上限时中断在因子调用。此前证据保留；
第二轮仅补测因子和 ST 冲突日期，完成后使用受控 worker 退出，未调用 logout。
当前代码表/证券信息各耗时约 73/71 秒；日线短窗约 0.21–1.39 秒，
两列因子全历史刷新约 86–91 秒。按单证券短窗耗时机械乘当前 5,566 个代码，
串行约 19–129 分钟；这不是全市场 benchmark，也没有证明批量、缓存、限流或长窗口成本。
正式 Stage 1 预算必须重新按确定后的请求策略估算。

原始 stdout/stderr 丢弃，不记录异常原文、traceback 或 SDK 配置对象。
公开 market 字段采用白名单保存；完整当前/历史代码列表未进入 artifact。

# C. Capability Matrix

完整可机读矩阵：[capability_matrix.csv](D:/ej/材料/codex/rss/artifacts/stage1_gate_a/capability_matrix.csv)。

| Capability | Status | Evidence | Stage 1 Blocking? | Required Action |
|---|---|---|---|---|
| Trading calendar | VERIFIED | 沪深北实测；春节/国庆/周末/2016 年窗口 | 否 | 固定交易所日历与 snapshot；PIT 另行判断 |
| Historical security master | PARTIAL | 四个历史单日名单，IPO/退市 membership 变化正确 | 是 | 补充全覆盖、历史代码/板块语义校验 |
| Historical listing/delisting | PARTIAL | 日期与公告一致；上市状态 enum 与文档冲突 | 是 | 取得 enum/version 依据 |
| Historical ST | FAILED | 康美有效日两次实测均与公告不符 | 是 | 供应商修正或独立 effective-dated 来源 |
| Trading/suspension status | PARTIAL | 停牌日有独立状态；不存在/接口缺失仍有歧义 | 是 | 缺失保持 UNKNOWN；补零量/特殊交易样本 |
| Raw daily bars | PARTIAL | 普通、高成交额、停牌、IPO、公司行动样本 schema/identity 通过 | 是 | 明确单位与空/缺失语义 |
| volume units | UNVERIFIED | 本地手册及 metadata 未明确 Kline 单位 | 是 | 取得该接口的股/手证明 |
| amount units | UNVERIFIED | 本地手册及 metadata 未明确币种/量级 | 是 | 取得该接口的币种/金额量级证明 |
| official limit-up/down price | PARTIAL | 多板块与边界字段存在；比例冲突、IPO 0/999 值 | 是 | 确认官方来源、sentinel、规则版本 |
| reference price | PARTIAL | 除息日与原始昨收明显不同 | 是 | 补全公司行动参考价语义 |
| adjustment factor schema | VERIFIED | 两证券密集日表，日历/正值/有限/排序/唯一检查通过 | 否 | 限实测 schema 范围，记录 anchor |
| factor revision/PIT | UNVERIFIED | 当前刷新一致；无历史截止参数 | 是 | 获取历史版本或明确限制研究范围 |
| availability/PIT metadata | UNVERIFIED | 无发布/修订版本字段 | 是 | 保持 availability_verified=false |
| session lifecycle | PARTIAL | login/reuse/异常完成；logout 未安全验证 | 否 | 保留进程隔离，后续验证安全退出 |

VERIFIED 仅表示对应样本、语义和边界已核验，不表示所有市场历史版本都已证实。
没有将任何 empty result 解释成 UNSUPPORTED。

# D. Raw Daily Bar Findings

真实返回是证券代码为 key 的 dict，内表含 code/kline_time/OHLC/volume/amount。
价格和金额为 float64，成交量为 int64，kline_time 为不带时区的 datetime64[ns]。
此 kline_time 是行情所属时间，不能解释为历史发布时刻。
采样价格按元级小数展示不构成单位证明。

全部保存的 raw 行通过证券身份、日期范围、唯一性、有限值、正价格、OHLC 包含关系、
非负量额检查。单证券与多证券返回 key 均正确；Stage 0 checked adapter 的真实请求也通过。
对 live capture 做的离线 wrong-key、内嵌 wrong-code、future-date 变异均 hard fail，
没有修改真实网络响应。

康美 2024-07-03 没有 bar，而独立状态接口明确停牌。IPO 前一日没有 bar；
上市首日开始返回。武钢退市边界窗口与不存在代码均触发 TypeError，不能视为正常空集合。
周末和颠倒日期区间均出现空返回，必须分别结合日历和请求合法性判断。
本次没有观察到合法交易日 volume=0 的实际行；其含义以及半日/特殊交易状态仍未验证。

单位证据：本地《AmazingData 开发手册》PDF 第 145 页（印刷页 141）的 Kline 表，
仅描述 volume 为“成交总量”、amount 为“成交总金额”。相关行情附录与 SDK FieldInfo
也未提供适用于 A 股 Kline 的明确股/手及币种/金额量级。
其他接口（如行业指数或可转债）的单位不能迁移套用。因此两项仍 UNVERIFIED，
没有修改 Stage 0 的默认 units_verified=false。

# E. Historical Universe Findings

本机 SDK 确实支持 historical code list，不应再简单标为 CURRENT_MASTER_ONLY：

| 历史请求 | 返回规模 | 边界样本 |
|---|---:|---|
| 2016-09-05 | 2,910 | 含武钢；不含当时尚未上市的宁德/中芯/中力 |
| 2017-02-15 | 3,106 | 不含已终止上市的武钢 |
| 2024-12-23 | 5,380 | 不含中力 |
| 2024-12-24 | 5,382 | 含中力 |

日期边界分别与[武钢终止上市公告](https://www.sse.com.cn/disclosure/announcement/listing/c/c_20170208_4235564.shtml)
和[中力上市公告](https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20241223_10767028.shtml)一致。
不能将区间并集当作单日 Universe；本次使用 start=end 的单日请求。

stock_basic 返回了普通主板、创业板、科创板、新 IPO、历史退市证券，上市/退市日期可取得。
但历史板块有效期、所有 A 股完整覆盖、修订版本没有独立证据；此外退市武钢的 IS_LISTED=0，
与公开手册列举的退市 enum 3 不一致。没有自动按 0/3 建立业务映射。

# F. Historical ST / Status Findings

[康美发行人公告](https://static.cninfo.com.cn/finalpage/2024-07-03/1220520500.PDF)
规定 2024-07-03 停牌、07-04 恢复交易并撤销风险警示。

| 日期 | SDK IS_ST_SEC | SDK IS_SUSP_SEC | 关键观察 |
|---|---|---|---|
| 07-02 | 1 | 0 | 5% 比例，限价 2.02 / 1.82 |
| 07-03 | 1 | 1 | 确认停牌；比例字段 10%，价格仍为约 5% 口径 |
| 07-04 | 1 | 0 | 公告已撤销风险警示，但标志仍为 1；限价已按 10% |
| 07-05 | 0 | 0 | ST 标志才切到 0 |

使用全新请求目录单独查询 07-04，结果仍为 1。故“在 T 当日是否 ST”的直接语义未通过边界检查。
这里只确认冲突，不能推断全部证券统一滞后一天，更不能将字段整体平移作为修复。
历史 ST 资格为 FAILED；不是 Stage 0 regression failure，也不是策略 bug。

独立停牌状态在康美停牌日及武钢退市前停牌日有正面证据。未上市/已退市需要历史名单和
上市/退市日期共同解释；接口异常或缺少状态记录时保持 UNKNOWN。

# G. Limit-Up / Reference Price Findings

历史状态接口真实返回上下限价格、涨跌幅比例和 PRECLOSE。
主板 10%、创业板/科创板 20%、ST 5%、IPO、除息样本均已采集。
普通样本的价格与给定参考价/比例在最小价格精度下一致；这只是诊断校验，未实现自算引擎。

两个关键边界：

* 康美 07-03 的比例字段为 0.1，但 PRECLOSE=1.93、HIGH_LIMITED=2.03、LOW_LIMITED=1.83，
  与该比例不一致。相关字段不能无条件混用。
* 中力首五个交易日的上下限都是 0，比例都是 999；第六日才出现正常限价。
  已知 IPO 场景与该特殊表示对应，但供应商未提供明确 sentinel/version 契约。
  不能把 0 当真实涨停价，也不能把 999 当真实百分比。

茅台 2024-06-18 原始收盘 1521.50；06-19 的 PRECLOSE=1490.62。
[上交所公告](https://www.sse.com.cn/assortment/options/mnjyzxxx/c/c_20240618_10758925.shtml)
可核实该标的除息日；该公告中的模拟期权数据没有用于行情验证。
这证明该日不能用 yesterday raw close 代替限价参考值，但尚未验证所有送转、配股及特殊参考价场景。

目前不需要因为“没有字段”立即启动 Rule Reconstruction Engine：字段实际存在。
若后续决定重建或校验，则至少需要历史证券/板块/风险警示有效期、IPO 日期及无价格限制阶段、
交易所分时期规则、最小报价单位、公司行动和正式参考价。仅以 pct_chg 判断不可接受。

# H. Adjustment Factor Findings

两只证券因子均为 float64 的 daily-dense 宽表，8,730 行、2 列，日期唯一有序，
所有值有限且为正，日期 hash 与本次沪市完整日历一致。
平安银行在 2024-06-14 至 06-24 窗口因子不变；茅台在 06-19 除息日由 7.857710
变为 8.020492。另从同一已下载列检查 2015 年送股边界，未新增网络下载，见 factor_validation.json。

接口没有 end-date 参数，不能真实请求“当年截至 T 保存的因子版本”。
两次强制当前刷新 hash 相同，只证明本次短时间观察一致，不能证明未来事件不会修订旧值。
不同 anchor 会改变归一化价格，不能把归一化变动误认为原始因子修订。
因子日期延伸至个别证券上市前，不可用因子存在性反推证券当时已上市。

仅 factor schema 标 VERIFIED；factor revision/PIT 仍 UNVERIFIED，未更改研究资格默认限制。

# I. Session / Error Findings

两轮均成功 login，各轮内部复用同一 provider。首次/重复日线请求结果相同。
不存在证券触发 TypeError，武钢无 bar 窗口也触发同类异常；异常原文没有写入 artifact。
颠倒日期请求返回空 dict，SDK 并未以一个可靠的参数错误分类替代空结果。
诊断层不自动 retry，不能把 SDK 内部行为假定为零 retry。

首轮预算终止与供应商异常分别记录，没有把前者归咎为服务不支持因子。
logout 因已有 Windows native 退出风险未调用，状态 UNVERIFIED；未故意制造断网或系统故障。
本次没有证实所有连接异常码、自动重连或安全 logout。

# J. Stage 0 Regression Re-run

前置：147 passed。最终：**162 passed，0 failed，0 skipped，142.95 秒**。
原有 147 项全部保留；新增 15 项覆盖 live-capture 离线变异、样本质量、公告冲突保留、
因子/PIT 不混淆、输出白名单和采样预算。
测试中的 source conflict 是预期 qualification finding，不是通过改 expected 隐藏失败。

```powershell
$env:PYTHONPATH = "$PWD;$PWD\.test_tools"
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -B scripts/analyze_gate_a_evidence.py
python -B -m pytest -q -x --tb=short --basetemp=.test_tmp/gate_a_final_01 --junitxml=.test_tmp/gate_a_final_results.xml
```

没有修改 Stage 0 production provider 或业务代码，因此没有 Stage 0 bugfix。
新增脚本不在 pytest 中登录。PDF 读取辅助库 pypdf 6.19.0 仅安装在 gitignored `.test_tools`，
未修改全局环境、项目依赖或 yh/RSI。

# K. Open Questions

1. 提供该 SDK 版本 A 股 Kline 的 volume 单位和 amount 币种/量级正式说明。
2. 澄清康美 07-04 的 IS_ST_SEC、07-03 的比例字段，及状态生效日期/修订政策。
3. 澄清 stock_basic 的 IS_LISTED enum，历史板块/代码变更及 Universe 覆盖范围。
4. 澄清 IPO 0/999 标志、正式历史限价来源、舍入与规则版本、公司行动 reference 口径。
5. 提供 factor/status/bar 的发布、修订、历史版本与 PIT 证据；无法提供时明确研究限制。
6. 补充合法零成交量、半日/特殊交易、状态缺失与安全 logout 的受控样本。

没有联络供应商或向第三方发送数据；这些是明确的后续证据需求。

# L. Stage 1 Data Path

**PATH B — Hybrid Data Layer（条件性方向，尚未放行实施）。**

AmazingData 原生日历、历史名单、bars、因子与状态/限价字段有真实可用基础，
因此当前证据不支持整体弃用而直接选择 PATH C。
但历史 ST 存在已复现冲突，且单位、规则特殊值、历史版本/PIT 尚未确认，不能选择 PATH A。
混合方案需独立、按生效日期维护的风险警示/状态证据与交易所规则/公司行动校验，
必要时采用其他历史来源；先补足单位证明，才能决定正式标准化映射。

# M. Gate Verdict

**STOP**

本次 qualification 已执行并给出可审计矩阵，但正式 Stage 1 数据设计的关键输入仍未闭合：
Kline 单位无明确证据、历史 ST 生效日实测失败、相关限价/状态特殊语义尚待澄清。
测试通过仅证明诊断和 Stage 0 未回归，不等于供应商数据能力全部通过。
未开始 Automatic Universe、Limit-Up Engine 或任何 Stage 1 策略实现。

## Evidence / scope / protection

主要证据：[artifact 目录](D:/ej/材料/codex/rss/artifacts/stage1_gate_a)、
[离线检查](D:/ej/材料/codex/rss/artifacts/stage1_gate_a/offline_checks.json)、
[来源记录](D:/ej/材料/codex/rss/artifacts/stage1_gate_a/source_evidence.json)。
本地 SDK 手册 SHA-256 为 `8d2f671828140e1e16007beef218dc92d34bc81b9288afdcea8f6814cbbd8cd9`。

本次只新增 qualification 脚本、测试、脱敏证据和报告；没有修改策略源码或冻结 baseline。
下列用户文件的开始/结束 hash 相同，且不纳入本次提交：

| 保护文件 | SHA-256 |
|---|---|
| inputs/three_board_daily_input.xlsx | 38DABDC991D26D4D1B5873BFDCBFB5DD29DB4671FA1760C20D932D3D17D9B9FE |
| inputs/three_board_daily_input_2026_backtest.xlsx | 282C4ABDBB5901F5E3EE33512E7256887C1E92AE129662F631397FBD6943CF22 |
| 手工统计.xlsx | 2FDF23576A7D220E994CF74143236A0B3DB34E9E495F769927D0A65C994F8277 |
