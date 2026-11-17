# 云端维护交接：给接手 Agent

## 入口与当前目录

用户已明确：以当前目录代码为准，其中包含其他 Agent 的修改。开始时读取 `git status --short` 和 `git diff`，确认改动范围，再修改或构建。发布使用当前工作目录；记录提交号、未提交差异和构建时间。

- 本机仓库：`/home/xietianyou/work/oldpig-upload`
- GitHub：`git@github.com:oldpig-god/Oldpig.git`
- 网站：<https://prime-elk-d3g17lc3y0b140937-1458390455.ap-shanghai.app.tcloudbase.com>
- CloudBase：**国内站**，地域 `ap-shanghai`
- 环境 ID：`prime-elk-d3g17lc3y0b140937`
- 控制台：<https://tcb.cloud.tencent.com/dev?envId=prime-elk-d3g17lc3y0b140937>

服务部署为云函数，日常更新通过 CloudBase CLI/MCP 完成。前端静态文件随 HTTP 函数一起发布。

## 资源对应关系

| 资源 | 用途 | 仓库入口 |
|---|---|---|
| `guanlan-api` | 网页、登录、研究 API | `app/cloud_server.py`、`app/static/` |
| `guanlan-daily-sync` | 股票/ETF日线与行业数据采集 | `deploy/cloudbase/daily-worker/index.py`、`app/daily_sync.py` |
| 私有 PG Storage bucket `oldpig-finance` | 行情、用户研究、任务状态、盈利研究档案 | `app/cloud_store.py` |

HTTP函数现有私有配置：Python3.10、120秒、1024MB，监听9000。日更函数的部署文档记录为Python3.10、240秒、256MB；发布前读取云端当前配置核对。函数 `/tmp` 是可重建缓存，持久数据在私有存储。

## 凭证在哪里

本机私有目录：`/home/xietianyou/.config/oldpig-cloud/`。

| 文件 | 内容与用途 |
|---|---|
| `deploy.json` | HTTP函数部署配置及运行环境变量；现有 `functionRoot` 为 `/home/xietianyou/work/oldpig-cloud-build`，函数子目录 `function` |
| `runtime-key.json` | 专用服务端存储API Key，供打包脚本读取 |
| `accounts.json` | 网站受邀账号；网页登录使用 |

三个文件保留在仓库外。接手Agent核对文件存在、JSON结构和环境变量名称即可；发布日志只汇报状态字段。包含密钥的原始输出写入权限600的私有日志。

HTTP运行环境包含：

- 存储：`CLOUDBASE_ENV_ID`、`CLOUDBASE_APIKEY`、`FINANCE_CLOUD_BUCKET`
- 登录：`FINANCE_CLOUD_USERS_B64`、`FINANCE_SESSION_KEY`
- 服务：`FINANCE_PUBLIC_ORIGIN`、`FINANCE_DATA_DIR`
- 采集与运行参数：`FINANCE_TECH_PROVIDER`、`FINANCE_SOURCE_WORKERS`、`OPENBLAS_NUM_THREADS`、`PYTHONUTF8`、`PYTHONIOENCODING`

日更函数使用同一环境的前三个存储变量。发布时保留云端已有其他变量。网页账号、CLI/MCP登录和服务端API Key是三类不同凭证。

换电脑接手：克隆仓库，登录CloudBase国内站，由账号持有人通过私有文件传递配置，或在新机器重新配置专用Key和现有账号。网站登录账号不具备云资源部署权限。

## 发布HTTP服务：现有机器

先阅读 `deploy/cloudbase/README.md`、本次涉及模块的文档，以及可用的CloudBase技能。已有MCP可先读登录状态和函数配置；本节CLI命令用于现有私有配置。

```bash
cd /home/xietianyou/work/oldpig-upload
git status --short
git diff --stat
python3 -m unittest discover -s tests
```

当前构建目录已有Linux依赖。更改依赖时，按README中的Linux x86_64/Python3.10命令安装到 `function`；核对 `deploy/cloudbase/requirements.lock`。构建包中的旧源码需要从当前目录重新复制。

准备包的现有脚本也会上传技术研究静态档案，并重新生成账号密码摘要；执行前确认当前技术档案是准备发布的版本。脚本复用私有账号密码和会话密钥。

```bash
python3 scripts/cloudbase_prepare.py \
  --build-dir /home/xietianyou/work/oldpig-cloud-build \
  --private-dir /home/xietianyou/.config/oldpig-cloud \
  --env-id prime-elk-d3g17lc3y0b140937 \
  --origin https://prime-elk-d3g17lc3y0b140937-1458390455.ap-shanghai.app.tcloudbase.com
```

CLI使用国内站登录。会话过期时执行 `tcb login --intl false`，由账号持有人完成授权。随后捕获部署结果到私有日志：

```bash
umask 077
tcb fn deploy guanlan-api --httpFn --runtime Python3.10 --force \
  --config-file /home/xietianyou/.config/oldpig-cloud/deploy.json \
  --env-id prime-elk-d3g17lc3y0b140937 --json \
  > /home/xietianyou/.config/oldpig-cloud/deploy-api-last.log 2>&1
```

当前域名与根路径路由已配置。普通代码发布沿用现有绑定。

## 发布每日采集函数

先读 `deploy/cloudbase/daily-worker/README.md` 和 `docs-industry-discovery.md`。本机包目录为 `/home/xietianyou/work/oldpig-cloud-build/guanlan-daily-sync`；该目录是构建产物，源代码从当前仓库复制。

日更包需包含入口 `index.py`、日更及健康检查/行业模块和它们的运行依赖。行业采集依赖本地JS资源和MiniRacer Linux库。Windows构建时也要打入Linux x86_64/Python3.10依赖。

使用CloudBase MCP读取现有函数配置，更新代码包并保留环境变量、240秒超时及现有触发器。当前仓库只有HTTP私有部署配置；日更函数使用单独配置或MCP发布，不能把HTTP配置直接套用到日更函数。

现有定时器：`daily-incremental`，表达式 `0 */5 * * * * *`。代码按北京时间限制采集时段。发布后观察下一次定时执行与进度推进。任务开关位于私有对象 `system/daily-sync/config.json.gz` 的 `enabled`。

## 验收与回滚

1. `/healthz`返回 `{"ok":true}`。
2. 未登录访问研究API返回401；使用现有账号登录后检查本次涉及页面/API。
3. 前端改动检查桌面与手机宽度；采集改动检查任务心跳、目标日期及进度。
4. 盈利研究数据发布与网页函数发布分别记录。模型离线训练，上传冻结数据/实验不等于上线新的预测页面。
5. 记录源代码版本、未提交文件、云函数更新时间、验证结果；更新相关部署文档。

发布前保留上一版代码包和私有配置。回滚时重新发布该包，再运行同一组验收。行情及研究记录继续保存在私有存储，代码回滚不回退数据。

## 可直接交给其他Agent的任务文字

> 请接手当前目录项目。先读 foragent.md 和 deploy/cloudbase/FOR_AGENT.md，核对 git status、现有差异和云端函数配置。CloudBase使用国内站，环境 prime-elk-d3g17lc3y0b140937，主函数 guanlan-api，日更函数 guanlan-daily-sync。私有配置在本机 ~/.config/oldpig-cloud/。按当前工作目录构建，完成修改、测试、发布和线上验证，记录发布版本及结果。

## 最近发布记录（2026-11-17）

- **源码与优化内容**（基于 `de87f22` 合入最新工作台后端与前端改版）：
  - 行情存储与查询：引入列式 `bars` 表与 zstd/zlib 分块存储（`app/bars.py`、`scripts/migrate_bars.py`、`scripts/audit_history_window.py`），兼容 `observations` 与无旧表的云端 seed 库；`app/core.py`、`app/market.py`、`app/backfill.py`、`app/spot_source.py`、`app/github_history.py`、`app/health_service.py`、`scripts/doctor.py`、`scripts/cloudbase_data.py` 同步升级。
  - API 优化：`app/server.py` 与 `app/cloud_server.py` 增加 `series_stats` 快速健康统计、`GET /api/universe?sort=...` 服务端排序（`code`/`name`/`pct_desc`/`pct_asc`/`amount_desc`，非法值返回 400）、单连接 `GET /api/series/{symbol}` 复用、`batch` 解析校验与 `parsed_issues` 返回。
  - 前端改版：`app/static/index.html`、`app/static/app.css`、`app/static/app.js` 与 `app/static/research/` 同步至工作台 v3 设计（保留 `shell.js` 侧栏/`theme.css` 双主题同步、云端登录/401 跳转与只读批次回显），并修复 `/` 在手机宽度（390×844）下 `.gl-work`/`.gl-main` 引起的水平溢出。
- **云端发布状态**（环境 `prime-elk-d3g17lc3y0b140937`，国内站 `ap-shanghai`）：
  - `guanlan-api`（`lam-rt70mht9`）：Modification time `2026-11-17 17:13:17`，Status `Deployment completed`（构建目录 `/home/xietianyou/work/oldpig-cloud-build`，未泄露/未提交任何私有配置；保留 `deploy.json.bak` 备份）。
  - `guanlan-daily-sync`（`lam-idj2c4wx`）：Modification time `2026-11-17 17:06:40`，Status `Deployment completed`（保留既有触发器、环境变量与 240s 超时配置）。
- **验证结果**：
  - 本地单测与语法：`python3 -m unittest discover -s tests` 全部 330 项通过；`node --check app/static/app.js` 与 `app/static/research/research.js` 通过。
  - 响应式与界面检查：无头 Chromium 桌面 `1440×900` 与手机 `390×844`（`/`、`/static/research/index.html`）`scrollWidth === innerWidth` 无水平溢出、无运行时错误。
  - 线上验收：`/healthz` 返回 `200 {"ok":true}`；未登录 `/api/technical` 与 `/api/universe` 返回 `401`、`/` 返回 `303 /login`；登录后 `/api/health`、`app.css`（含 `minmax(0,1fr)` 修复）、`/api/universe?sort=pct_desc&limit=5`、`/api/series/510300.SH?interval=1d&limit=10`、`/api/daily-sync/status`（`target_date=2026-11-16`、`current=7231/7246`）及 `/stocks`、`/compare`、`/discover`、`/data-health`、`/technical`、`/strategy` 均返回 `200`。
  - 盈利预测离线实验文件（`docs-earnings-residual.md` 及 `research/earnings/residual-*` 等）与本次网页/日更发布分离记录。
