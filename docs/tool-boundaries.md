# 工具所属模块

对话 Agent 只提供普通回答与论文搜索。以下目录和执行入口互不合并。

| 所属模块 | 工具 | 接口 |
| --- | --- | --- |
| 对话 | `decompose_question`、`search_papers`、`rerank_papers`、`assess_coverage` | `GET /api/tools`；`POST /api/research/tools/{tool_name}` |
| 论文关系 | `get_paper_details`、`find_related_papers`、`get_paper_references`、`get_paper_citations` | `GET /api/paper-relations/tools`；`POST /api/paper-relations/tools/{tool_name}` |
| 知识图谱 | `query_knowledge_graph`、`analyze_research_trends` | `GET /api/knowledge-graph/tools`；`POST /api/knowledge-graph/tools/{tool_name}` |

POST 请求沿用 `{"arguments": {...}, "context": {...}}` 结构；各模块分别校验工具名与输入范围。图谱工具使用明确的论文范围，不代表全库或整个领域。

`assess_coverage` 需要运行中的对话任务状态，由 Agent 执行器提供；单独通过通用接口调用且没有任务状态时返回 400，不能由客户端伪造一个全库评估。

对话目录、执行注册表和允许集合都只有四个工具。领域标签、旧上下文和模型输出不能扩大这个集合；模块工具经旧的对话工具 URL 调用会返回 404。

旧兼容名 `save_papers_to_workspace` 和 `request_deep_analysis` 已从对话执行注册表移除。路由固定清空分析、保存等后续操作，非普通回答只进入论文搜索分支。旧研究流程的内部代码尚保留，不再由新对话的路由启用；本次没有全面删除旧节点或更改独立页面的数据服务。

PDF 上传与解析、页面收藏和其他已有页面接口仍由各自业务接口处理，不作为对话 Agent 的工具。

验证入口：`python backend/tests/run_offline.py test_tool_module_boundaries test_research_tools test_agent_v1_tools test_agent_v1_flow test_agent_v1_prompts`。
