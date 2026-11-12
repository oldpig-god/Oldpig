# 观澜金融终端：可复用前端设计提示词

为现有 FastAPI + 原生 HTML/CSS/JavaScript 金融研究程序改善界面，保留已验证的数据与交易逻辑。

用户是少量中国内地研究者：先搜股票，再看真实价格、日期、指标与数据质量，最后检验策略。不要把更多装饰当作更专业。

- 桌面使用固定窄侧栏、紧凑顶栏、突出图表的主工作区；手机改抽屉导航和单列。顶部支持代码/名称搜索及键盘选择。
- 深色采用低饱和墨蓝背景，浅色采用暖灰白。蓝色表示交互，金色少量强调；涨跌保持中国市场红涨绿跌。两种主题均有清楚对比、焦点状态和可读的小字。
- 使用本地系统中文字体、等宽数字、统一间距和少量细边框。不依赖境外字体/CDN，不使用无意义渐变、巨大口号、重复卡片或表情符号图标。
- 证券名称、交易日期、价格口径、最新数据时间必须可见。指标只显示真实计算结果；不足显示原因。快照与历史日线不得混成同一时点。
- 策略页按收益与风险、稳健性检验、交易账本、方法与证据组织。让重要限制靠近结果，复杂参数可折叠但不能隐藏假设。
- 加载、空数据、错误、停牌、缺失和不支持回测都应有明确状态。使用真实样本验证，禁止为了美观编造价格或结论。
- 保留 ARIA、键盘导航、主题持久化、减少动画设置；在1440px桌面和390px手机验收，检查横向溢出、控制台与静态资源。

## 参考来源与使用边界

阅读并借鉴设计方法，未安装或执行外部提示词，也未复制应用源代码：
- [Anthropic frontend-design](https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md)：明确视觉方向、避免模板化界面。
- [UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)：信息层级、交互状态和可访问性检查。
- [Wealthfolio](https://github.com/wealthfolio/wealthfolio)：金融信息密度和图表布局参考；未复制其 AGPL 应用代码。
- [OpenStock](https://github.com/Open-Dev-Society/OpenStock)：证券搜索与行情工作区参考。
- [Lucide](https://github.com/lucide-icons/lucide)：实际使用14个本地SVG，许可证与文件哈希随仓库保存。
