# 开源项目筛选与整合决策

2026-11-10；目标为 A股 / ETF、分钟跟踪和日线研究。以下“建议”属于本项目工程判断；库的能力、许可与接口边界来自链接中的官方仓库和文档。仓库 push 时间不等于正式发布日，也不能证明数据服务正常。

## 推荐结论

采用模块组合：先把数据质量、可追溯性和清晰的研究界面做好。免费接口可用于起步与验证；若盘中稳定性要求较高，给授权行情服务保留接口，不能承诺免费源的可用性 SLA。

| 项目 | 能解决的问题 | 推荐使用方式 | 边界与取舍 |
|---|---|---|---|
| [AKShare](https://github.com/akfamily/akshare) | A股、ETF、指数、基本面等数据接口 | 首期免费数据适配层；隔离每种上游及接口 | 源站变动会影响可用性；不是自带长期存储的数据平台 |
| [Tushare SDK](https://github.com/waditu/tushare) / [Pro 文档](https://tushare.pro/document/2) | 标准化日线、因子、交易日历等；另有分钟权限 | 有账号权限后可作为结构化历史主源及独立比对源 | SDK 开源不意味着服务免费；以账号接口权限为准；股票权限不能直接推定覆盖 ETF |
| [efinance](https://github.com/Micro-sheep/efinance) | 股票和基金行情的便捷 Python 接口 | 小样本交叉验证、兼容性备用适配器 | 与 AKShare 东财接口存在共同上游，不能当作独立灾备 |
| [CNEquity](https://github.com/rootSunc/CNEquity) | 自托管历史湖、增量更新、Parquet、溯源和 PIT 设计 | 优先验证历史层候选；通过文件或查询适配器接入 | README 标明 0.x；本轮未运行完整数据湖；PIT 与退市覆盖是项目声明，必须抽样验收 |
| [Polars](https://github.com/pola-rs/polars) | 列式转换、批量清洗、聚合 | 主清洗引擎；接口返回 pandas 时在边界转换 | 不承担调度、交易日历或金融口径判断 |
| [Pandera](https://github.com/unionai-oss/pandera) | 字段类型与数据规则校验，含 Polars 后端 | 数据进入标准层前的质量闸门 | 金融规则仍需自己定义；需固定与 Polars 兼容的版本 |
| [DuckDB](https://github.com/duckdb/duckdb) | 本地分析查询 | 查询 Parquet、生成筛选和比较数据集 | 首期采用单写入进程；不设计为多个采集进程并发写同一数据库 |
| [Lightweight Charts](https://github.com/tradingview/lightweight-charts) | 金融时序图、K线、成交量 | 个股 / ETF 详情主图 | 只绘图，不提供行情；保留 NOTICE 及所需 TradingView 链接 |
| [Apache ECharts](https://github.com/apache/echarts) | 热力、散点、相关性、组合风险图 | 多资产分析图 | 避免与 K线库重复实现同一主图 |
| [TanStack Table](https://github.com/TanStack/table) | 可定制的数据表逻辑 | 自选、筛选、ETF 对比、数据质量表 | 样式自己做；大量行另配虚拟化或服务端分页 |
| [shadcn/ui](https://github.com/shadcn-ui/ui) | 可修改的界面组件基础 | 表单、弹层、菜单和侧边详情 | 用统一设计规范改造；本身不是金融终端 |
| [OpenBB](https://github.com/openbq-org/OpenBB) | 多数据供应商与 API 编排 | 借鉴 Provider 契约；后续全球资产扩展再评估 | 查询时旧仓库 URL 重定向至 openbq-org；当前 LICENSE 为 Apache-2.0，API 识别为 NOASSERTION，不沿用旧版本许可印象 |
| [Qlib](https://github.com/microsoft/qlib) | 量化研究、训练与评估框架 | 第二阶段因子 / 模型研究消费者 | 不是当前 A股分钟数据服务；样例数据不等于最新完整生产数据 |
| [VeighNa / vn.py](https://github.com/vnpy/vnpy) | 交易及事件驱动生态 | 日后有仿真或交易需求再接入 | 现阶段分析工作台不需要交易网关及订单系统 |
| [Dagster](https://github.com/dagster-io/dagster) | 数据任务、分区与可观测性 | 多数据集、多人员阶段升级调度 | 首期一个明确的采集进程及任务状态表即可，避免运维负担过大 |
| [QuantMind](https://github.com/jibiao-ai/QuantMind) | A股产品界面与多源整合思路 | 仅作功能参考 | 快照 stars 较少，未经端到端验证，不把界面中的“可用”直接当作证据 |

BaoStock 作为后续历史交叉校验候选保留。本轮官方站点读取失败，未确认当前接口与覆盖，不列入首期强依赖。

## 最新数据和历史数据的真实边界

1. **ETF 1分钟窗口有限**：[AKShare 官方基金文档](https://akshare.akfamily.xyz/data/fund/fund_public.html)说明 `fund_etf_hist_min_em(period="1")` 仅返回近 5 个交易日且不复权。上线后必须滚动归档；多年日线和多年分钟不能混为一谈。
2. **Tushare 分钟不是普通日线权限的附赠项**：[实时分钟 rt_min](https://tushare.pro/document/2?doc_id=374)为分钟周期闭合数据；[历史分钟 stk_mins](https://tushare.pro/document/2?doc_id=370)文档给出历史起点与分页限制，二者均需独立权限。ETF 需要另外核验其基金接口与授权。
3. **收盘数据并非15:00立即完整**：[daily](https://tushare.pro/document/2?doc_id=27)与[daily_basic](https://tushare.pro/document/2?doc_id=32)的发布时间窗口不同。因此设计17:30初次日终任务，并按完整性重试，而非15:00就把所有表标成最终版本。
4. **开源代码许可与数据使用权限分开记录**：AKShare 的[项目说明](https://github.com/akfamily/akshare)提示研究用途及接口可能移除；CNEquity 也说明不附带数据湖、上游数据有独立许可。共享或对外发布数据前要明确上游允许的使用范围。
5. **图表开源不包含市场数据**：[Lightweight Charts README](https://github.com/tradingview/lightweight-charts#license)要求署名及链接，选型时连同 NOTICE 一起保留。

## 整合路径

- **推荐路线 A：轻量组合。** 自己控制 Provider、标准表、质量规则、API 与前端，复用成熟基础库；开发量更可控，坏数据可定位到具体来源。
- **备选路线 B：CNEquity 历史层 + 自有盘中采集 + 自有前端。** 先在隔离数据目录验证少量股票，核对日线、退市、复权、公告时点和导出契约；通过后可明显减少历史数据工程工作。分钟和 ETF 能力按数据集逐项验收。
- **OpenBB 的整合时机。** 需求扩展到海外市场、宏观和多供应商时再增加 Provider 适配，不为首期 A股界面引入整个生态。不是把几个开源项目直接拼成同一个大应用。

采用 CNEquity 的验收门槛：固定 commit/版本；选 10 只不同板块证券及包含除权、停牌、退市的样本；逐项比较原始源与输出；测试断点续跑和历史修订；确认需要的 ETF 数据集真实存在。未通过前维持候选状态。

## 维护与许可证快照

下表由 GitHub REST API 查询结果生成。日期为 UTC push 日期，stars 仅用于了解社区规模，不作质量评分。原始时间及错误保存在 `github-snapshot.json`。许可证为仓库元数据，正式引入仍以锁定版本的 LICENSE / NOTICE 为准。

| 仓库 | Stars | 最近 push（UTC） | 许可证 |
|---|---:|---|---|
| [akfamily/akshare](https://github.com/akfamily/akshare) | 23275 | 2026-11-09 | MIT |
| [Micro-sheep/efinance](https://github.com/Micro-sheep/efinance) | 4171 | 2026-07-17 | MIT |
| [waditu/tushare](https://github.com/waditu/tushare) | 15488 | 2024-03-13 | BSD-3-Clause |
| [openbq-org/OpenBB](https://github.com/openbq-org/OpenBB) | 75283 | 2026-11-10 | Apache-2.0（已读 LICENSE；API 未识别） |
| [duckdb/duckdb](https://github.com/duckdb/duckdb) | 42952 | 2026-11-09 | MIT |
| [unionai-oss/pandera](https://github.com/unionai-oss/pandera) | 4522 | 2026-11-10 | MIT |
| [tradingview/lightweight-charts](https://github.com/tradingview/lightweight-charts) | 17831 | 2026-11-09 | Apache-2.0 |
| [apache/echarts](https://github.com/apache/echarts) | 67697 | 2026-10-13 | Apache-2.0 |
| [TanStack/table](https://github.com/TanStack/table) | 28557 | 2026-11-02 | MIT |
| [microsoft/qlib](https://github.com/microsoft/qlib) | 50168 | 2026-11-04 | MIT |
| [vnpy/vnpy](https://github.com/vnpy/vnpy) | 46306 | 2026-11-07 | MIT |
| [dagster-io/dagster](https://github.com/dagster-io/dagster) | 16381 | 2026-11-09 | Apache-2.0 |
| [rootSunc/CNEquity](https://github.com/rootSunc/CNEquity) | 986 | 2026-11-06 | Apache-2.0 |
| [jibiao-ai/QuantMind](https://github.com/jibiao-ai/QuantMind) | 4 | 2026-06-28 | MIT |
| [pola-rs/polars](https://github.com/pola-rs/polars) | 40296 | 2026-11-09 | MIT |
| [shadcn-ui/ui](https://github.com/shadcn-ui/ui) | 127756 | 2026-11-09 | MIT |

## 本机实测补充

在已安装的 AKShare 1.18.64 上进行了三项一次性连接探测：股票日线23行、ETF日线23行、ETF分钟1085行。分钟样本中964行开盘价为0，因此“成功返回”并不等于“可用的完整K线”。保留原始值，完整OHLC记录需校验后再发布。详见 [探测摘要](probe-summary.md) 与 [原始报告](probes/report.json)。

官方文档与本机库版本不同；本轮未升级已有Python环境。CNEquity、Tushare账号权限与持续盘中稳定性尚未实测。
