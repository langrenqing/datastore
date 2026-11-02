# datastore

用于获取感兴趣的数据。当前包含一个本机运行的**黄金价格趋势跟踪**工具。

一期（MVP）实现日线数据的采集、存储、指标计算与图表展示，覆盖国内外黄金价格。盘中实时数据与价格提醒留到二期。

## 功能

- **数据采集**：从免费公开接口抓取日线行情，多个数据源按优先级回退，失败自动重试（指数退避）。
- **数据存储**：SQLite 本地入库，按品种和日期范围查询，重复采集自动去重更新。
- **趋势分析**：MA5 / MA20 / MA60 均线、日涨跌幅、20 日年化波动率、基于均线排列的趋势判断。
- **可视化**：价格与均线走势图，附日涨跌幅柱状图与波动率曲线，输出 PNG。
- **本机运行**：纯 Python，无需数据库服务；可用 cron 定时执行。

## 跟踪品种

| 代码 | 品种 | 单位 | 数据源 |
| --- | --- | --- | --- |
| `XAU` | 国际黄金现货 XAU/USD | 美元/盎司 | 新浪财经（全球期货/现货日线） |
| `AU9999` | 上海金 Au99.99 | 元/克 | 上海黄金交易所官网 |
| `AU0` | 沪金期货主力连续 | 元/克 | 新浪财经（国内期货日线） |
| `GC` | COMEX 黄金期货（可选） | 美元/盎司 | 新浪财经，失败回退 Yahoo Finance |

免费数据源无需 API Key，但可能存在延迟、限流或接口调整，详见文末说明。

## 快速开始

要求 Python 3.9+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 一键：初始化数据库 + 增量采集 + 文本报告 + 图表
python -m goldtrack run
```

首次运行会拉取各品种的全部历史日线（XAU 约 2006 年至今），之后默认增量采集最近几天。

## 常用命令

```bash
# 初始化数据库（data/gold.db）
python -m goldtrack init

# 查看品种与数据源
python -m goldtrack sources

# 采集（默认增量；--full 全量）
python -m goldtrack fetch
python -m goldtrack fetch --symbols XAU,AU9999,AU0 --full

# 按时间范围查询
python -m goldtrack query --symbol XAU --start 2026-01-01 --end 2026-11-02
python -m goldtrack query --symbol XAU --limit 10 --json

# 指标（MA5/MA20/MA60、涨跌幅、波动率、趋势）
python -m goldtrack indicators --symbol AU9999 --limit 20
python -m goldtrack indicators --symbol AU9999 --json

# 图表（默认最近 180 个交易日）
python -m goldtrack chart --symbol XAU --days 180
python -m goldtrack chart --symbol AU0 --out output/charts/au0.png

# 最新趋势报告
python -m goldtrack report
```

全局参数 `--db` 可指定数据库路径，`-v` 输出详细日志。

文本报告示例：

```
黄金价格趋势跟踪  数据截至 2026-11-02

品种          最新日期         收盘      日涨跌       MA5       MA20       MA60   20日波动  趋势  样本
XAU 国际黄  2026-11-02   3,902.29    -0.36%  3,910.12  3,880.00  3,800.00    12.34%  多头  5184
...
```

图表示例位于 `output/charts/`：上方为收盘价与 MA5/MA20/MA60，下方为日涨跌幅与 20 日年化波动率。

## 定时采集

`scripts/daily.sh` 已封装「增量采集 + 报告 + 图表」，并将输出写入 `logs/daily.log`。

Linux / macOS cron（每个工作日 08:30 执行，路径换成实际项目路径与 Python 绝对路径）：

```cron
30 8 * * 1-5 /Users/you/datastore/scripts/daily.sh
```

macOS 也可用 launchd 定时调用同一脚本。免费日线数据通常在交易日收盘后更新，建议收盘后或次日早晨执行。

## 指标口径

| 指标 | 口径 |
| --- | --- |
| MA5 / MA20 / MA60 | 收盘价的 5 / 20 / 60 日简单移动平均 |
| 日涨跌幅 | `(收盘 / 前一收盘 - 1) × 100%` |
| 20 日年化波动率 | 近 20 个交易日对数收益率标准差（样本标准差）× `√252` × 100% |
| 趋势 | 收盘 > MA20 > MA60 且 MA5 ≥ MA20 为「多头」；反向为「空头」；其余为「震荡」；均线未形成时为「数据不足」 |

## 目录结构

```
goldtrack/
├── cli.py            # 命令行入口
├── config.py         # 路径与图表参数
├── db.py             # SQLite 存储与查询
├── fetch.py          # 多源采集、重试与增量写入
├── indicators.py     # 均线、涨跌幅、波动率、趋势
├── instruments.py    # 品种与数据源注册表
├── models.py         # Bar / Instrument 等数据模型
├── report.py         # 终端趋势报告
├── chart.py          # Matplotlib 图表
└── sources/          # 新浪财经、上金所、Yahoo 数据源
scripts/daily.sh      # 定时任务脚本
tests/                # pytest 单元测试（全部离线）
```

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

测试不访问网络：数据源解析使用 `tests/fixtures/` 中的固定响应，采集重试/回退使用假数据源。

## 数据源与限制

- 免费接口适合个人研究与趋势观察，不保证实时性或可用性；接口限流、字段变化时可能需要调整解析。
- XAU 为国际现货黄金报价，`AU9999` 为上海金基准价，`AU0` 为沪金主力连续，`GC` 为 COMEX 期货；不同品种的计价单位与交易时段不同，报告与图表按品种分别展示，不做跨品种换算。
- 主力连续合约换月会产生跳空，长期均线分析时需留意。
- 本工具仅用于数据整理与趋势参考，不构成任何投资建议。

## 二期计划

- 盘中 / 实时行情采集与刷新。
- 价格突破阈值提醒（邮件、IM 等）。
- 更多指标与回测、跨品种价差分析。
