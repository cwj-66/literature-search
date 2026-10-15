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

服务器 `deploy/.env` 使用 `SEMANTIC_SCHOLAR_API_KEY=` 配置数据源；空值时暂时停用 Semantic Scholar，全部来源只查询 arXiv，网页显示真实可用状态。配置密钥后自动启用；供应商限流时显示警告并保留 arXiv 结果。不上传本机 `.env`。

上游请求使用单进程共享排队（arXiv 至少间隔 3.1 秒、Semantic Scholar 至少间隔 1.1 秒），429 遵守 Retry-After，缺省按 2/4/8 秒退避，较长限流进入冷却。相同请求缓存 10 分钟，最多 128 个响应；列表元数据缓存最多 1000 条，详情优先复用。缓存为内存缓存，重启后失效，多进程部署前需改为共享存储。分页按固定 100 条批次缓存、去重后再切页，Semantic Scholar 只访问前 1000 条且每次最多 100 条。

前端支持六个常用中文词的精确映射，并明确提示英文检索；其他中文不自动翻译。不提供未接入数据的引用量、CCF 和中科院占位值。

```sh
cd /opt/paper-search
docker compose -p paper-search -f deploy/compose.yml up -d --build
docker compose -p paper-search -f deploy/compose.yml ps
docker compose -p paper-search -f deploy/compose.yml logs --tail 100 api
nginx -t
```

`nginx-http.conf` 用于证书签发前的 HTTP 路由，证书签发成功后再使用 `nginx-https.conf`。修改配置后必须先执行 `nginx -t`，再平滑加载 Nginx。不要替换主域名的配置。

当前部署只包含已有的论文检索与详情功能；数据库入库、AI 分析、推荐和知识图谱仍待开发。


## 安全头与静态路由

更新前端、后端以及对应的 Nginx 配置后生效。两份 Nginx 模板均限制 SPA 入口为 `/` 和 `/paper`，其他不存在的资源返回真实 404。`/robots.txt` 为真实文件，保留现有禁止索引策略，因此不生成 sitemap，`/sitemap.xml` 明确返回 404。`/favicon.ico` 和 `/favicon.svg` 为真实图标，`/health` 代理后端 `/api/health`，返回 JSON；`/docs`、`/redoc`、`/openapi.json` 关闭并返回 404。

安全头集中在 server 级，子 location 不使用 add_header，防止覆盖继承。包含 CSP、DENY、nosniff、Referrer-Policy、Permissions-Policy，HTTPS 模板另加 HSTS。CSP 允许 Ant Design 必需的内联样式，脚本仅允许同源。

本地回归：

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
npm --prefix frontend run build
```

上线时须运行 `nginx -t` 后再重新加载，并检查首页、静态资源及 404 响应的安全头；本地 Vite 开发服务器的 SPA fallback 不代表 Nginx 生产路由行为。
