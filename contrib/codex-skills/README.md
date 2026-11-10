# 可选的 Codex 股票研究 Skills

这是供 Codex 使用的独立研究工具，不是观澜 Oldpig 的主程序模块。文件仅位于 `contrib/codex-skills/`，不修改 `app/`、前端、主行情库、启动脚本或根目录依赖。不会自动注册为 Oldpig 的数据源或接入其网页，也不会自动扫描或上传主库。

## 三个 Skills

| Skill | 解决的问题 | 自动脚本的边界 |
| --- | --- | --- |
| [stock-data](stock-data/SKILL.md) | 通过现有 OpenBB V4、AKShare、可选 Tushare 采集数据，校验证券身份、日期、单位、复权及跨源差异 | 保存原始CSV、标准化结果、来源及失败记录；公告仅为有限近期列表 |
| [stock-research](stock-research/SKILL.md) | 组织商业模式、竞争、行业、财务、估值、行情和风险的中文研究流程 | 生成财务/估值研究底稿；正式公告、行业及商业证据仍需 Codex/研究者核验补全 |
| [stock-quant](stock-quant/SKILL.md) | MA、MACD、Wilder RSI、价格收益、波动率、回撤和收益相关性 | 不自动执行策略或下单；回测目前是方法与验收规范，通用引擎尚未验证 |

三目录须保持同级。`stock-research/scripts/draft.py` 直接复用同级 `stock-quant/scripts/compute.py`，不重复实现指标。`references/` 和 `assets/` 是实际使用的口径文档、情景输入约定及报告模板，安装时一并保留。

## 已完成的测试

- 原项目在贵州茅台 `600519.SH`、五粮液 `000858.SZ` 上完成真实查询，各取得 2021-11-09 至 2026-11-09 的 1,212 个交易日数据，最终各17项成功；唯一不可用项是未配置 Token 的 Tushare。价格交叉核验、TTM/PE重算、情景及敏感性产物通过，24项边界/回归测试通过。茅台指标与首次完整研究一致。
- 原项目临时 Codex 新会话能发现三个 Skills，另一次显式 `$stock-quant` 调用成功。这不是对任意桌面会话自动刷新或自然语言路由的保证。
- 分享版取消本机 `bootstrap.py`、私人配置和绝对路径依赖；在不含原工作区文件的隔离目录通过六项离线测试。分享适配器实际查询茅台、五粮液，各17项成功、1项Tushare未配置；两者各取得1,212日行情，五粮液量化指标及研究底稿生成通过。使用的是已有兼容依赖环境，未声明全新安装或全平台通过；原始行情、完整研究报告和本地日志不上传。

数据供应商会调整接口；以上是指定日期的测试，不保证未来全部接口持续可用，也不是投资业绩验证。

## 依赖与安装

**请使用独立虚拟环境，不要合并到 Oldpig 原有环境。** Oldpig 当前说明推荐 Python 3.11，而本包实测的 OpenBB 扩展要求 **Python >=3.13、<=3.14.3**，实测解释器为3.13.12。

主要版本：OpenBB4.7.1、core1.6.13、openbb-akshare1.2.5、AKShare1.18.56。清单在 [stock-data/requirements.txt](stock-data/requirements.txt)，包含pandas、NumPy、requests和Markdown表格所需tabulate；它不是完整的跨平台传递依赖锁文件。不要直接升级到 OpenBB V5，V5接口和扩展并不兼容本包。

1. 选择一个独立研究目录，把三个 `stock-*` 目录完整复制到它的 `.agents/skills/` 下。不要只复制SKILL.md，也不要分散安装到不同父目录。
2. 在这个研究目录建立单独的Python环境。Windows示例：

```powershell
py -3.13 -m venv .venv-stock-skills
.\.venv-stock-skills\Scripts\python.exe -m pip install -r .agents/skills/stock-data/requirements.txt
.\.venv-stock-skills\Scripts\python.exe -X utf8 .agents/skills/stock-data/scripts/portable_runtime.py --build
.\.venv-stock-skills\Scripts\python.exe -X utf8 -B .agents/skills/stock-data/scripts/test_share.py
```

Linux/macOS使用对应Python3.13和 `.venv-stock-skills/bin/python`。本轮没有在全新系统从零安装、也没有验证Linux/macOS，因此安装和平台兼容仍需接收者验收。

3. 从该研究目录打开新的Codex项目/会话，调用 `$stock-data`、`$stock-research` 或 `$stock-quant`。仅把文件放在仓库 `contrib/` 中不会自动激活；这是可选贡献目录，需要主动复制到技能目录。当前聊天若工作根不同，可能不会发现它们。

## 调用示例

```text
$stock-data 获取000858.SZ五年行情与已披露财务，记录单位和来源。
$stock-research 研究600519.SH，核验公告原文，给出三种情景和敏感性。
$stock-quant 计算五粮液的MA、MACD、RSI及最大回撤，不执行回测。
```

也可直接运行脚本；以下在已安装Skills的独立研究目录内执行，输出目录必须为空：

```powershell
.\.venv-stock-skills\Scripts\python.exe -X utf8 .agents/skills/stock-data/scripts/collect.py --symbol 000858.SZ --asof 2026-11-09 --years 5 --out ./stock-reports/example-run
.\.venv-stock-skills\Scripts\python.exe -X utf8 .agents/skills/stock-quant/scripts/compute.py --bundle ./stock-reports/example-run
.\.venv-stock-skills\Scripts\python.exe -X utf8 .agents/skills/stock-research/scripts/draft.py --bundle ./stock-reports/example-run
```

例中日期用于复核历史测试；省略`--asof`时按上海时间选择已结束日期并返回实际可得交易日，不保证上游已发布最新数据。生成的研究文件明确标为**底稿**。要计算三情景，按 [输入约定](stock-research/references/scenario-input.md) 自建有依据的JSON，再增加`--assumptions`参数；不提供通用“合理PE”默认值。

## 配置隔离与安全

分享版使用自带的 `portable_runtime.py`，不读取作者电脑的bootstrap.py或私人配置。OpenBB4缺少直接可用的项目配置目录环境变量，因此适配文件在导入SDK前重定向其常量；这是依赖V4内部结构的兼容层，未来版本需重新验证。

- 状态默认写入当前工作目录 `.stock-skills-state/`，可通过 `STOCK_SKILLS_STATE_DIR` 指定；保存的是空凭证配置、缓存与导出偏好，不要上传该目录。
- 可选凭证只从当前进程的 `TUSHARE_API_KEY`、`AKSHARE_API_KEY` 读取，不自动保存Token。需要Tushare时先在这个独立环境安装 `openbb-tushare==1.2.2`，再在本机安全设置环境变量并逐接口验证权限。**本轮未配置Tushare Token，权限和限频未验证。** 不要把值写入README、脚本、提交或PR。
- 只对公共历史行情、新闻、搜索取消扩展错误的统一雪球凭证要求；其他需要真实凭证的接口不绕过认证。
- 输出目录、日志、缓存、虚拟环境和密钥不属于分享内容。附带 `.gitignore` 只是辅助；在其他目录运行后产生的数据仍需用户自行排除。
- 本包不连接Oldpig本地数据库、不启动服务、不改Codex MCP配置。**OpenBB MCP尚未连接**，本包直接使用Python SDK；REST需接收者另行配置及确认实际端口。

## 当前限制

支持实测范围是沪深A股普通股票；不承诺北交所、ETF、银行保险、亏损企业、停牌/ST及历史退市股票的完整流程。历史财务按公告日过滤，但供应商回溯更正仍可能有前视偏差；资金流通常只有有限历史，“主力流入”不是机构持仓。

前复权价格收益不是严格的分红再投资总收益。含息总回报计算、通用回测引擎、历史费用/ST/涨跌停/T+1等成交限制下的交易模拟尚未实现或充分验证，不输出伪精确回测绩效。DCF、EV/EBITDA和同行分析需有可靠、适合该行业的输入；脚本不会自动补齐这些研究工作。

本包没有修改Oldpig主程序；新增路径与主程序无运行时依赖。软件包及数据服务各自的许可证、数据使用和再分发条件继续适用，下载依赖不代表取得行情再分发权；不随包复制第三方源码或数据，也不擅自给Oldpig整个仓库添加许可证。本工具不自动交易，不保证收益。
