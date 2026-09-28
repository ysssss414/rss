# three_board_rsi_entry

用于验证“3 连板及以上强势股的 RSI 二次买点”的独立小型项目。它只研究
MD_1、MD_2 入场信号，不包含自动连板识别、交易执行、仓位、止盈止损或资金
回测。

快速开始：

```bash
python -m three_board_rsi_entry.cli init-template \
  --output inputs/three_board_daily_input.xlsx

python -m three_board_rsi_entry.cli run \
  --input inputs/three_board_daily_input.xlsx \
  --start-date 2025-01-01 \
  --as-of 2026-07-29 \
  --output-dir outputs/2026-07-29
```

完整规则、AmazingData 环境变量、离线运行和输出说明见
[`docs/three_board_rsi_entry_v0.1.md`](docs/three_board_rsi_entry_v0.1.md)。

历史 MD_1/MD_2 事件回测的下一可交易日开盘、固定持有期、MFE/MAE、输出和
CLI 说明见
[`docs/three_board_rsi_entry_backtest_v0.2.md`](docs/three_board_rsi_entry_backtest_v0.2.md)。

Stage 0 的数据契约、不可变 snapshot、研究入口、manifest 与冻结回归说明见
[`docs/research_stage0.md`](docs/research_stage0.md)。

Stage 1 的 D8 结果路径归一化、离线冻结研究输入与
`REAL_RESEARCH_SNAPSHOT_V1` 使用说明见
[`docs/stage1_d8_and_research_snapshot.md`](docs/stage1_d8_and_research_snapshot.md)。

Stage 1 当前正式信号研究入口为 `python -m scripts.run_real_strategy_smoke_v1`：
使用冻结快照、V3、Observation V2 和 T 日运营资格筛选，输出 Observation、
生命周期与 Entry（不读取 D8 或未来收益）。证据降级、待复核清单及复跑审计见
[`docs/stage1_operational_trigger_qualification_and_real_smoke.md`](docs/stage1_operational_trigger_qualification_and_real_smoke.md)。
