# TREND_EXIT_V0_5_BAOSTOCK

本轮执行了真实BaoStock连接与两组Smoke；4次TCP连接成功，4次login发送后均15秒接收超时，SDK返回10002007、响应0字节。药明与昆仑各完成初始尝试及一次失败重试，没有有效登录会话；实际联网日线/日历调用0。按任务第4节停止完整采集，五粮液未发起完整请求。三股新行情0、新真实Snapshot0、原案例未恢复。

BaoStock 0.9.4官方PyPI wheel保存于 `artifacts/v0_5_packages/`，SHA256为 `0bf71c6069ab5890ff3596632f9c3f8f1fbc6bfcac582c2f9d6a5c11ab2cfaf8`。`artifacts/v0_5_env/` 为独立venv，仅安装baostock、不安装或升级依赖，读取基础环境的pandas 3.0.1。SDK的请求构造、paging和socket实现经过预检；失败后的本地包序列化检查使用内存sink，没有新增网络请求。源码、安装文件、实际错误与时刻均有指纹。

当前最小命令是离线重放已发生的真实失败，绝不重试网络：

```powershell
$env:PYTHONPATH = ((Resolve-Path .).Path + ';' + (Resolve-Path .cache/trend_exit_runtime).Path)
$env:PYTHONIOENCODING = 'utf-8'
$rssBaoPython = '.\trend_exit_research\artifacts\v0_5_env\Scripts\python.exe'
& $rssBaoPython -m trend_exit_research.src.v0_5
& $rssBaoPython -m trend_exit_research.src.v0_5 --out trend_exit_research/artifacts/v0_5_replay_repeat
& $rssBaoPython -m pytest trend_exit_research/tests/test_v0_5.py -q --basetemp=.test_tmp/trend_exit_v05_fresh
```

新主机先取得本地真实回执及原Snapshot才能重放，不能把缺少artifact视作成功空数据。生成acquisition report、data qualification和case results三份不可覆盖结果。完整测试/保护回执在reports/v0_5_validation_receipt.json；真实Smoke摘要、包/代码身份也有跟踪副本，详细wire/attempt文件仅本地保留。

数据验证保留原始供应商字段，检查代码日期、唯一性、数值、OHLC、preclose/pctChg一致性、交易/停牌和单位声明。停牌的参考价与零量额不是可交易观察。因果preclose差异仅标CORPORATE_ACTION_EVIDENCE_REQUIRED，缺口/边界阻断可比较片段，不伪造调整因子；新增数据为0，尚未应用于真实指标或交易时间线。V0.4 Snapshot契约的字段保留、不可变重放和迟到标记只在合成对象上验证。

没有提供自动在线重跑命令。原两组Smoke已用完本轮每请求2次尝试，当前采集目录有结束标记，CaptureSession拒绝重复初始化；还限制同请求重发、单调分页、全局预算和socket/查询耗时。诊断登录回包问题后若重试这些同一请求，应取得新的尝试授权，再评估真实Smoke通过后的完整采集。不能把连接失败外推为BaoStock无数据或策略失效。16:30自动检查仍只读缓存，无新联网权限；药明初始ID与日期保持不变。
