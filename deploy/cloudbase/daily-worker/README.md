# 每日增量采集

函数：guanlan-daily-sync，Event / Python3.10 / index.main / 240秒 / 默认256MB。不设HTTP路由。现有 guanlan-api 只读其结果，账号认证继续生效。

## 环境变量与启用

从现有 guanlan-api 原样复制 CLOUDBASE_ENV_ID、CLOUDBASE_APIKEY、FINANCE_CLOUD_BUCKET。密钥只进入控制台环境变量，不进仓库。入口默认 /tmp/oldpig-daily-sync 缓存和20秒存储超时。

当前部署阶段：函数与凭证已配置，daily-incremental定时器已创建，enabled=true。凭证配置经过用户明确授权，仅在一次独立MCP进程中放行updateFunctionConfig的凭证检查；进程结束后放行随之失效，未修改常用MCP配置。实际自动执行结果见验收记录。

重新部署时由Agent核验三个变量存在（不可打印值），启用私有对象 system/daily-sync/config.json.gz 的 enabled，并用 manageFunctions createFunctionTrigger 创建 timer：名称 daily-incremental，config `0 */5 * * * * *`。代码以Asia/Shanghai限制07:00—09:59、17:30—23:59采集，避免依赖平台cron时区解释。禁用时将enabled设false，或删除该触发器。启用前后均需检查函数Active、240秒超时、首次Timer执行与进度实际推进。

## 设计

- 当前名录每只证券加入目标交易日队列，优先8只关注证券。当天17:30前目标为上一交易日，之后目标为当天；节假日由真实日历判断。日历越界停止并显示 calendar_unknown；当前种子日历到2026-12-31，需要在到期前补充可核验的新年日历，绝不按周一到周五猜测。
- 每次最多160只、4条网络并发、110秒软预算；每4只保存进度，240秒平台硬超时。失败15/60/180分钟退避，每个目标日最多4次，下个目标日重新尝试。停牌/无返回日线保留为未解决，不伪造零涨跌。
- 原始档案保持不变。scheduled/{symbol}/1d.json.gz只保存增量和重叠尾部；首次从归档末日之前10天拉取，以后从最早待补缺口/水位前10天拉取。完整原始响应及哈希保存为证据。
- 原始档案、scheduled与手动live三层按event_time合并，同一日取fetched_at较新的记录，防止手动与调度互相覆盖。仅保存近五年；缺口显式报告。
- PG Storage POST x-upsert=false创建5分钟槽位声明，重复投递不能同时写队列。只在槽位前45秒取得声明后执行，平台240秒硬超时确保下一槽位前终止。不得把超时调至255秒或更大；修改调度间隔必须重做并发分析。SCF独占配额接口在此环境报服务错误，未依赖该能力。
- 每槽位一个很小的声明对象保留以阻挡旧投递；原始采集证据持续累积，需要根据实际存储用量设置后续保留政策，当前不删除审计证据。
- 免费源没有完整性SLA；全市场完成情况由current/pending/retry/exhausted计数报告，不承诺每只都更新成功。只采日线，不承诺分钟实时同步。

## 预算与验证

没有新增常驻服务器、套餐升级或预置并发。新增按量Event函数、存储读写与网络请求会消耗现有套餐/账单额度；批量、并发、时段和重试均有限制，不能声称永久免费。

`python -m unittest discover -s tests`；tests/test_daily_sync.py覆盖切日、周末、未知日历、回退保护、失败重试、重启恢复、内部缺口、预算截止。云存储原子创建实测首次成功、重复失败。8只真实补采结果见 design/daily-sync/live-validation.json。

## 本次上线验收

2026-11-12 17:55（北京时间）Timer自动运行，17:56:50完成160只；累计168/7246只current，7078只pending，retry/exhausted均为0。这是首轮状态，不能视为全市场已完成。126项Python测试、14项终端浏览器检查、5项云端页面/鉴权检查、3项定时页面刷新检查通过。全量进度持续在页面展示。
