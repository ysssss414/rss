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
