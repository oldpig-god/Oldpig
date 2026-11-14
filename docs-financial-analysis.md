# 个股财务分析与研究页面结构

## 设计依据

- TradingView Lightweight Charts：https://github.com/tradingview/lightweight-charts 。实际集成5.0.8 standalone版本，替换个股页原KLineCharts渲染。原始日线、服务端MA20/60、缺失日留白、十字光标、区间缩放；页面保留TradingView归属链接与内置标志。
- Apache ECharts：https://github.com/apache/echarts 。实际集成5.6.0本地资源，财务营收/归母/扣非趋势使用柱图，含负数坐标、图例切换、悬浮数值，响应容器大小和主题切换。
- Tremor：https://github.com/tremorlabs/tremor 。参考指标摘要、图表与明细的层级和留白，不引入React/Tailwind改造现有原生页面。
- OpenBB：https://github.com/openbq-org/OpenBB 。参考按研究主题分类、统一字段与数据来源口径；没有安装完整OpenBB平台。
- Perspective：https://github.com/perspective-dev/perspective 。参考可展开的结构化明细与固定指标列；当前单证券约20期，不引入WASM透视引擎。
- TradingView财务分类：https://www.tradingview.com/support/solutions/43000543506-how-to-access-financial-data-on-tradingview/
- NN/g渐进展示：https://www.nngroup.com/articles/progressive-disclosure/

界面：四个常用导航入口；其余工具折叠。个股分技术走势、财务分析、数据说明。证券浏览按需打开。财务采用核心数字→五年趋势→可展开观察依据→指标明细。桌面双图、手机单栏。

## 数据与计算

`app/financial_analysis.py`；GET `/api/securities/{symbol}/financials`。证券通过现有名录解析，ETF不适用。
来源为AKShare `stock_financial_analysis_indicator_em` 使用的东方财富指标接口：
https://github.com/akfamily/akshare/blob/main/akshare/stock_fundamental/stock_finance_sina.py

保留近五年已披露报告，报告期与公告日独立展示。公告日晚于当前日期的记录排除，公告日缺失明确标注；并非历史时点数据库。
金额元→页面亿元；百分比维持数据源百分数；利润/现金流为年初累计，资产负债率为期末，ROE为源加权口径且未自行年化。空值不是零。负利润允许；非有限数、负营收、代码/币种错误、冲突报告拒绝写入。
TTM=本期累计+上年全年−上年同期，缺少任一期留空。年度报告直接作为TTM。
财务观察不产生买卖评分：展示增长方向、扣非与归母差异、每股经营现金流正负、负债率同比百分点。金融企业不套工业企业偿债阈值。

## 缓存与云端

复用现有Store：`system/financials/{symbol}.json.gz`。每日首次查询检查更新，更新失败返回上次已校验缓存并提示；此功能不是全市场定时预采。进程内分片锁限制重复同证券采集，HTTP超时5/20秒。云路由在通用路由复制之前注册，确保使用云Store。接口沿用现有登录认证，无新增密钥或外部CDN运行依赖。

## 验证

6项专项单元测试：TTM、空/零/负利润、身份/非法值/冲突、未来公告/五年区间、缓存复用/失败保护、ETF/银行。
全套213项通过。实测600031.SH、002463.SZ、000001.SZ、920002.BJ；ETF 510300.SH不适用。未宣称名录全部证券都有财务覆盖。
浏览器覆盖技术/财务标签、ECharts柱图、报告期切换、财务明细展开、真实股票切换、深色主题、390/760/800/1024/1440宽度无横向溢出。截图见design/stock-financials。

云端验收：guanlan-api部署后Active。登录后实测财务接口和技术/财务页面交互通过；未登录财务API返回401。源码研究额外核对Tremor的BarChart/Card组件与OpenBB的income_statement标准模型，采用其分类和布局思路，未复制组件源码。
