# 发现组合：研究工作台视觉改版

2026-11-14。沿用原生 HTML/CSS/JS，与真实行业快照连接。

参考并阅读：
- Anthropic frontend-design：https://github.com/anthropics/skills/tree/main/skills/frontend-design
- UI UX Pro Max：https://github.com/nextlevelbuilder/ui-ux-pro-max-skill
- 本地 CloudBase ui-design / web-development skills。

选择：以研究操作为核心，减少首屏说明层级；白色导航与内容、浅灰画布、蓝色负相关、琥珀色正相关。中文使用 Noto Sans CJK SC / Microsoft YaHei；数字使用 tabular-nums。不引入远程字体、运行时设计依赖或装饰性行情。

变更：统计压成状态条；搜索、窗口、排序作为主筛选；其他筛选和计算说明可展开；五列研究表保留并把“入选理由”改成“研究线索”；零中心刻度展示真实相关系数，同跌占比展示实数比例。手机采用两列证据卡片。公共导航与主题色同步调整。

验证：1440×1000 下首行从旧截图约894px前移到525px。390/760/800/1024/1440宽度无横向溢出；实际浏览器验证搜索、筛选说明展开、热力图、观察页、浅深色切换，无页面脚本错误。截图位于 ../discovery/。

发布：guanlan-api 更新后为 Active。线上登录后复核发现组合页、搜索、说明展开、热力图、观察页、响应式和深色模式通过。公共主题涉及的 /compare、/stocks、/research、/data-health 在本地1440与390宽度通过，无脚本错误。仅修改展示层，数据计算与鉴权接口保持原有实现。
