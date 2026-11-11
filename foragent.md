# 给接手 Agent：观澜本地金融研究工作台

> **先区分轻量研究与全市场数据：** 使用最新main源码；新增六股技术面研究自带 `research/technical/snapshot/`，无需先下载3GB数据包。若需要完整行情库，再下载 `finance-v2026.11.10` 的两个分片，用 `scripts/join_release.py` 合并；解压旧ZIP后仅把 `data/` 复制到最新源码根目录，不要用旧代码覆盖最新程序。旧Release是不可变的历史交付版本，未包含本次六股模块。

请先完整阅读本文件，然后在**最新源码的项目根目录**工作。目标是让接收者在自己的电脑查看真实 A股/ETF 行情和已保存的协方差研究，并按需继续更新。不要依赖原作者机器的路径、Python环境或已启动的服务。

## 1. 交接内容与环境边界

- 推荐 **Windows 10/11 + Python 3.11 64位**；也支持 Linux/macOS 的 Python 3.10–3.13。优先用3.11，不要默认用最新Python或32位解释器。
- ZIP包含源码、前端本地依赖、SQLite数据库、主行情原始采集证据、19个标的的复权研究原始响应、研究结果与测试。**不包含 Python解释器、虚拟环境或离线安装轮子**。首次安装需要访问PyPI，数据更新需要访问上游行情服务。
- 建议解压至本机SSD短路径，例如 `C:\finance-workbench`。避免网络共享盘、OneDrive同步目录和只读目录。Unicode路径已按UTF-8处理，短英文路径更便于排障。
- 解压数据约10GB；建议接收机器至少有 **25GB可用磁盘、8GB内存**，预留数据库WAL和更新空间。ZIP含大于4GB的单个数据库，必须使用支持ZIP64的解压器与NTFS/exFAT/APFS/ext4等文件系统；不要放FAT32。完整解压后再运行，勿在压缩文件预览窗口启动。
- 当前交付数据快照为 **2026-11-10（上海）**，日线主要截止2026-11-09。它不代表接收者启动当天的最新行情。主界面研究窗口随上海日期滚动，研究页是固定日期的已保存分析。
- 本包不包含 `data/external/`：其中有无明确再分发许可证的GitHub CSV、隔离样本与未完成的大文件下载。按需GitHub适配器及固定commit目录索引仍在，但该缓存需接收方自行访问上游取得；不得声称包中已附带这部分数据。
- `delivery-manifest.json`记录实际纳入文件、SHA-256、大小、打包时数据库状态和排除项；旁边的ZIP `.sha256`校验文件用于传输完整性检查。

## 2. Windows快速使用（优先）

先确认64位Python 3.11已安装并带Python Launcher：

```powershell
py -0p
py -3.11 -c "import sys,struct; print(sys.version); print(struct.calcsize('P')*8)"
```

在资源管理器中先运行 `setup-windows.cmd`，安装成功后运行 `start-windows.cmd`。窗口保留运行时，打开：

- 行情：`http://127.0.0.1:8765/`
- 共振研究：`http://127.0.0.1:8765/static/research/index.html`
- API状态：`http://127.0.0.1:8765/api/health`

Agent应优先使用下列可检查退出码的PowerShell命令。无需激活虚拟环境，因此不受PowerShell激活脚本执行策略影响：

```powershell
Set-Location 'C:\finance-workbench'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -X utf8 -m pip install --upgrade pip
.\.venv\Scripts\python.exe -X utf8 -m pip install -r requirements-research.txt
.\.venv\Scripts\python.exe -X utf8 scripts\doctor.py
.\.venv\Scripts\python.exe -X utf8 scripts\run_local.py
```

如没有 `py` 命令，找到实际Python3.11可执行文件，替换第一条建环境命令，例如 `& 'C:\Python311\python.exe' -m venv .venv`。不要把开发机的 `.venv` 复制过来。端口被占用时使用 `start-windows.cmd --port 8766` 或 `scripts\run_local.py --port 8766`，并在浏览器使用相同端口。

默认只绑定 `127.0.0.1`；默认自动采集关闭。仅浏览已有快照不需要行情网络；不要为了打开页面立即重跑全市场历史采集。退出服务按Ctrl+C。

## 3. Linux / macOS

```bash
cd /实际路径/finance-workbench
bash setup-linux.sh
.venv/bin/python -X utf8 scripts/doctor.py
bash start-linux.sh
```

Linux若缺venv模块，安装发行版对应的python3-venv。绘图若缺中文字体，可安装Noto CJK；Windows通常直接使用微软雅黑。可设置 `FINANCE_FONT_PATH` 为本机真实存在的 `.ttf/.ttc` 字体绝对路径。后端使用 `tzdata` 提供Asia/Shanghai时区，接收者不必把操作系统时区改为上海。

## 4. 首次验收

1. 查看 `delivery-manifest.json` 与压缩包SHA-256；需要逐文件验证时运行 `python -X utf8 scripts/verify_delivery.py`，会顺序读取约10GB数据，不需要网络。
2. `scripts/doctor.py`应输出Python/平台、已装依赖、上海时间、数据路径、SQLite schema和研究结果状态。失败先修环境，不要删库重建。深度SQLite检查可执行 `scripts/doctor.py --deep`，全库扫描较慢。
3. 页面应能看到证券列表、覆盖数量、日线和“共振研究”入口；共振页应显示16只股票、3只ETF、1,172个共同收益率样本和120组股票对。
4. 本次快照：5,580只A股、1,666只ETF；7,245个证券有日线、7,397,946条窗口内日线；1个空响应为688827.SH。1,294个证券在已观察首尾日期之间有交易日数量缺口，原因未全部核验。不得把采集任务完成解释为每只证券都有完整五年历史。
5. Python测试：`.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -q`（Unix替换Python路径）。交付前Linux运行结果记录在 `design/delivery-validation.json`。Windows原生执行仍需接收方确认，不能声称本包已在Windows实机验证。

可选浏览器回归需要单独安装开发依赖与浏览器：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -X utf8 -m playwright install chromium
.\.venv\Scripts\python.exe -X utf8 scripts\check_research_ui.py
.\.venv\Scripts\python.exe -X utf8 scripts\check_market_ui.py
```

测试默认连接8765端口，请先启动服务。已装Chrome也可设置 `CHROME_PATH` 为其本机绝对路径。浏览器测试中的证券数量基于本次快照；更新名录后需核对预期变化。

## 5. 数据更新与环境变量

命令示例使用PowerShell，Unix使用 `.venv/bin/python -X utf8` 替换解释器路径。均在项目根目录执行：

```powershell
# 更新交易日历及一只ETF，保留已有数据
.\.venv\Scripts\python.exe -X utf8 -m app.collector --calendar --symbols 510300.SH --intervals 1d
# 更新当前证券名录、再更新批量行情
.\.venv\Scripts\python.exe -X utf8 -m app.market --inventory
.\.venv\Scripts\python.exe -X utf8 -m app.market
# 续跑未完成的历史任务；本包已完成的任务会跳过
.\.venv\Scripts\python.exe -X utf8 -m app.backfill
# 仅在确需重新尝试异常/空任务时使用
.\.venv\Scripts\python.exe -X utf8 -m app.backfill --retry
```

- `FINANCE_DATA_DIR`：数据目录绝对路径。默认项目根目录下的 `data/`。若迁移数据，应复制整个data目录及其相对结构，不只复制SQLite文件；原始证据路径与任务状态互相关联。
- `FINANCE_AUTO_REFRESH=1` 或启动参数 `--auto-refresh`：启动交易时段全市场快照轮询和收盘日线调度。自动更新不是Windows服务，机器休眠/关机会中断；网页关闭不会停止已启动的后台历史进程。仅查看时不启用。
- 全市场日线为滚动五个日历年；主库为不复权价格。分钟数据按需获取，不代表全市场分钟覆盖。上游无有效值时保留缺失，不得造价补齐。
- 首次跨机器验收时，若有代理环境变量，检查它们指向本机可用代理；不得沿用原作者的localhost代理地址。

## 6. 研究逻辑与复现

入口 `app/comovement.py`。固定选取16只当前存续股票，另有沪深300/中证500/创业板ETF；有选择与存续偏差。研究使用单独采集的东方财富后复权（fqt=2）日线，不改主库。前复权对照为fqt=1；现金分红价格调整会改变历史收益分母，两种序列均未独立验证为分红再投资总回报。

按保存的交易日历对齐后计算对数收益率；不填充缺失日、不跨缺失日期计算收益，不删除大涨跌。使用所有19个标的共同有效日期。按时间前80%筛选股票对，后20%回顾性检验。输出样本协方差、Ledoit–Wolf收缩协方差、Pearson/Spearman相关、扣除沪深300ETF线性影响后的残差相关、60交易日滚动相关、层次聚类与PCA。年化因子固定252。置信区间是探索性的区块bootstrap，未进行多重比较校正。

```powershell
# 完全本地复现本轮固定快照，不发网络请求
.\.venv\Scripts\python.exe -X utf8 -m app.comovement --snapshot data/research/20261110T154400 --reference app/static/research/qfq-analysis.json
# 重新抓取当前近5年并生成新研究，会更新研究页静态结果
.\.venv\Scripts\python.exe -X utf8 -m app.comovement
```

研究结果在 `app/static/research/`，原始输入及manifest在 `data/research/`。重算前需要保留旧结果时先复制该结果目录。热力图与CSV均可下载。同步相关只描述历史共同波动，不能据此推断因果、领先关系或可套利价差。

## 7. 代码地图与接手约束

| 路径 | 用途 |
|---|---|
| `app/core.py` | SQLite结构、原始证据、规范化、修订、交易日历、五年窗口 |
| `app/collector.py` / `provider.py` | 隔离的AKShare调用与按需采集 |
| `app/market.py` / `history_source.py` / `backfill.py` | 全市场名录、批量报价、日线与持久队列 |
| `app/process_lock.py` | Windows msvcrt / Unix fcntl非阻塞进程锁；禁止同时运行同类采集器 |
| `app/server.py` / `app/static/` | FastAPI、本地行情界面、独立研究页 |
| `app/github_history.py` / `research/github-data/` | 固定commit目录索引与按需独立GitHub来源；复权未声明 |
| `contracts/` / `design/` | 集成范围、覆盖审计、视觉截图与历史设计；历史设计可能包含尚未实施的目标 |

实际栈为 Python、SQLite、FastAPI、原生JavaScript、KLineChart、pandas/NumPy/SciPy/scikit-learn/Matplotlib。Polars、DuckDB、React等历史规划不是运行时依赖。PyPortfolioOpt与Riskfolio-Lib用于方法参考，本包未直接依赖或宣称实现其完整组合优化器。

修改时保留：来源参数与哈希、拒绝原因、复权标签、真实时点和缺失值。不将批量报价冒充分钟K线，不把抓取时间当成交时间，不混合未声明复权方式的GitHub价格与主库，不承诺无缺口全历史。服务仅本地研究，无账户、交易或下单功能。

不要在运行服务或采集进程时直接复制单独的SQLite主文件；WAL模式可能尚有已提交内容在旁文件中。备份应使用SQLite backup，或停掉全部写入进程后checkpoint再复制。不要删除用户原始数据库、证据文件或任务记录来掩盖启动问题。

## 8. 常见排障

- `ModuleNotFoundError`：确认启动与安装都使用当前目录 `.venv` 内同一个Python；运行doctor。
- `ZoneInfoNotFoundError`：重新安装requirements中的tzdata；Windows默认可能无IANA时区数据库。
- 中文乱码/JSON解码失败：使用 `-X utf8`，设置上文两个UTF-8环境变量；重新解压原文件，不手改JSON编码。
- 绘图中文方框：安装中文字体或设置 `FINANCE_FONT_PATH`，之后重新生成研究图。已有PNG无需本地字体。
- 数据库锁超时：检查重复服务和采集器，等待当前事务完成；保持本地磁盘，禁止通过删除锁文件或清空库解决。
- `busy`：已有同类采集进程占锁，先检查进程；关闭网页不等于停止该进程。
- 价格/接口为空：保留旧数据，读取批次错误与原始响应；上游可变，不能硬编码模拟数据绕过。
- Windows DLL导入失败：确认Python3.11 x64且依赖轮子正确，必要时按官方说明安装Microsoft Visual C++ Redistributable x64；不要下载来源不明的DLL。
- ZIP校验失败、缺文件或SQLite损坏：停止启动，重新传输并校验，不自动以空数据库替代。


## 9. 新增六股技术面：无需全市场库的轻量模式

2026-11-11新增：沪电股份002463、东山精密002384、宏和科技603256、鹏鼎控股002938、江海股份002484、三一重工600031，以及沪深300ETF参照。先安装研究依赖（Windows setup脚本已包括），再执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\doctor.py --technical-only
.\.venv\Scripts\python.exe -X utf8 -m app.technical --snapshot research/technical/snapshot
.\.venv\Scripts\python.exe -X utf8 scripts\run_local.py
```

打开 `http://127.0.0.1:8765/technical`。以上重算完全使用随源码提供的轻量输入，既不访问行情网络，也不读取全市场数据库。正常doctor仍要求全市场数据库；轻量模式使用 `--technical-only`。行情主页的全市场覆盖依然需要完整库或自行采集，不能把轻量模式当作已有全市场历史。

技术价格：前复权OHLC乘固定比例锚定样本末日原始收盘价，用于MA20/60/120/250、Wilder RSI14/ATR14、MACD12/26/9、布林带20/2、前20/60日高低点与量比。价格图不是历史实际报价；收益/相关性与事件结果另外使用后复权代理。两者都未独立核验为分红再投资总回报。官方TA-Lib参考CSV校验RSI、ATR、MACD与布林带；MACD柱使用1倍DIF−DEA。

快照保存源f86时间、小数位f59和昨收，明确与完整日线分开；16:00前不把当天未最终确认的日线加入指标。页面展示实际数据截止日；请求目标日期不代表上游已经完整返回。

固定3个收盘规则和20交易日后观察结果，不是可执行回测；没有交易费用、成交、涨跌停和T+1模拟。首250个交易日预热，前80%日期为形成期，后20%为回顾性留出期；形成期结果不能跨边界，缺持有期任一交易日则排除，同证券同规则事件至少间隔20日。少于10次标记样本不足，不能依据展示的均值或比例宣传预测胜率。

`POST /api/technical/refresh`需要本地操作标记和同源检查，后台互斥运行，失败保留上一份结果。重新联网采集需要先准备交易日历；全量交付库已经包含，纯轻量模式可按第5节日历命令初始化后再采集。修改指标后运行全部测试和 `scripts/check_technical_ui.py`。Windows兼容逻辑保留，尚未做原生Windows实机验收。


### 自选技术面分析

打开 `/technical`，按名称/代码搜索A股或ETF，添加或移除证券后点击“分析所选证券”。支持每次1—12只；单只显示指标和历史事件，多只额外显示共同样本相关性/协方差。结果保存在本地，刷新页面可继续使用；“更新研究数据”更新当前结果中的证券。恢复原六股按钮只修改待分析列表，点击分析后生效。

数据窗口为近5年；新股和停牌不补价，指标窗口不足或共同收益样本不足显示空值。名录之外可以输入完整代码（如 `600519.SH`、`000001.SZ`、`920001.BJ`），能否生成以源端是否提供有效日线为准；尚不支持港美股。名称搜索依赖本地证券名录。新的联网分析仍需要已保存的交易日历。

Windows / Linux 命令行示例（已配置依赖和数据目录）：

```bash
python -X utf8 -m app.technical --symbols 600519.SH 000001.SZ 510300.SH
```

API：`GET /api/technical/search?q=茅台`；`POST /api/technical/refresh`，JSON `{"symbols":["600519.SH","510300.SH"]}`，请求头 `x-workbench-action: local-refresh`。同源限制与并发锁继续有效；无正文保留原默认六股兼容行为。失败保留上一份分析。自选采集不会覆盖仓库内原六股离线输入；当前研究可通过页面下载 `evidence.zip`，解压后用 `--snapshot` 重算。输出目录仍为 `app/static/technical`，每次成功更新替换当前研究，不是多用户隔离存储。
