# GitHub 历史数据补充核验

核验日期：2026-11-10。用户授权寻找真实历史数据补充。未运行任何候选仓库的代码；仅读取元数据、文档和价格文件。

| 仓库 | 实际发现 | 本轮决定 |
|---|---|---|
| [tomuopc/a-stock-data](https://github.com/tomuopc/a-stock-data) | 通达信来源的股票、基金、指数 CSV；README 截止 2026-06-26；无单独许可证文件 | 接入固定 commit 的只读本地按需历史源。与当前名录匹配 4,584 只 A股、1,505 只 ETF，共 6,089 只；不包含的证券仍使用主行情 |
| [noblechang/a-share-data](https://github.com/noblechang/a-share-data) | 发布者提供约 1 GB DuckDB 和摘要文件，声明股票主源为 BaoStock、学习研究用途 | 记录 release / SHA-256；下载速度较慢，停止大文件传输，保留部分文件与状态。尚未验完文件哈希、未查询或接入，不计入覆盖 |
| [YuxuanXiu/China-ETF-LOF-Daily-Market-Data](https://github.com/YuxuanXiu/China-ETF-LOF-Daily-Market-Data) | README 声明 CC BY 4.0；ETF / LOF CSV 至 2025-11-17 | 四只样本中日期列名为空，与说明不一致；510300 有 108 / 211 重叠日收盘价差异，510880 有 13 / 211。疑似复权差异但未证实；保持隔离，未合并 |
| [que3sui/A_stock](https://github.com/que3sui/A_stock) | 有数据结构文档和研究代码，根目录无其声称的全量 daily 原始数据目录 | 不作为本轮可直接导入数据包 |
| [rootSunc/CNEquity](https://github.com/rootSunc/CNEquity) | 本地数据湖采集框架，不能把项目代码仓库当作已下载历史数据包 | 继续作为数据工程候选，未声称已运行或集成 |

## 已接入版本与校验

固定 `tomuopc/a-stock-data` 的 commit `cd1ee3a2e2c28ba5b623b159e8444130ca7546e1`。目录树完整性已检查；逐文件下载核对 Git blob SHA-1、字节数，并生成本地 SHA-256 与来源记录。缓存目录为 `data/external/github-csv/<commit>/`。不把源文件名当作当前证券名称，证券归属仍使用主库名录。

先下载原股票池 10 只作为核验样本，再通过真实 API 按需下载万科 A、50ETF 两只。12 只合计比较 4,284 个重叠交易日，OHLC 在股票 0.0101 元 / ETF 0.0011 元容差内均无差异。所有已缓存样本价格校验无无效行或冲突日期。万科 A 归档始于 1991-01-29，50ETF 始于 2005-02-23；不是所有证券都有同样长度。

重叠比较结果不能证明早期历史全部正确，也不能证明全时期复权口径。仓库未明确复权方式，API 标记 `source_unspecified`，界面明确“源仓库未声明复权口径”。当前独立展示，不自动拼接、覆盖主库价格，也不据此宣称全部历史或无生存者偏差。

证据：`tdx-tree.json`、`tdx-sample-audit.json`、`000002.SZ-live-import.json`、`510050.SH-live-import.json`、`etf-sample-audit.json`。大型 DuckDB 候选状态位于 `data/external/a-share-data/status.json`。

## 全市场主库首轮结果

当前名录 7,246 只；日线 7,245 只，共 3,016,200 条；无失败 / 部分拒绝任务，1 个空响应。空响应证券 688827 的交易所上市日期为 2026-11-10，本轮日线截止 2026-11-09，因此请求窗口内无历史，符合日期边界。[上交所上市公告](https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20261109_10837163.shtml)

全市场快照有 7,246 条记录，第二轮有效价格为 7,243 只。缺失价格保持 null，不以前收盘价填补。完整状态见 `design/v4/coverage-final.json`。
