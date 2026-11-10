# 第二版金融工作台预览

`workbench-preview.html` 是本轮对话中的交互界面源文件（HTML fragment），不是已经部署的生产应用。它加载固定版本 KLineChart 9.8.12，行情由本地确定性模拟序列生成，不请求金融API。

已有功能：自选搜索/分类/排序、证券联动、日线/周线/5分钟、MA/VOL/MACD、缩放与十字线、图表专注模式、ETF表排序、同源行情明细、真实探测快照、亮暗主题。

预览文件按 visualize 运行环境编写，依赖宿主提供的图标和可选状态保存。正式应用落地时应迁移至React组件，使用npm锁文件固定KLineChart版本，并将行情生成器替换为已校验API；保留演示与真实数据的显式模式隔离。

源码在项目目录与本轮聊天的可视化目录保存相同快照。第一版未覆盖。检查脚本位于 `../design/v2/check_preview.py`，使用临时渲染器生成的 `/tmp/finance-workbench-v2-preview.html`；该文件并非持久交付，重跑前需使用本机visualize的render.py生成。

图表库： https://github.com/klinecharts/KLineChart ，Apache-2.0。项目使用其公开API和发行包；没有复制Wealthfolio、Ghostfolio、OpenStock或Fincept的应用源代码。
