# 观澜 · A股策略筛选与研究工作台

**给接手Agent：先读 [foragent.md](foragent.md)。推荐Windows Python 3.11 x64，也支持Linux/macOS。**

**策略筛选工作台（2026-11-18 更新）**：主页 `/` 聚焦满足策略条件的 A股筛选与前复权行情分析。筛选引擎 `app/screener.py` 按 7 项规则从全市场 A股中逐级过滤（漏斗可审计）：非 ST（`not_st`）、总市值 > 500 亿元（`cap`）、`PE TTM > 0`（`pe_positive`）、行业 `PE TTM` 在同行业均值 ± 2σ 内（`pe_band`，同行业样本 < 5 时不淘汰，支持开关切换）、日线前复权 `MA5 > MA10` 或 `MA5 > MA20`（`ma`）、`EMA12 > EMA20` 或 `EMA12 > EMA50`（`ema`）以及 `MACD(12,26,9) DIF > DEA`（`macd`）。同时保留 `/stocks`、`/technical`、`/strategy`、`/compare`、`/discover`、`/data-health` 与 `/research` 多页面研究工作台。

**六股技术面研究**：直接克隆最新源码即可使用附带的轻量样本，无须先下载大数据库。安装研究依赖后运行 `python -X utf8 -m app.technical --snapshot research/technical/snapshot`，启动服务后打开 `/technical`。默认研究沪电股份、东山精密、宏和科技、鹏鼎控股、江海股份与三一重工，支持快照/完整日线分时点展示、均线/RSI/MACD/ATR/布林带、历史事件检验和六股相关性。

首次完整交付包与近5年全市场数据库位于 [finance-v2026.11.10 Release](https://github.com/oldpig-god/Oldpig/releases/tag/finance-v2026.11.10)。源码仓库保留轻量，未将约10GB解压数据写入Git历史。

1. 从Release下载两个 `.zip.part001`、`.zip.part002` 附件，放在同一个目录。
2. 使用Python合并并校验：`python -X utf8 scripts/join_release.py --parts-dir 分片目录 --output finance-workbench-20261110.zip`。
3. 克隆最新源码。解压旧ZIP后，只把其中的 `data/` 目录复制到最新仓库根目录，保留最新程序；不要用旧包的 `app/` 覆盖新代码。Windows在最新仓库目录运行 `setup-windows.cmd`、`start-windows.cmd`；Linux使用 `bash setup-linux.sh`、`bash start-linux.sh`。
4. 打开 `http://127.0.0.1:8765/` 查看策略筛选与图表终端，并可通过顶部导航进入 `/stocks`、`/technical` 等工作台。首次安装依赖需要网络，查看已保存数据可离线。

完整包约3.07GB、解压约9.92GB；建议25GB可用空间。运行时交易状态、名录与筛选结果以页面及 `/api/health`、`/api/screen` 为准。

A股范围以 AKShare / 腾讯行情接口取得的当前上市名录为准；行情快照中额外出现的退市、转换等代码保留在原始记录中，不计入当前覆盖。ETF 范围为上游 ETF 分类列表，可在全市场搜索中查看；主页策略筛选仅面向当前 A股名录。

## 启动与策略筛选采集

推荐 Python 3.11 64位；支持 Windows / Linux / macOS。进程锁在 Windows 使用 msvcrt，在 Unix 使用 fcntl。交接与安装请先看 [foragent.md](foragent.md)。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# 初始化交易日历，同时取一只证券的日线
.venv/bin/python -m app.collector --calendar --symbols 510300.SH --intervals 1d
# 先取得交易所名录与全市场行情快照
.venv/bin/python -m app.market --inventory
.venv/bin/python -m app.market
# 运行一次策略筛选（先抓全市场快照，再对市值>500亿且PE>0的非ST候选拉取前复权日线）
.venv/bin/python -m app.screener
# 启动界面；也可通过右上角“运行筛选”在后台运行
.venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8765
```

打开 <http://127.0.0.1:8765>。本机现有环境亦可直接用 `python3` 或 `scripts/start-local.sh` 启动服务。默认数据目录为 `data/`；可用 `FINANCE_DATA_DIR` 设置绝对路径。

## 更新方式

- **运行策略筛选**：页面点击「运行筛选」（或 `POST /api/screen/run`）会更新全市场名录与实时快照、筛选出非 ST 且总市值 > 500 亿、PE TTM > 0 的候选股票，再并发抓取候选前复权日线计算 MA / EMA / MACD，并为全部入选股票补齐 1d 前复权图表日线；结果写入 `screen_runs`。
- **全市场快照**：「更新全市场快照」刷新批量行情；开启自动更新后，交易时段按周期刷新，16:05 后可触发当日盘后自动筛选，17:30 后调度日线更新。
- **当前图表**：「更新当前图表」独立更新所选证券、所选周期。1 分钟趋势价格、5 分钟 K线仍按需抓取。批量快照不会伪造成全市场分钟 K线。

自动更新开关只保留至服务进程退出；设置 `FINANCE_AUTO_REFRESH=1` 可在启动时开启。未安装系统常驻服务。

## 页面与数据表

主页 `/` 左侧展示当前策略入选股票与筛选条件开关（含 `行业PE ±2σ` 开关、全市场搜索及多维排序）；右侧主图提供前复权日K / 5分 / 1分图表与 `MA(5,10,20)`、`EMA(12,20,50)`、`VOL`、`MACD(12,26,9)` 叠加显示，底部展示筛选漏斗、候选明细表、行情明细与数据质量证据。已移除早期共振研究页 `/static/research/index.html`、`/api/coverage`、`/api/backfill` 及 GitHub 补充历史源（`app/comovement.py`、`app/github_history.py`）。

| 数据层 | 本地表 / 文件 | 含义 |
|---|---|---|
| 证券名录 | `instruments`、`market_members` | 当前上市范围、交易所、证券类型；保留非当前名单记录 |
| 批量行情 | `market_quotes` | 最近快照与市值 / 市盈率 / 行业字段；缺失值保留 null |
| 策略筛选 | `screen_runs` | 每次筛选快照时间、漏斗计数、候选及入选明细、获取错误 |
| 原始响应 | `data/raw/YYYY-MM-DD/*.json` | 不可覆盖的源数据批次、参数、哈希；新历史批次采用 .json.gz |
| 价格历史 | `observations`、`revisions` | 当前记录及值变化历史；新价格载荷压缩保存，兼容旧格式 |
| 作业与进度 | `batches`、`bars`、`watermarks` | 校验结果、列式压缩日线与采集游标 |

AKShare / 腾讯行情与前复权直连适配器用于名录、批量快照、按需分钟线与候选日线，保留原始响应批次与 SHA-256 校验。

## 已知数据边界

1. 策略筛选依赖当前行情快照中的总市值、PE TTM、行业分类与历史前复权日线；停牌、新股窗口不足或上游获取失败的候选不计入入选，并在页面显著提示 `fetch_errors`。
2. 1 分钟接口按分时价格序列处理，开盘为 0 不自动判定有效价格也无效；不填造开高低。全市场尚未批量建立 1 分钟 / 5 分钟历史。
3. 本工作台主页前复权技术条件与入选清单仅作规则筛选与研究展示，不构成投资建议；`/stocks` 等全市场历史指标与回测边界见各专项说明。
4. 量额保留上游数值，单位尚未逐接口确认，暂不提供跨源量能比较。ETF NAV、IOPV 和费用字段未进入已核验分析层。
5. 主行情库以东方财富与腾讯公开行情为上游；原始批次与 SQLite / CloudStore 记录分离保存。

## 验证

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m unittest discover -s tests -q
# 本地与云端辅助检查
python3 -X utf8 scripts/doctor.py --technical-only
# 先启动服务，默认浏览器为 /usr/bin/google-chrome
python3 scripts/check_market_ui.py
```

测试使用临时数据库，验证策略筛选、技术指标、多页面 API、云端隔离安全与存储逻辑；`scripts/check_market_ui.py` 验证策略筛选主页、漏斗、切换规则开关及图表交互。

## 研究资料

- [滚动近5年范围](design/five-year-window.md)
- [本轮全市场实现](design/whole-market-v4.md)
- [上一版单机数据链路](design/local-app-v3.md)
- [第二轮开源项目研究](research/round-two/selection.md)
- [第一轮开源项目研究](research/selection.md)
- [长期架构目标](design/data-architecture.md)：其中 Polars / DuckDB / React 等仍属计划，当前运行栈为 Python、SQLite、FastAPI、原生 JavaScript、KLineChart。
- [实际集成清单](contracts/integration-manifest.json)

接口来源：[AKShare 股票数据](https://akshare.akfamily.xyz/data/stock/stock.html)、[ETF 数据](https://akshare.akfamily.xyz/data/fund/fund_public.html)、[A股名录源码](https://github.com/akfamily/akshare/blob/main/akshare/stock/stock_info.py)。图表使用本地 KLineChart 9.8.12，附 Apache-2.0 许可证。

## A股策略筛选工作台（2026-11-18）

主页 `/` 已重构为 A股策略筛选工作台（`app/screener.py`），接口为 `GET /api/screen?industry_band=true|false` 与 `POST /api/screen/run`：
1. **前置基础过滤**：排除 ST / *ST（`not_st`）、要求总市值 > 500 亿元（`cap`）且市盈率 TTM > 0（`pe_positive`）。
2. **行业市盈率区间**：按同行业非 ST 且 `PE TTM > 0` 的 A股计算均值与标准差，检验是否在均值 ± 2σ 内（`pe_band`）；同行业有效样本不足 5 只时不因该项淘汰，页面支持实时切换该条件开关。
3. **前复权技术条件**：对通过前置条件的候选股票抓取前复权日线，验证最新收盘后满足 `MA5 > MA10` 或 `MA5 > MA20`（`ma`）、`EMA12 > EMA20` 或 `EMA12 > EMA50`（`ema`），以及 `MACD(12,26,9) DIF > DEA`（`macd`）。
4. **移除项**：删除原首页全市场覆盖表与 `/api/coverage`、`/api/backfill`、GitHub 补充历史适配器（`app/github_history.py`）以及旧版共振研究页面 `/static/research/index.html`（`app/comovement.py`、`scripts/check_research_ui.py`）。

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

### 策略验证

访问 `/strategy`：固定双均线、次日执行、成本、三基准、独立留出、滚动窗口、形成期参数敏感性和匹配随机入场。每次实验冻结原始数据与代码版本，支持下载和重放，云端按账号隔离。

**现阶段是扣费后的净价格收益研究，不含未核验分红送转；历史ST/逐日官方涨跌停价亦未补齐，不能视为完整账户回测或有效性证明。** 适用范围、方法和复现见 [策略验证说明](research/strategy/README.md)。

## 全市场研究与新版终端（2026-11-12）

默认研究入口 `/stocks`，统一侧栏、顶部代码/名称搜索、深浅主题和手机导航。当前名录7,246只：A股5,580只、ETF1,666只；包含主板、创业板（含302代码段）、科创板、北交所与风险警示证券。名录覆盖不代表每只都有完整五年历史。指标按实际日线计算，缺失显式报告；这里使用不复权价格，除权除息可能影响指标，不作为总回报。

`/strategy` 按收益与风险、稳健性检验、交易账本、方法与证据分栏。engine strategy-v1.1 支持科创板/北交所申报数量；ST及未建模ETF交易规则仍禁止回测，样本不足也会拒绝。旧实验可查看下载；代码变化后的旧实验不承诺直接重放，下载中保留对应代码证据。

全市场接口：`/api/securities/coverage`、`/api/securities/search`、`/api/securities/{symbol}/analysis`。云端沿用登录和私有云历史读取，不向浏览器暴露存储凭证。没有新增本机库依赖；Windows启动方式沿用原说明。UI提示词与设计来源见 `design/terminal-v3/frontend-prompt.md`。

## 云端每日增量与补采（2026-11-12）

独立Event函数 `guanlan-daily-sync` 每5分钟检查一次，按北京时间17:30后采集日线、07:00—09:59补采。当前名录全部证券进入持久化队列；失败退避重试，重启继续，原档案保留。个股页展示应有交易日、有效日线日期、落后交易日数及采集状态。此功能无需用户电脑或浏览器保持打开。

`/api/daily-sync/status?symbol=000034.SZ` 受原账号登录保护。设计、并发约束、暂停方法与部署步骤见 `deploy/cloudbase/daily-worker/README.md`。当前交易日历到2026-12-31，越界停止并明确提示；到期前应更新可核验日历。不能把全名录队列等同所有源都能成功，也不能把日线更新等同分钟实时行情。

## 双对象比较（2026-11-13）

新增 `/compare`：任意股票/ETF，或两组各1—10只自选证券。煤炭、元件为可编辑代表组合，非完整行业指数。输出复权日收益协方差、相关系数、滚动图、散点图、窗口对比及剔除大盘后的相关性。每份报告可下载冻结原始证据并重算；账号隔离保持不变。方法和限制见 `research/comparison/README.md`。


### 数据健康中心（日更）

访问 `/data-health` 查看全市场完成率、失败/过期证券、历史缺口、来源与任务状态，按证券或筛选结果提交幂等补采。总览使用独立预聚合快照，明细后端分页；异常数据不会覆盖原有正常行情。数据按交易日收盘后更新，不要求盘中实时。运维、口径和验证见 [数据健康中心说明](docs-data-health.md)。
