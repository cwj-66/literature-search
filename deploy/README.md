# 部署说明

复制根目录 `.env.example` 为 `deploy/.env`，填写需要的服务配置：

```sh
docker compose -p paper-search -f deploy/compose.yml up -d --build
```

前端在 `frontend` 中执行 `npm ci`、`npm run build`，由 Nginx 提供 `dist`。API 默认绑定 `127.0.0.1:15179`；模板 `nginx-https.conf` 中需要替换域名、证书与静态目录，运行 `nginx -t` 后再加载。

## 在线配置

设置页支持大模型、向量/重排、MinerU 与 Semantic Scholar 的密钥。保存后立即生效；接口只返回配置状态，不返回密钥。自部署也可使用环境变量。

单人实例使用默认 `PAPER_USER_SESSIONS=0`。面向多个浏览器时设置 `PAPER_USER_SESSIONS=1`：服务端设置 HttpOnly 会话 Cookie，每个会话有独立数据库、配置、向量缓存、领域报告和后台任务范围。会话中的付费密钥为空时不会回退到主机密钥。浏览器缓存按同一会话命名空间隔离。

会话模式提供匿名隔离，不提供账户注册、跨设备登录或密码恢复。Cookie 是访问凭据，正式部署请启用 HTTPS，并根据需要补充账户认证和速率控制。托管实例只接受已支持的官方 API 地址，避免任意网关造成内网请求；私有部署可配置自定义 HTTPS 网关。

## 数据与更新

SQLite 保存论文、对话、证据和 QA 索引；相似推荐使用独立的 FAISS 索引。保留命名数据卷，更新时不要执行 `down -v`。在线数据库备份请使用 SQLite backup API。

会话模式使用数据目录下的 `users` 子目录，密钥随所属数据库保存，数据库文件设为仅属主可读写。`session-secret` 用于命名空间计算，应与数据卷一同备份，不要提交到 Git。

前端先上传 hashed assets，再替换 `index.html`；保留旧 assets。检查 `/api/health`、`/api/session`、`/api/runtime`、SPA 深链接刷新，以及两个新浏览器会话的配置隔离。
