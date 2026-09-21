# Stage 0 frozen references

原始代码身份：`fb09873473188a8671d2cff0132b9d932e9c6cb6`。
首次比较前固定 tolerance：abs `1e-10`，rel `1e-12`。

| 文件 | 身份 / 用途 |
|---|---|
| `reference.json` | 修改 runtime 前冻结的 12 组语义 expected、历史 expected、config、日历及来源身份 |
| `checksums.json` | 主 reference 与历史文件的 SHA-256；测试只核验，不更新 |
| `head_input.xlsx` | 原始 Git HEAD 输入，缺有效确认/连板信息，不是合格回测 |
| `historical_input.xlsx` | 从当时用户输入显式选取 000020.SZ，身份独立于 HEAD |
| `historical_raw.csv` | 原有 raw cache 中该证券的数据；缺 volume，不能通过新正式 schema |
| `historical_factors.csv` | 该证券固定因子；源 HDF 身份记录于 reference |
| `pipeline_reference.json` | 原始 HEAD 临时副本上运行固定 examples 所得完整管线 expected 与输入 hash |
| `pipeline_reference.sha256` | 补充 reference 的完整性校验 |

HEAD 输入 SHA-256：
`83978f770fa8e2c718acdf576d880d1ebcc3b18eb5100954712e619a32debf34`。
当时用户修改输入 SHA-256：
`38dabdc991d26d4d1b5873bfdcbfb5dd29db4671fa1760c20d932d3d17d9b9fe`。

历史 fixture 只有 1 个 cycle、0 个信号，且日历来自行情观察日期、缺历史状态证据。
它验证旧数据可复现及新质量规则拒绝，不承担合格信号的唯一验证责任。
合格语义依靠 12 组冻结场景及固定 examples 的 2 个信号 / 2 个事件全字段对照。

不得为消除差异重新生成 expected、扩大 tolerance 或将它们覆盖为当前输出。
完整解释见 `docs/research_stage0.md`。
