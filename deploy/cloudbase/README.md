# CloudBase 国内站部署

当前实现采用 Python 3.10 HTTP 云函数（端口9000）＋私有 PG Storage；前端由同一个经过登录保护的服务提供。无需用户安装 Python 或保存行情数据库。

## 数据与账号

- 全市场名录、快照、交易日历以小型 SQLite 种子加载到函数 `/tmp`，只作可重建的查询缓存。
- 近5年标准化行情按16组证券/周期压缩为一个不可变对象，目录记录各证券的数据块及范围；请求只读取需要的数据块。
- 原本已有的行情、最新覆盖报告、原六股分析和证据包先迁移；新抓取响应及批次元数据写入云存储。
- 原有历史批次的完整原始 HTTP 文件未全量迁移；保留标准化行情、批次元数据与源哈希。新抓取及技术研究保存完整证据。
- 每个账号的技术分析放在 `users/<username>/technical/<generation>/`；完整结果和证据全部上传后，才更新该账号的结果指针。失败保留旧指针。
- 服务端用专用 CloudBase API Key 访问私有 bucket，浏览器只持有 HttpOnly、Secure、SameSite=Lax 的签名会话 Cookie。账号密码使用随机盐 PBKDF2-SHA256（200000次）保存摘要；原始随机密码只写操作者本机私有文件。
- 用户账号、签名密钥、CloudBase API Key、CLI 登录凭证均不得提交 Git。部署返回值也可能包含环境变量，输出应保存至权限600的私有日志，汇报只取状态字段。
- 所有账号均可研究和更新行情，不提供 CloudBase 控制台管理权限。每个账号分配给一个人；不要多人共享 owner。

## 可重复部署（Linux / WSL）

本机需 Node.js 18+、CloudBase CLI 3.9.2+、Python 3.10、git。先国内站 `tcb login --intl false`，使用真实 envId。以下路径由操作者设置，示例不包含凭证。

1. 在仓库外创建构建目录和私有配置目录，私有目录权限700。
2. 通过 `tcb env apikey create oldpig-workbench-runtime --expire 0 --json` 签发专用服务端 key；**在脚本中捕获返回值**写入私有目录 `runtime-key.json`，权限600，不要把返回值打印到聊天、终端录屏或提交到仓库。
3. 在 PG Storage 创建私有 bucket `oldpig-finance`。Python 适配器 `CloudStore.create_bucket()` 使用官方存储 HTTP API；bucket保持 `public=false`，浏览器无直接读取权限。
4. 安装打包依赖（生成 Linux x86_64 / Python3.10 兼容包）：

   ```bash
   python -m pip install --target /ABS/BUILD/function --only-binary=:all: --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 --implementation cp --python-version 310 -r deploy/cloudbase/requirements.lock
   ```

5. 使用原有完整数据目录导出和上传：

   ```bash
   FINANCE_DATA_DIR=/ABS/DATA python scripts/cloudbase_data.py --source /ABS/DATA --output /ABS/BUILD/data --key-file /ABS/PRIVATE/runtime-key.json --env-id REAL_ENV_ID
   ```

   导出只取滚动近5年；上传断点写入 `uploaded.json`，失败后使用 `--upload-only` 重试同一批。新批次使用新前缀；只有全部上传成功才替换 `system/current.json.gz`。

6. 准备代码、账号和私有部署配置：

   ```bash
   python scripts/cloudbase_prepare.py --build-dir /ABS/BUILD --private-dir /ABS/PRIVATE --env-id REAL_ENV_ID --origin https://REAL_GATEWAY_HOST
   ```

   首次生成 owner/researcher1/researcher2/researcher3；密码位于私有目录 `accounts.json`，配置位于 `deploy.json`。再次执行复用密码和签名密钥。账号摘要使用 Base64 编码注入 `FINANCE_CLOUD_USERS_B64`，避免 CLI 将 JSON 字符串转换为对象。

7. 捕获部署输出到权限600日志，再部署：

   ```bash
   tcb fn deploy guanlan-api --httpFn --runtime Python3.10 --force --config-file /ABS/PRIVATE/deploy.json --env-id REAL_ENV_ID --json
   ```

8. 给已有默认 `HTTPSERVICE` 域名添加根路径 `/` → `WEB_SCF` → `guanlan-api`（`$LATEST`）。保留路径传递，平台路由鉴权关闭，应用自己检查登录。设置整体60 QPS、单IP 30 QPS上限。**不要编辑环境级函数授权策略来实现网页登录。**

   CLI 3.9.2 的 `routes add` 会先调用只适合自定义域名的 `VerifyHTTPServiceRoute`，系统默认域名会被该预检拒绝。已有默认域的路径绑定应按官方 HTTP 网关 API，使用 `CreateHTTPServiceRoute` 的 `Domain.Routes`；这不创建或购买域名。实际域名必须先从 `tcb routes list` 获取，不可猜测。

9. 验证：`/healthz`只返回固定 `{"ok":true}`；未登录 `/api/technical`应401；登录后验证全市场历史、单股1m/5m/1d更新、两账号分析隔离、下载、退出、手机宽度及函数重启后数据持久性。

## 使用与维护

- 打开交付的 HTTPS 地址；首次出现 CloudBase 测试域名提醒时，等待3秒后点击“确定访问”，再用各自账号登录。默认域名只用于开发测试，提示页和限频是平台行为；长期正式服务应绑定已备案的自定义域名。官方说明：https://docs.cloudbase.net/service/introduce
- 用各自账号登录。主页为行情工作台，顶部可进入技术面研究或共振研究。
- 云函数按请求运行；分钟轮询仅浏览器打开且处于交易时段时更新当前证券。关闭网页后不持续轮询。当前没有全天候全市场分钟归档任务。
- 云端新抓取及技术分析使用腾讯证券公共源；原有东方财富归档仍逐条标明来源。腾讯1分钟接口的量额是源端累计字段，不解释成每分钟成交量。技术分析一次研究中的三种复权价格均来自腾讯，不与东方财富价格混算；各复权源只截取共同发布的末日，缺失成交额保持空值。
- 全市场快照可以手动更新；按当前名录过滤，不用快照冒充分钟K线。证券名录和交易日历需要定期维护并发布新快照，日历未覆盖时不得假设交易日。
- 原有 GitHub 固定版本历史仍独立展示；在国内云端读取 GitHub 可能受网络影响，主历史读取不依赖 GitHub。
- 体验环境额度耗尽或套餐过期可能停服；查看 CloudBase 资源点和到期时间，不能承诺长期免费或免费额度支持全市场永久高频采集。
- 当前是少量受邀用户使用的部署，不是多租户商业服务。每次研究上限12只；较大的计算可能有冷启动或等待。
- 日志服务未开通时，错误会写入私有 `diagnostics/<reference>.json.gz`，包含脱敏异常类别、消息和栈帧，不含请求头、环境变量或签名下载地址。只通过管理员私有存储权限读取，不对网页开放。
- 密码轮换：在私有 `accounts.json` 中为目标账号换随机密码，重新准备并部署。撤销账号则从私有账号文件移除后重新部署。会话检查会拒绝已移除账号。
- 服务端 key轮换：在 CloudBase创建同用途的新 key，更新私有 `runtime-key.json`，重新准备并部署，验证实际存储读取后撤销旧 key。所有权：此项目的 CloudBase账号持有人。

## 策略验证（2026-11-12）

访问同域名 `/strategy`，使用现有账号。实验保存在私有 Storage 的 `users/<user>/strategy/experiments/`；单个对象包含证据、数据、代码版本和结果，函数临时文件丢失不影响档案。列举使用官方 PG Storage v2 object/list API，不需要新增业务表、权限、套餐或环境变量。

本次为净价格收益研究，缺企业行动账本与历史证券状态，完整限制见 `research/strategy/README.md`。引擎/数据处理代码变化会改变代码哈希；旧实验仍可查看下载，但网页拒绝用新版伪装原版本重放。
