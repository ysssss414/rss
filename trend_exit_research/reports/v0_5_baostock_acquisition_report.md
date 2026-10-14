# TREND_EXIT_V0_5 BaoStock真实采集

**三只股票均未取得真实历史日线。** 昆仑0条、五粮液0条、药明0条。药明与昆仑真实Smoke各进行初始尝试和一次失败重试，均在登录响应阶段失败；五粮液完整采集按任务第4节未启动。结果不能解释为股票无行情。

| 股票 | 实际Smoke尝试 | 实际日线API调用 | 新行情 | 结果 |
| --- | --- | --- | --- | --- |
| 昆仑万维 300418.SZ | 2 | 0 | 0 | SMOKE_LOGIN_FAILED |
| 五粮液 000858.SZ | 0 | 0 | 0 | NOT_ATTEMPTED_SMOKE_FAILED |
| 药明康德 603259.SH | 2 | 0 | 0 | SMOKE_LOGIN_FAILED |

4次TCP连接至public-api.baostock.com:10030均成功；4个login报文均已发送，每次等待15秒且响应字节为0，BaoStock返回10002007（网络接收错误）。受控尝试总耗时61.296秒。实际login调用4，history API调用0、calendar API调用0、数据分页请求0、logout API调用0（没有成功会话）。完整采集/日历没有进入。本轮未追加重试，没有获取全市场、因子或其它证券日期。供应商服务故障、线路限制、协议端兼容性等原因无法仅据这些回执确定，未证明BaoStock没有这些股票数据。

## 来源、环境与请求约束

使用[官方PyPI BaoStock 0.9.4](https://pypi.org/project/baostock/0.9.4/)，在研究artifacts/v0_5_env安装；venv只新增baostock，复用基础环境只读pandas 3.0.1，没有升级RSS依赖。包SHA256为 `0bf71c6069ab5890ff3596632f9c3f8f1fbc6bfcac582c2f9d6a5c11ab2cfaf8`。实际Python、包成员、执行时源码和配置身份见data qualification JSON。

检查安装包的history、resultset、trade dates和socket/login实现：数据请求在发送前包含指定code、fields、start/end、frequency=d、adjustflag=3；翻页保留这些参数，SDK未见自动查询重试。只允许三证券各自授权日期及四个必要API。上限为16次高层尝试、每请求2尝试、每尝试3页/1200行、数据报文48次、每socket 15秒、每查询90秒、整次900秒、每响应8MiB；不会要求证明第三方服务器内部实现。SDK原recv在peer关闭时可空读循环，包装显式拒绝空读并加超时。安装包实际序列化还以纯内存sink验证，没有额外联网。

供应商[API文档](https://github.com/zxygithub/baostock/blob/master/docs/pythonAPI.md)声明价格/amount为CNY、volume为股、turn为%、tradestatus标记交易/停牌。它们是供应商口径声明，未取得真实行作此次单位校验。停牌保留参考价和零量额也不能成为交易观察；潜在preclose/前日raw_close差异只标CORPORATE_ACTION_EVIDENCE_REQUIRED，不推断具体行动或伪造因子。未验证事件不跨段计算。

## 实证与Snapshot结果

真实有效行0，没有新Snapshot/manifest，不创建空成功Snapshot。因未过Smoke，没有连续MA、三日确认、潜在执行价、未来5/10/20/40走势或重新准入结果。无法做跨源边界核验；原AmazingData证据保留，没有额外下载2024年重叠数据。已有V0.4 raw Snapshot契约的供应商字段保留、哈希重放和迟到标记在聚焦测试中检查，合成对象不能充当真实采集。

药明原WUXI_FORWARD_20261013_V0_1及初始回执未改。仍只有原缓存截至2026-09-23，backfill_retrieved_at=null。连接尝试的检索时间不等同于捕获了10月13日行情，不声称之后的前瞻验证；16:30自动检查仍只读缓存。TGW阻断和V0.1—V0.4冻结结果保持不变。

真实证据见artifacts/v0_5_live：smoke_receipt.json、smoke_results.json、4份attempts、4份wire响应记录、acquisition_identity.json。执行后仅增加了防止已结束采集重跑、每请求不可重复、分页单调性和错误日志的保护；未用更新代码重跑网络。JSON同时保留执行时和最终代码哈希。

下一阶段需先定位此执行主机到BaoStock公共端口的登录回包问题；若要重试本轮已耗尽的同一Smoke请求，需明确新的尝试授权。通信恢复且至少一组真实Smoke通过后，再串行取得3个完整日线及缺少的有界calendar，成功者以V0.4契约独立冻结。不能用其它股票、全集请求或无限重试绕过当前失败。测试、Git范围及旧成果校验见reports/v0_5_validation_receipt.json；最小离线回放命令为 `python -m trend_exit_research.src.v0_5`。

判定：BAOSTOCK_LIVE_ACQUISITION_STOP；RAW_DATA_UNQUALIFIED；CONTINUOUS_TREND_INDICATORS_UNQUALIFIED；CASE_ALIGNMENT_PARTIAL；STRATEGY_EFFECTIVENESS_NOT_YET_ESTABLISHED。
