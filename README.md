# 观澜 · 全市场 A股 / ETF 研究工作台

**给接手Agent：先读 [foragent.md](foragent.md)。推荐Windows Python 3.11 x64，也支持Linux/macOS。**

**新增六股技术面研究（2026-11-11）**：直接克隆最新源码即可使用附带的轻量样本，无须先下载大数据库。安装研究依赖后运行 `python -X utf8 -m app.technical --snapshot research/technical/snapshot`，启动服务后打开 `/technical`。默认研究沪电股份、东山精密、宏和科技、鹏鼎控股、江海股份与三一重工，支持快照/完整日线分时点展示、均线/RSI/MACD/ATR/布林带、历史事件检验和六股相关性。

首次完整交付包与近5年全市场数据库位于 [finance-v2026.11.10 Release](https://github.com/oldpig-god/Oldpig/releases/tag/finance-v2026.11.10)。源码仓库保留轻量，未将约10GB解压数据写入Git历史。

1. 从Release下载两个 `.zip.part001`、`.zip.part002` 附件，放在同一个目录。
2. 使用Python合并并校验：`python -X utf8 scripts/join_release.py --parts-dir 分片目录 --output finance-workbench-20261110.zip`。
3. 克隆最新源码。解压旧ZIP后，只把其中的 `data/` 目录复制到最新仓库根目录，保留最新程序；不要用旧包的 `app/` 覆盖新代码。Windows在最新仓库目录运行 `setup-windows.cmd`、`start-windows.cmd`；Linux使用 `bash setup-linux.sh`、`bash start-linux.sh`。
4. 打开 `http://127.0.0.1:8765/`；共振研究入口在页面右上角。首次安装依赖需要网络，查看已保存数据可离线。

完整包约3.07GB、解压约9.92GB；建议25GB可用空间。它包含7,245个证券的约740万条日线和研究结果，属于2026-11-10交接快照，并非未来启动日的实时数据。Windows已做兼容适配，尚需接收者在Windows实机验收。

本地应用已扩展为全市场证券名录、批量行情快照和可续传历史日线采集。**统一研究范围为滚动近5年；2026-11-10交付时窗口为2021-11-10至2026-11-10，运行时随上海日期滚动。**2026-11-10 首轮发现 **5,580 只当前沪深京 A股、1,666 只沪深场内 ETF**，共 7,246 只。运行时覆盖数量与补采结果以页面和 `/api/coverage` 为准。

A股范围以 AKShare 从交易所取得的当前上市名录为准；行情快照中额外出现的退市、转换等代码保留在原始记录中，不计入当前覆盖。ETF 范围为上游 ETF 分类列表，包含股票、债券、商品、跨境等场内 ETF；不含场外联接基金。当前名录不等于历史退市证券全集。页面分别显示“窗口内有记录”和“近5年请求完成数”，后者未完成时表示仍在补齐旧年份。

## 启动与全市场采集

推荐 Python 3.11 64位；支持 Windows / Linux / macOS。进程锁在 Windows 使用 msvcrt，在 Unix 使用 fcntl。交接与安装请先看 [foragent.md](foragent.md)。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# 初始化交易日历，同时取一只证券的日线
.venv/bin/python -m app.collector --calendar --symbols 510300.SH --intervals 1d
# 先取得交易所名录，再取得全市场行情
.venv/bin/python -m app.market --inventory
.venv/bin/python -m app.market
# 四个并发工作线程；任务进度持久化，可中断续传
.venv/bin/python -m app.backfill
# 启动界面；历史采集可在另一终端同时运行
.venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8765
```

打开 <http://127.0.0.1:8765>。本机现有环境亦可直接用 `python3` 或 `scripts/start-local.sh` 启动服务。默认数据目录为 `data/`；可用 `FINANCE_DATA_DIR` 设置绝对路径。

## 更新方式

- **全市场快照**：「更新全市场快照」抓取 A股与 ETF 两类批量行情。「开启全市场轮询」在交易时段执行，最小调度间隔 60 秒，不重叠；源端分页使一轮实测超过一分钟，因此不承诺每只股票每分钟取得新报价。股票快照未提供源行情时间时只显示抓取时间，不把抓取时间当作成交时间。
- **当前图表**：「更新当前图表」独立更新所选证券、所选周期。1 分钟趋势价格、5 分钟 K线仍按需抓取。批量快照不会伪造成全市场分钟 K线。
- **历史日线**：队列从上海日期的五年前同日开始请求，盘中截止前一天，当天收盘后的最终发布仍有延迟。已有数据会去重并记录修订；完成任务在续传时跳过。页面「继续补采日线」可启动尚未处理的队列。
- **失败重跑**：`python3 -m app.backfill --retry` 重新排入空响应、异常和部分拒绝任务；连续源请求失败会中止本轮，保留其余待处理任务。
- **收盘处理**：启用轮询后，15:01 起补抓批量收盘快照，17:30 起将日线队列重新排入。轮询首轮会检查当日 A股名录是否已更新。交易日历需定期执行初始化命令更新，日历不覆盖时不自动采集。

自动更新开关只保留至服务进程退出；设置 `FINANCE_AUTO_REFRESH=1` 可在启动时开启。未安装系统常驻服务。后台历史队列是独立进程；网页关闭不影响已启动的队列，但机器休眠或关机会中断采集，之后可续传。

## 页面与数据表

全市场搜索和分页在后端执行，每页 50 只，支持 A股 / ETF、上海 / 深圳 / 北京筛选。覆盖面板分开显示名录数、有效报价数、已有日线证券数和队列状态，并列出价格缺失证券。主图与列表快照使用独立的时间标签。

| 数据层 | 本地表 / 文件 | 含义 |
|---|---|---|
| 证券名录 | `instruments`、`market_members` | 当前上市范围、交易所、证券类型；保留非当前名单记录 |
| 批量行情 | `market_quotes` | 最近快照与批次；缺失价格保留 null |
| 原始响应 | `data/raw/YYYY-MM-DD/*.json` | 不可覆盖的源数据批次、参数、哈希；新历史批次采用 .json.gz |
| 价格历史 | `observations`、`revisions` | 当前记录及值变化历史；新价格载荷压缩保存，兼容旧格式 |
| 作业与进度 | `batches`、`history_tasks`、`watermarks` | 校验结果、可续传任务与成功游标 |

AKShare 用于名录、批量快照和按需分钟接口；全市场日线使用相同东方财富 Kline 端点的轻量 HTTP 适配器，核验响应证券代码及市场，避免逐只启动完整 SDK 进程。直接适配器设置 5 秒连接 / 20 秒读超时，批次标记 `direct-kline-v1`；SDK 单只请求仍有独立进程 25 秒期限。

## GitHub 近5年补充

图表上方选择「GitHub · 近5年补充」即可读取固定版本的 [tomuopc/a-stock-data](https://github.com/tomuopc/a-stock-data) CSV。当前可匹配主名录中的 6,089 只证券（4,584 只 A股、1,505 只 ETF），首次查看按需下载，之后使用本地缓存。开发验证时曾缓存并核验12只；本次交接包不包含这些外部缓存，接收方按需下载。查询时仅返回近5年窗口。原始 CSV 可能包含更早日期，仅保留作源文件哈希核验，不进入当前分析。归档截止 2026-06-26，最新行情仍来自主库。

每个文件检查固定 commit 的 Git blob 哈希并记录 SHA-256；重叠日期自动比对 OHLC，页面显示差异计数和固定源文件链接。12 只合计 4,284 个重叠交易日，在设定价格容差内无差异。历史复权方式未在源仓库明确声明，因此独立展示、不覆盖主库。仓库无单独许可证文件，当前仅作本地研究，不发布其数据副本。

[候选数据集与核验报告](research/github-data/selection.md)区分已接入、隔离和未完成下载的数据。另一个 ETF 数据集样本存在价格口径差异，未导入；约1GB的DuckDB候选在开发机只完成部分下载，未验完哈希，未计入覆盖，也未随交接包分发。

## 已知数据边界

1. “全部名录已发现”“有一条日线”“历史无缺口”是不同状态。覆盖计数不代表上市以来全历史，也不涵盖历史退市全集。新上市、停牌、上游缺失等情况会显式保留。
2. 1 分钟接口按分时价格序列处理，开盘为 0 不自动判定有效价格也无效；不填造开高低。全市场尚未批量建立 1 分钟 / 5 分钟历史。
3. 日线和 5 分钟价格不复权；未接公司行动、历史复权因子和 PIT 财务。不能直接据此计算投资总收益或完成严谨回测。
4. 量额保留上游数值，单位尚未逐接口确认，暂不提供跨源量能比较。ETF NAV、IOPV 和费用字段未进入已核验分析层。
5. 主行情库仍以东方财富为上游；GitHub / 通达信样本已完成重叠窗口比对，但并未对全市场全部日期进行双源核验。原始批次持续留存，尚未配置归档清理策略。

## 验证

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m unittest discover -s tests -v
# 先启动服务，默认浏览器为 /usr/bin/google-chrome
python3 scripts/check_market_ui.py
```

30 项管道 / 市场名录 / GitHub 校验 / 五年边界 / API 测试通过；21 项全市场浏览器检查通过。测试使用临时数据库；浏览器检查真实本地服务，包括全市场分页、北交所新代码、ETF 筛选、源价格比对、原始证据及手机布局。报告及截图：[design/v4](design/v4/)。

## 研究资料

- [滚动近5年范围](design/five-year-window.md)
- [本轮全市场实现](design/whole-market-v4.md)
- [上一版单机数据链路](design/local-app-v3.md)
- [第二轮开源项目研究](research/round-two/selection.md)
- [第一轮开源项目研究](research/selection.md)
- [长期架构目标](design/data-architecture.md)：其中 Polars / DuckDB / React 等仍属计划，当前运行栈为 Python、SQLite、FastAPI、原生 JavaScript、KLineChart。
- [实际集成清单](contracts/integration-manifest.json)

接口来源：[AKShare 股票数据](https://akshare.akfamily.xyz/data/stock/stock.html)、[ETF 数据](https://akshare.akfamily.xyz/data/fund/fund_public.html)、[A股名录源码](https://github.com/akfamily/akshare/blob/main/akshare/stock/stock_info.py)。图表使用本地 KLineChart 9.8.12，附 Apache-2.0 许可证。

## 首轮共振研究

工作台右上角“共振研究”进入 `/static/research/index.html`。分析16只代表股票和3只宽基ETF，固定滚动近5年窗口。真实后复权日线单独存入 `data/research/`，不改主行情库；原始响应、SHA-256、参数与交易日历可审计。前复权对照保留在研究页，最大相关系数差约0.037；发现前复权现金分红调整会改变历史收益分母后，主分析采用后复权价格收益代理。两者都不宣称是已独立验证的分红再投资总回报。

研究页含收益率与残差相关热力图、120组股票对、60交易日滚动曲线、训练/留出期比较、价格与收益率协方差、Ledoit–Wolf收缩协方差、含ETF的矩阵和数据说明。固定当前存续样本具有选择偏差；这是回顾性探索，不是时点回测或交易策略。方法参考PyPortfolioOpt与Riskfolio-Lib，实际运行使用scikit-learn、SciPy、NumPy、pandas。

```bash
python3 -m pip install -r requirements-research.txt
python3 -m app.comovement
# 复现本轮固定快照与前复权敏感性检验，不请求网络
python3 -m app.comovement --snapshot data/research/20261110T154400 --reference app/static/research/qfq-analysis.json
python3 -m unittest discover -s tests -q
python3 scripts/check_research_ui.py
```

本轮验证：37项Python测试、11项研究页浏览器检查、21项原行情页回归检查通过。近5年全市场补采完成：7,245个证券有日线、7,397,946行，另1个返回空；1,294个证券在已观察首尾日期之间存在交易日数量缺口（原因未核验），未进行填充。最终覆盖见 `design/v4/coverage-five-years.json`，缺口审计见 `design/v4/history-window-audit.json`。


## 六股技术面研究（2026-11-11新增）

入口 `/technical`，后端 `app/technical.py` 与 `app/indicators.py`。技术图使用前复权价格并锚定样本末日实际收盘价；收益率/协方差使用后复权价格收益代理，两种口径明确分开，均不宣称已核验的分红再投资总回报。当前完整日线截至11月10日，11月11日行情快照单独展示，不补成一根假日K。

固定3个规则：MA20上穿MA60、突破此前20日高点且量比≥1.5、RSI首次进入70以上；按时间前80%形成期、后20%留出期观察20交易日超额表现。首250日预热；形成期结果不跨入留出期，同证券/规则持有窗口不重叠。样本由用户当前截图选择，存在选择偏差；留出期事件大多不足10次，不能据此宣称有效交易策略。

```bash
# 仅技术研究：不需要全市场SQLite，也不发网络请求
python -X utf8 scripts/doctor.py --technical-only
python -X utf8 -m app.technical --snapshot research/technical/snapshot
# 重新采集：需要已保存/更新的交易日历，或使用页面“更新研究数据”
python -X utf8 -m app.technical
# 有服务运行时，浏览器验收
python scripts/check_technical_ui.py
```

Windows用 `.venv\Scripts\python.exe` 替换解释器。实际输入在 `research/technical/snapshot/`，数值参考测试在 `tests/fixtures/ta-lib/`；页面提供逐行输入、来源参数、哈希及事件日期下载。旧Release仍保留原日期版本，不包含本次新模块；获取新功能以最新源码为准。


### 自选技术面分析

打开 `/technical`，按名称/代码搜索A股或ETF，添加或移除证券后点击“分析所选证券”。支持每次1—12只；单只显示指标和历史事件，多只额外显示共同样本相关性/协方差。结果保存在本地，刷新页面可继续使用；“更新研究数据”更新当前结果中的证券。恢复原六股按钮只修改待分析列表，点击分析后生效。

数据窗口为近5年；新股和停牌不补价，指标窗口不足或共同收益样本不足显示空值。名录之外可以输入完整代码（如 `600519.SH`、`000001.SZ`、`920001.BJ`），能否生成以源端是否提供有效日线为准；尚不支持港美股。名称搜索依赖本地证券名录。新的联网分析仍需要已保存的交易日历。

Windows / Linux 命令行示例（已配置依赖和数据目录）：

```bash
python -X utf8 -m app.technical --symbols 600519.SH 000001.SZ 510300.SH
```

API：`GET /api/technical/search?q=茅台`；`POST /api/technical/refresh`，JSON `{"symbols":["600519.SH","510300.SH"]}`，请求头 `x-workbench-action: local-refresh`。同源限制与并发锁继续有效；无正文保留原默认六股兼容行为。失败保留上一份分析。自选采集不会覆盖仓库内原六股离线输入；当前研究可通过页面下载 `evidence.zip`，解压后用 `--snapshot` 重算。输出目录仍为 `app/static/technical`，每次成功更新替换当前研究，不是多用户隔离存储。


### CloudBase 国内站云端版本

已增加 Python HTTP 云函数入口、私有 PG Storage 数据适配、受邀账号登录、按账号隔离的技术分析结果和云端行情更新。部署和维护步骤见 [CloudBase 部署说明](deploy/cloudbase/README.md)。云端持久数据存储与函数 `/tmp` 缓存分离，不可把 `/tmp` 当数据库备份；凭证和账号密码必须放在仓库外私有目录。原本的本地启动方式继续可用。
