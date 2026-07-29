# three_board_rsi_entry v0.1

## 1. 项目目的与边界

本项目在每日收盘后读取人工维护的“三连板股票”Excel，获取日线行情，计算
RSI 和 MA5，并逐交易日重放候选准入、MD_1 与 MD_2。每次运行都从人工输入
历史和行情重新计算，不保存可变业务状态。

本版本不自动识别涨停或连板数，不扫描全市场，不包含实时/分钟行情、GUI、
Web 服务、数据库、消息通知、仓位管理、止盈止损、交易执行或资金曲线回测，
也不与任何 `rsi_exit` 业务模块合并。

## 2. 配置

默认配置位于 `config/three_board_rsi_entry.json`：

```json
{
  "rsi_period": 14,
  "candidate_rsi_threshold": 70.0,
  "md2_rsi_floor": 60.0,
  "md2_max_below70_days": 9,
  "candidate_max_observation_days": 20,
  "warmup_trading_days": 150,
  "price_adjustment": "qfq"
}
```

`price_adjustment` 支持 `qfq` 和 `none`。不同复权方式会改变历史价格、MA5
和 RSI，从而导致信号差异。运行摘要、信号明细和 Excel 参数页都会记录实际
使用的方式。不执行自动参数寻优。

## 3. Excel 人工输入

生成模板：

```bash
python -m three_board_rsi_entry.cli init-template \
  --output inputs/three_board_daily_input.xlsx
```

模板包含两个工作表：

- `交易日确认`：`trade_date`、`input_complete`、`note`。分析区间中的每个
  交易日都必须有一行，且 `input_complete=1`。无三连板时也必须确认，可在
  `note` 填“无”。
- `三连板股票`：`trade_date`、`ts_code`、`stock_name`、`board_count`、
  `limit_up_type`、`note`。一只股票继续四板、五板时需要逐日继续填写；断板
  日不填。

同日同代码重复、`board_count<3`、相邻交易日仍出现但板数没有增加 1，都会
使运行失败。代码会去除首尾空格并转为大写；纯数字代码补齐到六位。建议直接
填写 AmazingData 使用的完整代码，例如 `000001.SZ`，避免 Excel 丢失前导零。
程序不会根据价格修改人工板数。

严格模式下，缺少任何交易日确认都会停止并列出日期。`--allow-incomplete`
允许继续，但 `run_summary.json`、`运行检查` 和候选的 `data_complete` 会明确
标记不完整。

## 4. RSI 与 MA5

RSI 实现同花顺/Wilder 递推：

```text
LC = REF(CLOSE, 1)
GAIN = MAX(CLOSE - LC, 0)
CHANGE = ABS(CLOSE - LC)
SMA_t = (X_t + (N - 1) * SMA_(t-1)) / N
RSI = SMA(GAIN, N, 1) / SMA(CHANGE, N, 1) * 100
```

初始化固定为：第一根价格没有 `LC`，RSI 为 NaN；第一次有效价格变化同时作为
GAIN 和 CHANGE 两条递推的种子。后续严格按上式递推。若平滑后的 CHANGE 为
0，则 RSI 返回 NaN，不生成无穷值。停牌或无有效收盘价的行不作为新的递推
观测，下一有效行情继续使用此前状态。

程序在 `start_date` 前请求至少 150 个实际交易日作为预热，仅用来稳定指标；
预热日期不会读取为候选，也不会产生信号。若数据源或上市历史不足，运行检查
会产生明确警告。MA5 是最近 5 个有效股票交易日收盘价的简单平均。

字段名 `rsi14` / `latest_rsi14` 为固定输出协议名称；实际周期以配置和参数页
记录的 `rsi_period` 为准。

## 5. 连板周期与候选准入

同一股票的下一条人工记录若同时满足：

1. 日期是上一条记录后的下一个市场交易日；
2. 板数等于上一日板数加 1；

则归入同一周期。两条记录间至少空一个交易日则新建周期。首次记录可以从四板
或更高开始。稳定 ID 为：

```text
<ts_code>_<sequence_start_date:YYYYMMDD>
```

每个周期第一次满足“人工板数至少 3 且当日 RSI 严格大于 70”时准入，记录
`qualified_date` 与 `qualified_board_count`。RSI 恰好等于 70 不准入。
同一周期随后继续更新 `last_limit_up_date` 和 `max_board_count`。从未满足的
周期保留为 `NOT_QUALIFIED`。

## 6. 观察期

只有最后连板日之后才评估 MD_1/MD_2。最后连板日为第 0 日，下一市场交易日
为第 1 日，第 20 日仍允许触发，第 21 日开始到期。停牌日仍属于市场观察日，
但没有有效价格/指标时不评估触及或突破；MD_2 的低于 70 天数只累计有有效
RSI 的股票交易观测。

每日处理顺序固定为：

1. 读取当天人工记录；
2. 先延长既有连板周期或建立新周期；
3. 更新当天已知的最后连板日；
4. 再评估观察期信号。

因此，三板后的次日即使价格触及 MA5，只要当天 Excel 已记录继续四板，就不会
误判为断板后的买点。

## 7. MD_1

MD_1 要求周期已准入，日期是最后连板后的第 1～20 个交易日，从准入日到信号
日 RSI 从未低于 70，且最后连板后第一次 `LOW <= MA5` 的当天
`CLOSE >= MA5`。

`first_ma5_touch_date` 只记录真正的第一次触及。若该日收盘没有站回 MA5，
结果永久为 `FAILED_FIRST_TOUCH`，后续第二次、第三次触及不会重试。首次触及
前（或触及当天）RSI 已低于 70，结果为 `LOST_RSI70_BEFORE_TOUCH`。每周期
最多一个 MD_1。

## 8. MD_2

MD_2 要求最后连板后出现一次从 `RSI>=70` 到 `RSI<70` 的连续调整。程序在
本次连续调整中逐个有效股票交易观测计数，维护：

- `below70_start_date`
- `below70_trading_days`
- `below70_min_rsi`

第 1～9 个低于 70 的交易观测均须 `RSI>=60`。重新突破严格定义为：

```text
current_RSI >= 70 AND previous_RSI < 70
```

重新突破当天还必须 `CLOSE>=MA5`。任何一次调整中 RSI 低于 60，整个候选周期
的 MD_2 永久为 `RSI_BELOW_60`；连续第 10 个低于 70 的有效交易观测仍未突破，
永久为 `BELOW70_TIMEOUT`。如果突破当天收盘低于 MA5，当天不触发；以后若形成
一段新的连续调整，仍可再次按完整规则评估。每周期最多一个 MD_2。

MD_1 与 MD_2 独立记录。MD_1 触发不会关闭 MD_2，因而允许先出现 MD_1、随后
跌破 70，再出现 MD_2。

## 9. AmazingData 1.1.6

在线适配层复用相邻 RSI 项目已经审计的
`yh_quant_shape.data_provider.AmazingDataProvider`，不在本项目中另行猜测 SDK
接口。复用链路为：

- `AmazingDataProvider.login`
- `AmazingDataProvider.get_trade_calendar`
- `AmazingDataProvider.get_daily_bars`
- `AmazingDataProvider.base.get_backward_factor`

legacy provider 内部使用 SDK 的 `Period.day.value` 请求日线，并将结果统一为
`date/open/high/low/close/volume/amount`。本项目继续校验日期、重复行、OHLC、
成交量和成交额，不把异常改写为空数据。程序不包含 API 密钥或账号。沿用参考
项目的环境变量：

```powershell
$env:AMAZINGDATA_USERNAME = "..."
$env:AMAZINGDATA_PASSWORD = "..."
$env:AMAZINGDATA_IP = "..."
$env:AMAZINGDATA_PORT = "..."
# 可选；未传 CLI 参数时也会自动检查相邻 yh 项目
$env:AMAZINGDATA_LEGACY_PROVIDER_ROOT = "path/to/yh"
```

也可在 CLI 使用 `--legacy-provider-root path/to/yh`。前复权沿用参考项目的
`get_backward_factor`，只向前填充至已有行情日，并按查询终点对应的最后因子
归一化：

```text
qfq_price(date) = raw_price(date) * factor(date) / factor(as_of)
```

因子缺少最早行情覆盖时直接报错，不使用未来因子反向填补。只调整 OHLC，成交额
不复权。缓存位于 `--cache-dir`：原始日线按代码保存为 CSV，覆盖文件记录已请求
交易日，缺口只补请求缺失区间；API 未返回的日期不伪造停牌价格行。复权因子使用
参考实现的本地因子缓存。更换 `as_of` 时会重新按对应终点归一化，不把未来复权
基准固化进原始日线缓存。`--force-refresh` 会重新请求分析所需区间和因子。

AmazingData 1.1.6 的 pyc-only operator 模块存在 Numba `cache=True` 兼容问题。
默认启用参考项目已验证的 no-JIT 装饰器兼容层；仅在已确认本机 SDK 可直接导入时
使用 `--no-numba-compat`。SDK 因子缓存还需要 PyTables，可通过
`python -m pip install -e ".[amazingdata]"` 安装本项目侧依赖。

本项目的联网真实调用仍需要用户提供有效账号、网络、服务地址和权限；离线测试
不会声称验证这些外部条件。

## 10. CLI

真实 AmazingData：

```bash
python -m three_board_rsi_entry.cli run \
  --input inputs/three_board_daily_input.xlsx \
  --start-date 2025-01-01 \
  --as-of 2026-07-29 \
  --output-dir outputs/2026-07-29 \
  --legacy-provider-root ../yh
```

指定配置、允许不完整输入或强制刷新：

```bash
python -m three_board_rsi_entry.cli run \
  --input inputs/three_board_daily_input.xlsx \
  --start-date 2025-01-01 \
  --as-of 2026-07-29 \
  --output-dir outputs/2026-07-29 \
  --config config/three_board_rsi_entry.json \
  --allow-incomplete \
  --force-refresh
```

离线 CSV 用于测试或可重复研究。CSV 至少包括
`trade_date,ts_code,open,high,low,close,amount,suspended`，可增加
`price_adjustment` 声明其价格模式：

```bash
python -m three_board_rsi_entry.cli run \
  --input examples/smoke_input.xlsx \
  --market-data-csv examples/smoke_market_data.csv \
  --start-date 2025-08-01 \
  --as-of 2025-08-08 \
  --output-dir outputs/smoke
```

## 11. 输出

`candidate_cycles.csv` 每周期一行，包含稳定周期 ID、代码/名称、周期起点、初始
板数、准入日期/板数、最后连板日、最高板数、当前阶段、观察天数、最新价格与
指标、首次 MA5 触及、MD_1 结果、低于 70 调整详情、MD_2 结果、到期日、无效
原因和数据完整性。

`signals.csv` 每个 MD_1/MD_2 一行，包含信号日、周期/证券信息、准入和连板
信息、观察日、当日与前一 RSI、OHLC、MA5、低于 70 天数/最低 RSI、复权方式
及运行 `as_of`。`signal_type` 只会是 `MD_1` 或 `MD_2`。

`candidate_status.xlsx` 包含：

- `当前候选`
- `历史信号`
- `未准入连板`
- `运行检查`
- `参数`

`run_summary.json` 记录运行日期、起始日期、复权方式、RSI 周期、输入文件及其
SHA-256、周期/准入/信号数量、缺失确认日期和警告。

## 12. 无未来函数重放

`as_of` 是硬截止点。Excel 在该日期之后的行先被丢弃，再做重复、板数和周期
校验；行情和交易日历也只请求到 `as_of`。引擎按交易日顺序推进，不会先查看
完整未来周期后倒推 `last_limit_up_date`。相同输入、配置、行情和 `as_of`
会产生相同的 CSV/JSON 内容和相同的 Excel 单元格结果。

历史验证建议分别运行不同截止日：

```bash
python -m three_board_rsi_entry.cli run \
  --input inputs/three_board_daily_input.xlsx \
  --start-date 2025-01-01 \
  --as-of 2025-06-30 \
  --output-dir outputs/replay/2025-06-30
```

不要用后续补录的文件去推断当时未完成人工确认的日期；严格模式会阻止这种静默
缺口，`--allow-incomplete` 仅用于明确接受不完整数据的研究场景。

## 13. 测试

所有策略测试使用人工构造的确定性行情，不访问 AmazingData：

```bash
pytest -q
```

若环境尚未安装开发依赖：

```bash
python -m pip install -e ".[test]"
pytest -q
```

## 14. 已知限制

- 真实 AmazingData 调用依赖外部账号、权限、地址、网络和 SDK 运行环境。
- 离线 CSV 的行情必须已经采用其声明的复权方式，程序不会再次复权。
- 停牌日计入 20 个市场观察日，但没有有效指标时不产生信号。
- 新上市证券可能没有完整 150 根有效预热行情；程序警告但仍保留审计结果。
- 不验证人工填写的股票是否真实涨停，也不自动修正板数。
- 本项目只验证入场信号，不评价任何交易策略的收益或风险。
