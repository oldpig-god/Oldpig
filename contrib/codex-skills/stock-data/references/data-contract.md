# V4接口与数据契约

由两只A股实测流程抽取的接口和数据口径。原始报告和行情数据不随本包分发。

SDK：`obb.equity.price.historical(symbol=code,provider='akshare',start_date=...,end_date=...,use_cache=False)`；未复权省略adjustment，不显式传None；前复权adjustment='qfq'。V4不是V5的提供商命名空间。portable_runtime.py仅使公共历史/新闻/搜索凭证可选，不更改其他凭证要求；状态隔离，Token只从进程环境变量读取。

公共补充：stock_zh_a_hist（日期、股票代码、OHLC、成交量手、成交额元、换手率百分数）；stock_zh_a_daily新浪（volume股、turnover比例），stock_zh_a_hist_tx腾讯仅用未复权收盘核验，不误用其amount字段。不同来源qfq算法不同，未复权可核对；交叉误差不应自动解释成抄错。

三表：stock_profit_sheet_by_report_em / stock_balance_sheet_by_report_em / stock_cash_flow_sheet_by_report_em，symbol为SH/SZ前缀；财务指标stock_financial_analysis_indicator_em传完整后缀代码。金额元，REPORT_DATE报告期、NOTICE_DATE公告日。OPERATE_INCOME≠TOTAL_OPERATE_INCOME，PARENT_NETPROFIT≠NETPROFIT。ROEJQ是累计期间加权ROE，不无条件年化。缺财报季度时同比按日期匹配，不机械shift(4)。金融企业及带财务子公司的CFO和净现金需特别审慎。

估值stock_value_em：股价元、市值元、股本股、PE/PB倍；历史序列不是无前视偏差数据。非正盈利时不强行计算有意义PE。同行异常PE不简单平均。分红stock_fhps_detail_em每10股元，补stock_history_dividend_detail中的已实施事件；同除息日一致性先检查，冲突留档、优先实施公告，不重复相加。

资金流stock_individual_fund_flow：金额元、占比%，通常仅有限历史，不等于机构实际持仓。主营stock_zygc_em：金额元、比例0–1，产品与地区维度不能双计。公告CNINFO返回需核对secCode、去重、区分标题与原文；确认原文后再作事实引用。宏观bond_zh_us_rate为收益率百分数；不得当作股票折现率。

Tushare当前测试可能无Token；有Token才调用，不通过set_token写入配置。daily、daily_basic、adj_factor、income、balancesheet、cashflow等每个权限独立记录。Tushare财务与行情的单位依该接口当前字段文档，不与AKShare盲目合并。脚本将其原始结果保留作备份/核验，未经验证不作为标准化主源。

输出：raw/*.csv保持来源字段；price_raw.csv/price_qfq.csv统一date、close、volume_shares、turnover_pct；financial.csv金额元并包含period和ann_date；bundle.json标明identity、asof、真实latest及queries，crosschecks.json记录误差/覆盖。failed不是空成功。模板报告的未补全研究章节必须显示待核实，不得伪称完整投研。
