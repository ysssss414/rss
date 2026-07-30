# three_board_rsi_entry 历史事件回测 v0.2

## 1. 目的与边界

本模块消费 v0.1.0 逐日重放生成的 `signals.csv`，评价 MD_1、MD_2 信号在未来
1、3、5、10 个有效股票交易日的收益、MFE 和 MAE。它不会反向修改候选周期或
信号，也不包含止盈止损、仓位、手续费、滑点、资金曲线、参数优化或自动涨停识别。

## 2. 入场口径

MD_1 和 MD_2 都只能在信号日收盘后确认，因此主要入场口径固定为：

```text
NEXT_TRADABLE_OPEN
```

从信号日之后的市场交易日开始，寻找第一条非停牌且 open/high/low/close 均有效
的股票行情。该日为 `entry_date`，开盘价为 `entry_price`。下一市场日即可交易
时 `entry_delay_market_days=1`；停牌时继续寻找复牌后的首个有效开盘。若截止
`as_of_date` 仍无可交易行情，则为 `NO_ENTRY_DATA`。

`signal_close` 仅用于审计和计算
`signal_close_to_entry_gap=entry_price/signal_close-1`，不是主要可执行入场价，
也不会用作事件收益的分母。本模块不计算以信号收盘价入场的理论收益；如未来增加，
必须明确标记为 `THEORETICAL_SIGNAL_CLOSE`。

若市场日缺少股票行且没有明确停牌行，或 OHLC 无效，则标记
`MARKET_DATA_INCOMPLETE` 并产生警告，不以前值填充，也不把缺失日当作停牌。

## 3. 持有期与收益

入场日为第 0 日。`holding_period=N` 的退出日是入场日之后第 N 个非停牌且
OHLC 有效的股票交易日：

```text
return_N = exit_close_N / entry_open - 1
```

所以 1 日持有不是入场当天收盘退出，而是至少跨过一个完整交易间隔。默认持有期
为 1、3、5、10 日。较短周期可以完成而较长周期为
`INSUFFICIENT_FORWARD_DATA`；任何窗口若跨过未解释的行情缺失，则该窗口为
`MARKET_DATA_INCOMPLETE`，不进入完成样本统计。

## 4. MFE 与 MAE

对每个完整持有期，窗口包含入场日和退出日：

```text
MFE_N = 窗口最高 high / entry_price - 1
MAE_N = 窗口最低 low / entry_price - 1
```

结果不截断，同时记录 `max_high_date_N` 与 `min_low_date_N`。MFE 可以为负，
MAE 也可以为正。所有价格与信号计算使用同一 `price_adjustment`。

## 5. 双信号与首信号

`backtest_events.csv` 保留每个 MD_1、MD_2 信号。同一周期先后出现两个信号时
保留两行，不实现加仓或资金曲线。

`first_signal_events.csv` 每个 `candidate_cycle_id` 只保留最早信号；同一天同时
出现 MD_1、MD_2 时固定优先 MD_1。该文件用于评价“每周期只交易一次”。

## 6. 统计

`backtest_summary.xlsx` 的 `信号汇总` 分别统计全部信号、MD_1、MD_2 和每周期
首信号。1、3、5、10 日均输出完成数、缺失数、胜率、均值、中位数、四分位数、
极值、MFE/MAE、平均正负收益、盈亏比及超过正负 5% 的比例。

胜率使用 `return_N>0`；盈亏比为平均正收益除以平均负收益绝对值。没有正样本
或负样本时盈亏比为空，不除零。未完成样本不进入对应期限的收益统计。

`分层统计` 仅按信号类型、准入板数、最高板数、观察日和信号年份分组。板数分为
3 板、4 板、5 板及以上；观察日分为 1—3、4—7、8—12、13—20 日。

## 7. 输出

`backtest` 命令保留 v0.1.0 的四个输出，并新增：

- `backtest_events.csv`：每信号一行的事件明细；
- `first_signal_events.csv`：每周期首信号明细；
- `backtest_summary.xlsx`：事件、首信号、汇总、分层、未完成样本和参数；
- `backtest_run_summary.json`：样本数量、各期限完成数、规则和警告。

Excel 工作表为：

```text
事件明细
首信号明细
信号汇总
分层统计
未完成样本
参数
```

`未完成样本` 会列出无入场、远期数据不足、行情缺失和停牌导致的入场延迟。

## 8. 无未来函数

- 原信号仍只使用信号日及以前数据；
- 入场只能来自信号日之后；
- 行情、入场、收益、MFE 和 MAE 均硬截断于 `as_of_date`；
- 较早截止日不会看到以后才完成的持有期；
- 不足数据保持为空，不自动延长截止日；
- 相同输入、配置和截止日产生确定性 CSV、JSON 和 Excel；
- 回测模块复制输入表，不改变 `ReplayResult` 或原 `signals.csv`。

## 9. 停牌、数据缺失与日线成交限制

明确停牌行不会虚构开盘价，程序继续寻找复牌首日，并记录市场日延迟。未解释的
缺失行或无效 OHLC 属于数据质量问题，不静默跳过。

`entry_open_equals_high_equals_low_equals_close` 仅作为审计字段，不自动删除样本。
`NEXT_TRADABLE_OPEN` 是日线级基准成交假设，不代表一字涨停、瞬间封板、流动性
不足等样本在真实交易中一定能以开盘价成交。

## 10. CLI

离线批量回测：

```bash
python -m three_board_rsi_entry.cli backtest \
  --input examples/backtest_smoke_input.xlsx \
  --market-data-csv examples/backtest_smoke_market_data.csv \
  --start-date 2025-06-30 \
  --as-of 2025-07-14 \
  --output-dir outputs/backtest-smoke-v0.2
```

真实 AmazingData：

```bash
python -m three_board_rsi_entry.cli backtest \
  --input inputs/historical_three_board_input.xlsx \
  --start-date 2024-01-01 \
  --as-of 2026-06-30 \
  --output-dir outputs/backtest_20240101_20260630 \
  --cache-dir .cache/three_board_rsi_entry \
  --legacy-provider-root ../yh
```

`--retry-count`、`--retry-delay-seconds`、`--force-refresh`、
`--allow-incomplete` 和 `--no-numba-compat` 与原命令一致。

## 11. 历史 Excel 批量使用

继续使用现有 Excel 的 `交易日确认` 与 `三连板股票` 工作表，逐日填写目标区间
内所有人工确认的三板及以上股票。运行时先执行完整 v0.1.0 信号重放，再以同一批
前复权行情评价事件。回测结果用于信号质量研究，不应解释为完整可执行交易系统。
