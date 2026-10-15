# 论文检索平台部署

访问地址：https://papers.chenwj.click

## 本地启动

在项目根目录启动后端（已用 uv 创建 `.venv`）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --port 8000
```

另开终端启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

打开 http://localhost:5173，前端开发服务器将 `/api` 转发到 8000 端口。

## 服务器结构

- 项目目录：`/opt/paper-search`
- 前端：本地执行 `npm ci`、`npm run build` 后，将 `frontend/dist` 上传到同名目录，由 Nginx 提供静态文件，支持详情页刷新。
- 后端：Docker Compose 项目名 `paper-search`，Python 3.12、uv 安装依赖，单进程运行，内存上限 256MB。
- API 端口：仅监听 `127.0.0.1:15179`，由独立子域名下的 `/api/` 转发。
- Nginx 配置：`/etc/nginx/sites-available/paper-search`，通过同名链接启用。
- 证书：`/etc/letsencrypt/live/papers.chenwj.click/`，由 Certbot 管理续期。
- 独立于主域名已有项目和 API 路由。

## 配置与维护

服务器 `deploy/.env` 使用 `SEMANTIC_SCHOLAR_API_KEY=` 配置可选密钥；空值时调用公开匿名接口，前端如实提示共享额度可能限流，不再直接禁用来源。密钥获批后填写并重启后端即可，密钥始终只存后端。`SEMANTIC_SCHOLAR_ENABLED=false` 可由管理员临时停用来源。限流时显示来源错误与重试按钮，保留其他来源的结果。不上传本机 `.env`。

上游请求使用单进程共享排队（arXiv 至少间隔 3.1 秒、Semantic Scholar 至少间隔 1.1 秒），429 遵守 Retry-After，缺省按 2/4/8 秒退避，较长限流进入冷却。相同请求缓存 10 分钟，最多 128 个响应；列表元数据缓存最多 1000 条，详情优先复用。缓存为内存缓存，重启后失效，多进程部署前需改为共享存储。分页按固定 100 条批次缓存、去重后再切页，Semantic Scholar 只访问前 1000 条且每次最多 100 条。

前端支持六个常用中文词的精确映射，并明确提示英文检索；其他中文不自动翻译。引用量、CCF 和中科院分区缺失时按验收要求显示“未知”，不伪造等级；等级映射和筛选留在阶段二。

```sh
cd /opt/paper-search
docker compose -p paper-search -f deploy/compose.yml up -d --build
docker compose -p paper-search -f deploy/compose.yml ps
docker compose -p paper-search -f deploy/compose.yml logs --tail 100 api
nginx -t
```

`nginx-http.conf` 用于证书签发前的 HTTP 路由，证书签发成功后再使用 `nginx-https.conf`。修改配置后必须先执行 `nginx -t`，再平滑加载 Nginx。不要替换主域名的配置。

当前代码包含论文检索、详情、本地库和 AI 分析。相似推荐与知识图谱仍待开发。分析任务使用进程内队列，必须保持 Dockerfile 中的单进程；增加 worker 会让队列分裂，重启后未完成的准备或分析会被标为失败并可重试。线上是否包含这些更新取决于实际部署版本。


## 安全头与静态路由

更新前端、后端以及对应的 Nginx 配置后生效。两份 Nginx 模板均限制 SPA 入口为 `/`、`/paper` 和 `/library`，其他不存在的资源返回真实 404。`/robots.txt` 为真实文件，保留现有禁止索引策略，因此不生成 sitemap，`/sitemap.xml` 明确返回 404。`/favicon.ico` 和 `/favicon.svg` 为真实图标，`/health` 代理后端 `/api/health`，返回 JSON；`/docs`、`/redoc`、`/openapi.json` 关闭并返回 404。

安全头集中在 server 级，子 location 不使用 add_header，防止覆盖继承。包含 CSP、DENY、nosniff、Referrer-Policy、Permissions-Policy，HTTPS 模板另加 HSTS。CSP 允许 Ant Design 必需的内联样式，脚本仅允许同源。

本地回归：

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
npm --prefix frontend run build
```

上线时须运行 `nginx -t` 后再重新加载，并检查首页、静态资源及 404 响应的安全头；本地 Vite 开发服务器的 SPA fallback 不代表 Nginx 生产路由行为。


## 本地分析库与导入接口

使用 SQLite 保存论文元数据、摘要、外部标识、数据来源、内部 ID 和入库时间。桌面开发默认文件为 `backend/data/papers.sqlite3`（已忽略提交）；可用 `PAPER_DB_PATH` 指定其他文件。Docker 使用 `/app/data/papers.sqlite3`，挂载 `paper_data` 命名卷，容器重建后保留。更新时同时使用本次 Dockerfile 和 Compose 文件，确保目录属于 UID 10001，且不删除已有命名卷。

页面 `/library` 可通过 ID / URL 导入；详情页可点击“加入本地分析库”。已入库详情优先读取数据库，网络不可用或后端重启不影响查看。按 arXiv / DOI / Semantic Scholar 外部标识去重，重复导入返回同一内部 ID，不重复新增。导入只保存元数据和摘要；全文下载和 AI 分析在详情页单独触发。

| 请求 | 参数 / 结果 |
| --- | --- |
| `POST /api/papers/import` | JSON `{ "external_id": "arxiv:2005.11401" }`，也支持单独提供 `url`、`id`、`arxiv_id` 或 `doi`，这些字段只能选一个 |
| 新增成功 | HTTP 201，`{ "paper": { ...论文字段, "local_id": "内部ID", "imported_at": "UTC时间" }, "created": true }` |
| 重复导入 | HTTP 200，`created: false`，返回已有 `local_id` |
| `GET /api/papers/{id}` | 读取已入库论文；id 可为 local_id 或已保存的外部 ID，路径特殊字符需编码；未入库返回中文 404 |
| `GET /api/papers?page=1&page_size=20` | 本地库分页列表，含 papers / total / page / page_size |
| `GET /api/papers/detail?id=arxiv:...` | 保留兼容的外部详情接口 |

导入先将参考标识规范化，仅向固定的官方 API 发出请求；不直接获取用户给出的任意 URL。导入本身不下载 PDF，也不调用大模型。外部请求超时、参数错误或本地库故障均返回中文错误。新增 POST 还用于创建分析任务，CORS 仍不开放 DELETE / PATCH 等方法。

详情页可对已入库或尚未入库的论文执行 AI 分析。任务立即返回，页面轮询状态：准备文本、分析中、完成或失败。全文只从允许的公开站点获取（arXiv HTML/PDF，以及 OpenReview、ACL Anthology、PMLR、PMC、Europe PMC 的开放 PDF），不绕过登录或付费墙；单份文件不超过 8MB。无法取得合格全文时改用摘要，并写明原因。模型输出经 Pydantic 校验，格式错误只自动修复一次，仍失败则不保存为成功结果。重新分析失败时保留上一版成功结果。

| 请求 | 参数 / 结果 |
| --- | --- |
| `POST /api/papers/{id}/analyze` | JSON `{ "reanalyze": false }` 可省略。新建或复用进行中任务返回 202；已有成功结果且未要求重跑时返回 200 |
| `GET /api/papers/{id}/analysis` | 最新任务和最新成功结果。尚无任务时两者都为空 |

年份筛选使用明确括号和逐关键词 AND 的 arXiv 查询，完整覆盖起止日期当天；分页前对两个来源的 year 再做闭区间检查。有年份筛选时排除年份缺失记录，缺少年份不能作为满足范围的证据。供应商统计与本地校验/去重可能不同，筛选后的总数显示估计值。
