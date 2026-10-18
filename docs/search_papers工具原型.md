# search_papers 工具原型

用途：按关键词搜索候选论文，供后续分析或索引步骤使用，不自动导入文献库。

与现有工具一致，工具说明登记在 `app.tools.TOOLS`，Python 入口为 `app.tools.run_search_papers`，HTTP 入口为 `POST /api/tools/search_papers`。

请求示例：

```json
{
  "query": "retrieval augmented generation",
  "year_from": 2022,
  "year_to": 2026,
  "source": "all",
  "sort": "relevance",
  "page": 1,
  "page_size": 10
}
```

只需提供 `query`。来源可选 `all`、`arxiv`、`semantic_scholar`，排序可选 `relevance`、`date`，每页最多 20 篇。年份包含边界，起始年份不能晚于结束年份。

返回 `tool`、`status`、原始 `input_query`、规范化后的 `query`、分页信息及 `papers`。每篇保留论文 ID、标题、作者、摘要、年份、来源、详情与 PDF 链接等已有检索字段；来源未提供的字段可为空。

来源诊断保留在 `source_errors`、`source_warnings`、`source_retry_after` 等字段。`total_estimated` 和 `total_is_limited` 用于识别估计或受限的结果总数。

- `completed`：检索完成，无来源错误或警告；零结果也可为正常完成。
- `partial`：存在来源错误或警告，需结合来源诊断判断覆盖情况。
- `failed`：没有返回论文，也没有正常来源计数，且存在来源错误。

复用现有多源检索、去重、排序与年份过滤能力。部分中文词沿用已有英文映射，不代表接入中文文献数据库。当前为可调用的后端工具原型，尚未接入模型 Function Calling 或修改调研 Agent 的调度流程。
