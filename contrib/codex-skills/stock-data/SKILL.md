---
name: stock-data
description: 获取和校验中国股票行情、已披露财务、公告、估值及宏观数据，使用现有OpenBB V4、AKShare和有权限的Tushare。适用于取数、数据核验，或作为stock-research和stock-quant的数据层；不生成投资结论、不安装软件、不交易。
---

# 金融数据采集与校验

使用独立的Python 3.13环境，依赖清单位于本技能requirements.txt，详细安装步骤见源码发行包README。采集脚本通过同目录portable_runtime.py初始化OpenBB V4，不依赖作者电脑、bootstrap.py或Oldpig应用。先读取[接口与口径](references/data-contract.md)。

```sh
python -X utf8 .agents/skills/stock-data/scripts/collect.py --symbol 000858.SZ --asof 2026-11-09 --years 5 --out ./stock-reports/example-run
```

默认状态目录为当前工作目录下.stock-skills-state，可用STOCK_SKILLS_STATE_DIR指定。不要提交该目录或采集结果。可选Token只从TUSHARE_API_KEY、AKSHARE_API_KEY进程环境变量读取，不读取私人配置文件、不自动保存Token。首次环境需要构建时，执行同目录portable_runtime.py --build；不要升级到OpenBB V5。

- 代码交易所须相符；从返回的证券代码/名称验证身份，不能以请求参数或返回非空代替标的校验。支持SH/SZ的A股代码；其他市场需另核对接口，不猜映射。
- 默认截止最近已完成交易日；发现停牌/滞后时报告真实最新日，不补齐假交易。财报按公告日过滤，缺公告日隔离，不能只看报告期。保留原始CSV，标准化另存。
- 优先现有SDK；本包不启动REST或MCP；如接入自行运行的REST需核对实际端口。补充AKShare原始换手率及三表；Token缺失或接口无权时记录限制、继续合法公共源，不填假密钥。脚本只读取现有Token，逐接口记状态，不声明Tushare全权限。
- 元/万元/亿元、手/股、百分数/比例必须明确。不同复权供应商不得拼接；技术面与原始报价分开，含息收益需另核算。
- 只写空的新输出目录，拒绝覆盖；按查询记录来源、参数、抓取时间、单位、复权、数据日期、错误和交叉差异。错误不能泄露凭证。官方披露优先，公告分页去重，新闻标题不作核实事实。
- 财务身份、OHLC、日期、唯一键或跨源检查失败时隔离数据并报告；不静默“修好”原值。供应商有误、来源不足、修订导致前视风险时明确限制。

结果交给stock-research或stock-quant；原始茅台案例仅作为回归基准，不能复制其公司结论、估值假设或URL到其他标的。
