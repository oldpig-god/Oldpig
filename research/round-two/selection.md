# 第二轮开源检索与整合结果

核查日期：2026-11-10。范围仍为 A股 / ETF、分钟跟踪和日线研究；本轮重点补强前端产品体验及数据工程。检索并核查了9个新候选，与首轮16个不重复。仓库热度和push日期仅作维护参考，不当作正确性或行情覆盖证明。

## 本轮取舍

| 项目 | 值得吸收的部分 | 本项目的具体用法 | 状态 / 边界 |
|---|---|---|---|
| [Wealthfolio](https://github.com/wealthfolio/wealthfolio) | 图表优先、价格层级、组合研究布局；本地优先产品思路 | 将主图前置，缩窄导航，自选固定在右侧 | 已参考官方README截图；AGPL-3.0；未复制代码，未接入券商同步 |
| [Ghostfolio](https://github.com/ghostfolio/ghostfolio) | 资产组合、活动记录和风险分区 | 后续持仓、收益与风险页按研究任务分区 | 架构参考；AGPL-3.0；Angular/NestJS体系不直接混入当前预览 |
| [OpenStock](https://github.com/Open-Dev-Society/OpenStock) | 自选、搜索、详情、缓存共享的产品流程 | 自选筛选→点击→详情与图表同步 | 已吸收交互思路；AGPL-3.0；其图表为TradingView嵌入、行情用Finnhub，不能视为自有A股数据引擎 |
| [Fincept Terminal](https://github.com/Fincept-Corporation/FinceptTerminal) | 研究终端的信息密度、跨模块导航 | 保留窄导航与工作区分层，延后交易、AI代理和大型终端集成 | 参考候选；当前仓库含原生C++/Qt结构；不是现成React组件包。LICENSE核查为AGPL-3.0，不从第三方下载站取安装包 |
| [KLineChart](https://github.com/klinecharts/KLineChart) | K线、十字线、缩放、内置技术指标 | **已接入预览**：替换手画SVG，加入MA/VOL/MACD、日/周/5分钟切换 | Apache-2.0；固定9.8.12；该版本实际加载和交互已验证，不宣称是最新版 |
| [Perspective](https://github.com/perspective-dev/perspective) | 大表、分组聚合、交互式分析与流数据 | 后续“行业×日期”“ETF×指标”透视分析页 | 方案候选；Apache-2.0；旧finos/perspective地址会重定向。小型自选表无需引入整套WASM组件 |
| [dlt](https://github.com/dlt-hub/dlt) | 增量游标、状态、merge与重叠窗口 | 下一阶段历史回补与重复拉取的装载层候选 | Apache-2.0；不提供A股行情，不能替代AKShare；未运行数据流水线 |
| [Great Expectations](https://github.com/fivetran/great_expectations) | 数据质量检查与结果表达 | 后续跨批次、跨表完整性审计；当前保留轻量Pandera行级检查 | Apache-2.0；旧组织路径已重定向；避免一开始引入两套重复规则引擎 |
| [FlexLayout](https://github.com/caplin/FlexLayout) | React多面板、标签与布局持久化 | 将来需要多窗口研究时再使用；本轮先做图表专注模式 | MIT；当前未集成，预览的专注开关是自有实现，不是假称可拖拽工作区 |

项目能力依据各自官方仓库；OpenStock的外部服务依赖依据其[README的集成说明](https://github.com/Open-Dev-Society/OpenStock#readme)。Fincept功能范围依据其[官方仓库](https://github.com/Fincept-Corporation/FinceptTerminal)，未用同名镜像作判断。

## 整合方案更新

### 已实际完成

- KLineChart 9.8.12经固定CDN URL加载，不调用行情API。接入均线、成交量、MACD、鼠标缩放与十字线。版本通过npm元数据及发行类型定义核验。
- 界面从“先表格后小图”改为“主图 + 自选 + 底部研究数据”；搜索、ETF/股票筛选、涨跌排序、亮暗主题与图表专注模式均实现。
- 行情明细和图表读取同一份确定性的演示序列；切换标的和周期后同步。
- 数据质量页单独展示首轮真实探测快照，明确区别于演示价格。没有把964条开盘价为0的分钟记录画成有效K线。

### 下一阶段应落地

```text
AKShare / 授权数据源
  → 保存不可变原始响应与批次
  → 可选 dlt 增量装载（游标 + 重叠窗口）
  → Polars 标准化 / Pandera 规则与隔离
  → Parquet / DuckDB
  → FastAPI 查询契约
  → KLineChart + 自选表 + ETF比较
  → 可选 Perspective 透视页
```

dlt的[增量文档](https://dlthub.com/docs/general-usage/incremental-loading)支持游标、merge和窗口刷新。金融历史会修订：只按最大日期追加不够，需保留原始版本、重叠回读，并对超出窗口的公司行为触发定向回补。源游标、装载游标、已校验可见版本需要分开记录，不能因装载成功就把坏记录公开。

前端主图选型更新为KLineChart优先，因为本阶段需要内置指标与中国市场K线交互。Lightweight Charts仍是简单嵌入图的备选；一个主图不同时维护两套图表库。首轮数据源、复权、时间戳与PIT规则继续有效。

### 暂不整合

不拼接完整的Wealthfolio/Ghostfolio/OpenStock/Fincept应用；保留自己的证券主键、数据契约和页面状态。它们作为产品与架构参考，避免同时维护多种框架、账户体系和供应商依赖。当前没有把AGPL应用代码复制到项目中。

## 设计技能与证据

- [Anthropic frontend-design](https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md)：已阅读全文，用于设计计划和截图自检；未全局安装。
- 本机 visualize / UI mockups：用于本次对话中的交互界面；配色、排版和控件使用产品自己的规范。
- [设计记录](../../design/redesign-v2.md)
- [交互检查结果](../../design/v2/checks.json)
- [整合清单](../../contracts/integration-manifest.json)

## GitHub元数据快照

下表日期为UTC push日期，不等同于正式发布日。原始字段保存在同目录 `github-snapshot.json`。许可证以锁定版本的实际文件为准；本表不是对数据使用权的判断。

| 仓库 | Stars | 最近push（UTC） | 许可证 |
|---|---:|---|---|
| [wealthfolio/wealthfolio](https://github.com/wealthfolio/wealthfolio) | 9399 | 2026-11-10 | AGPL-3.0 |
| [ghostfolio/ghostfolio](https://github.com/ghostfolio/ghostfolio) | 9680 | 2026-11-10 | AGPL-3.0 |
| [Fincept-Corporation/FinceptTerminal](https://github.com/Fincept-Corporation/FinceptTerminal) | 33642 | 2026-11-09 | AGPL-3.0 |
| [Open-Dev-Society/OpenStock](https://github.com/Open-Dev-Society/OpenStock) | 21398 | 2026-11-09 | AGPL-3.0 |
| [perspective-dev/perspective](https://github.com/perspective-dev/perspective) | 11406 | 2026-11-09 | Apache-2.0 |
| [klinecharts/KLineChart](https://github.com/klinecharts/KLineChart) | 4297 | 2026-11-08 | Apache-2.0 |
| [dlt-hub/dlt](https://github.com/dlt-hub/dlt) | 6068 | 2026-11-09 | Apache-2.0 |
| [fivetran/great_expectations](https://github.com/fivetran/great_expectations) | 11922 | 2026-11-09 | Apache-2.0 |
| [caplin/FlexLayout](https://github.com/caplin/FlexLayout) | 1385 | 2026-10-21 | MIT |
