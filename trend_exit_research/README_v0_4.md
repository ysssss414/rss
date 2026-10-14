# TREND_EXIT_V0_4

本轮有明确的三证券、日期和数据集授权，仍在实际联网前停止：AmazingData 1.1.6的Python层请求有证券和日期限制，但TGW 1.0.8.7原生初始化/查询范围及内部重试没有可核验证据。`STOP_NETWORK_SCOPE_UNQUALIFIED`不代表接口已证实会全市场扇出，也不代表SDK不可用；它表示本次要求的底层范围资格没有成立。

当前交付是可复现的离线预检、raw证据快照契约、fake SDK测试和未覆盖案例回执，没有已合格的live backend；没有新真实Snapshot。配置在 `config/v0_4.json`，原退出规则、V0.1—V0.3和Stage 1不变。未取得新数据的案例不计算均线或收益。不同证券的本地证据以内容哈希独立存放，失败不会修改已有对象；探索级raw字段资格与严格PIT/执行资格分开。

在仓库根目录运行：

```powershell
$env:PYTHONPATH = (Resolve-Path .cache/trend_exit_runtime).Path
$env:PYTHONIOENCODING = 'utf-8'
$rssResearchPython = 'C:/Users/cjzs0/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $rssResearchPython -m trend_exit_research.src.v0_4 --out trend_exit_research/artifacts/v0_4_final
& $rssResearchPython trend_exit_research/tests/verify_v0_4_outputs.py
& $rssResearchPython -m trend_exit_research.src.v0_4 --out trend_exit_research/artifacts/v0_4_repeat
& $rssResearchPython -m pytest trend_exit_research/tests tests/test_factor_pit.py tests/test_trade_lifecycle.py tests/test_research_cache.py tests/test_d8_snapshot.py tests/test_stage1_foundation.py -q --basetemp=.test_tmp/trend_exit_v04_fresh
```

预检默认读取相邻 `yh/AmazingData_sdk/AmazingData/AmazingData-1.1.6-cp312-none-any.whl` 和 `yh/tgw-1.0.8.7-py3-none-any.whl`，可通过 `--sdk-wheel`、`--tgw-wheel`指定本地文件；必须使用匹配Python 3.12字节码的解释器。无需安装或导入SDK，也不读取账号配置。独立验证脚本只将选中的vendor函数code object与完全离线fake TGW绑定，检查参数实际构造；它不执行模块、原生库或登录组件。此检查不能证明原生网络范围。

输出四个不可覆盖文件：SDK范围审计JSON、数据资格JSON、案例Markdown和run receipt。保护清单首次记录所有V0.3 HEAD已有跟踪文件（含用户已修改的Excel）和既有本地CSV/JSON/MD/Parquet结果，后续运行逐字节核对。原Snapshot通过RSS完整加载验证。若源码改变，应选新的V0.4输出目录，避免改写已经冻结的结果。

`capture_v0_4.freeze_capture`接收规范raw OHLCV/amount及状态，复用RSS数据验证、canonical JSON及SnapshotStore不可变写入，创建 `<root>/<security_id>/<sha256>/manifest.json`。manifest嵌入raw数据、状态、可用因子/事件证据、真实检索时间、实际范围、行数、指纹、代码/配置身份和资格。`load_capture`核对manifest和数据集哈希。数据保持raw；不设置伪造的历史可用时刻，不生成连续复权价格或严格成交资格。没有可信单位证据或状态/行情缺口时保留UNQUALIFIED。当前真实创建调用数为0；测试对象只在pytest临时目录，标为SYNTHETIC_TEST_ONLY。

这不是旧REAL_RESEARCH_SNAPSHOT_V1加载契约的替代，也暂未接入其要求D8、完整历史状态及PIT证据的前瞻观察器。16:30自动检查配置和联网权限不变，缺数据仍安静。后续需先取得与现有SDK/TGW版本匹配的原生初始化、查询及重试边界证据，关闭/计量额外重试、验证calendar来源和raw单位，再在本次证券/日期预算内完成药明最小试调用；缺少严格交易字段本身不阻止合格的raw探索研究。

最终结果和哈希见 `reports/v0_4_acquisition_report.md`、`v0_4_sdk_scope_audit.json`、`v0_4_data_qualification.json`、`v0_4_validation_receipt.json`、`v0_4_case_results.md`。报告的测试成功仅代表离线工程与契约测试，不能等同于真实采集成功或退出策略有效。
