# parse_paper

通过 MinerU 云 API 将 PDF 转为结构化正文，不调用大语言模型。

## 调用

Python：`await run_parse_paper(pdf_bytes, filename="paper.pdf")`，从 `app.tools` 导入。

HTTP：`POST /api/tools/parse_paper`，使用 `multipart/form-data`，文件字段名为 `file`。

```sh
curl -X POST http://localhost:8000/api/tools/parse_paper -F "file=@paper.pdf"
```

服务端配置 `MINERU_API_TOKEN`；可选配置 `MINERU_API_BASE`、`MINERU_MODEL`、`MINERU_POLL_LIMIT`（默认 120）、`MINERU_POLL_INTERVAL`（默认 2 秒）。文件必须以 PDF 标识开头，最大 50MB。上传文件会发送至 MinerU 云服务。

## 结果

- `tool`：`parse_paper`；`parser`：`mineru`。
- `status`：`completed` 或 `partial`，后者附带 `warnings`。
- `filename`：本地展示文件名；云端统一使用 `paper.pdf`。
- `markdown`：MinerU 的完整 Markdown。
- `text`：按原顺序拼接的内容文本，包含表格正文、图注和公式。
- `blocks`：`id`、`type`、`text`、`page_number`、`bbox`、`heading_level`、`section_id`。
- `sections`：`id`、`title`、`level`、`parent_id`、`page_start`、`page_end`、`block_ids`、`text`。章节正文和块列表只收录直接所属内容；页码范围覆盖子章节。
- `page_count`：根据 MinerU 内容列表中最大页索引推算的覆盖页数，不保证包含末尾空白页。

读取结果 ZIP 中的 `full.md` 和 `*_content_list.json`（MinerU v1 内容列表）。`page_idx` 从 0 转为从 1 开始的 PDF 物理页码，不是论文印刷页码。`bbox` 保留 MinerU 原始坐标，不自行转换。标题层级按 MinerU 的 `text_level` 保留。

没有内容列表时，从 Markdown 提取章节并返回 `partial`；页码和坐标为 `null`，不根据文本猜测。结构化 JSON 损坏时返回解析错误。

非法文件返回 HTTP 400；MinerU 未配置、调用失败或超时返回 HTTP 502。本工具只负责解析，不写文献库、不建立向量索引；上传和后台导入流程复用结构化解析结果，并另行保存正文、结构和索引。
