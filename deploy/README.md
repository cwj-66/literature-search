# 部署与入口隔离

## 普通单人自部署

1. 复制项目根目录 `.env.example` 为 `deploy/.env`，填写自己的服务配置；保持 `PAPER_DEMO=0`。
2. 项目根目录执行 `docker compose -p paper-search -f deploy/compose.yml up -d --build`。
3. 在 `frontend` 中执行 `npm ci`、`npm run build`。不要设置 `PAPER_DEMO_BUILD=1`。
4. 用 Nginx 提供 `frontend/dist`，将 `/api/` 转发到 `127.0.0.1:15179`。参考 `nginx-https.conf` 替换域名、证书与静态目录，先 `nginx -t` 再加载。

默认命名卷 `paper-search_paper_data` 保存持久数据。更新时保留卷，不要执行 `down -v`。后台队列与上游限速按单进程设计，多实例部署需额外实现共享队列。

本地开发不用 Docker 时，将 `.env.example` 保存为 **`backend/.env`**，启动后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --port 8000
```

前端 `npm run dev` 默认将 `/api/` 转发到 8000。依赖安装、服务配置见 [README](../README.md#快速开始)。

## 两个网站同时运行

两个入口必须独立，不把开源子域名重定向到作品集。

| 配置 | 开源子域名实例 | 作者作品集演示 |
| --- | --- | --- |
| 地址 | `papers.chenwj.click` | `chenwj.click/demo/paper-search/` |
| Compose 项目 | `paper-search-opensource` | `paper-search` |
| Compose 文件 | `compose.opensource.yml` | `compose.yml` + `compose.demo.yml` |
| 模式 | `PAPER_DEMO=0` | `PAPER_DEMO=1` |
| 端口 | `127.0.0.1:15180` | `127.0.0.1:15179` |
| 数据卷 | `paper-search-opensource_opensource_data` | `paper-search_paper_data` |
| 配置 | `deploy/.env.opensource` | `deploy/.env` |
| 静态目录 | `/opt/paper-search-opensource/frontend/dist` | `/opt/paper-search/frontend/dist` |
| 前端构建 | 普通路径 `/` | `PAPER_DEMO_BUILD=1`，路径 `/demo/paper-search/` |

独立实例的启动命令：

```sh
# 可选：从 .env.example 复制为 deploy/.env.opensource，填写自己的配置。
docker compose -p paper-search-opensource -f deploy/compose.opensource.yml up -d --build
```

`nginx-opensource.conf` 为独立子域名的 TLS 模板，不代理演示容器，也不读取演示配置或数据。`release_portfolio_demo.py` 不再改写子域名路由，后续发布演示不会重新添加跳转。

作者的公开预览设置 `PAPER_PUBLIC_PREVIEW=1`，LLM、向量、MinerU 和 Semantic Scholar 密钥均为空，不加载示例数据，也不接受个人密钥。自己的私有实例无需设置该标记。

作品集演示依赖作者已有的登录服务与只读会话数据库。普通克隆没有这些私有设施，不要直接叠加 `compose.demo.yml`；使用 `nginx-portfolio-paper.conf` 前需实现自己的登录校验。

## 配置、数据与更新

- `/api/runtime` 只返回运行模式、模型名及配置布尔值，不返回密钥。
- 模型、向量和 MinerU 密钥由服务端读取，修改后需重启。浏览器保存的连接信息不能改变后端配置。
- Semantic Scholar 支持匿名检索；私有实例可在设置页保存可选密钥，公开预览禁止保存。
- SQLite 保存论文、工作区、对话、证据与 QA 索引；相似推荐的 FAISS 索引独立维护。不要随意删除或互换。
- QA 默认子块/重叠/父块为 350/50/1,000 tokens。更换 Embedding 模型需匹配 tokenizer、输入上限和索引版本。
- `LLM_FALLBACK_MODELS` 配置额度耗尽时的备用模型顺序；普通限流、超时和权限失败不会触发切换。可写的 `LLM_FALLBACK_STATE` 用于跨重启保留状态。
- 发布前备份配置与数据库，在线 SQLite 使用 backup API。先上传新 hashed assets，再替换 `index.html`，保留旧 assets。

发布后检查 `nginx -t`、`/api/health`、SPA 深链接刷新、静态资源路径和 `/api/runtime`。子域名不应跳往作品集；两个容器的数据卷与配置不共享。演示入口仍应保留原有认证。

普通版面向单人自部署，不是完整多用户 SaaS。公开使用前需另配认证、权限、上传限制与用户数据隔离；作品集专用访客隔离不等于通用多租户支持。
