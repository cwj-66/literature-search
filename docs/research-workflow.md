# 研究助手：证据驱动工作流

## 实现与来源

2026-10-18：将研究问答从模型自由编排底层工具，改为程序控制必要阶段。
保留 FastAPI、LangGraph、SQLite 检查点、现有论文库和检索设施，没有新增大型运行依赖。

代码级参考以下公开实现；本次是针对现有数据模型独立实现相同设计原则，没有复制其源码文件。
下列 SHA 为本次读取文件的 Git blob SHA，用于记录参考版本，并非整个仓库的提交 SHA。

| 参考实现 | 阅读位置 / blob SHA | 本项目采用的设计 |
| --- | --- | --- |
| [Ai2 ScholarQA](https://github.com/allenai/ai2-scholarqa-lib/blob/main/api/scholarqa/scholar_qa.py) | `run_qa_pipeline`、`generate_report` / `0f65304725c9e136055e271ecebb7927c30d2b5b` | 固定检索、重排、原文提取、综合阶段；无材料时明确终止；阶段结果可观察 |
| [PaperQA](https://github.com/Future-House/paper-qa/blob/main/src/paperqa/agents/tools.py) | `PaperSearch`、`GatherEvidence`、`GenerateAnswer` / `efe32a8a4f06cb8305fc915283fe33da03dee2a0` | 论文级阅读封装、共享证据状态、同一检索继续翻页 |
| [OpenScholar](https://github.com/AkariAsai/OpenScholar/blob/main/src/open_scholar.py) | `run`、`edit_with_feedback_retrieval` / `46a582b5c3a718ff104f046012d9a9ae8cceb0a2` | 根据具体证据缺口有限补检索、反馈修订、区分已有材料纠错和材料不足 |
| [GPT Researcher](https://github.com/assafelovic/gpt-researcher/blob/main/gpt_researcher/skills/researcher.py) | 研究执行、访问记录、MCP 缓存锁 / `59e688e764be1feef316301321baba0301a64f00` | 执行层去重与缓存，单篇失败不破坏其他材料 |

## 当前主流程

1. `router`：区分通用直答、简单研究、需要审阅计划的复杂研究。
2. `prepare_research`：解析已授权本地论文，识别 `read` / `discover` / `mixed`，补全“类似这些论文”的标题上下文。
3. `search_literature`：仅在需要发现论文时执行；候选结果与本地授权范围分别保存。
4. `read_paper`：程序逐篇读取，优先已有文本和索引；必要时获取公开全文；重排失败保留原有排序。
5. `extract_quotes`：从候选片段提取相关原文，并逐字检查其确实出现在该片段内。伪造引文不能入证据池。
6. `assess_evidence`：基于实际证据、读取状态、剩余预算和复核反馈，判断是否补检索。
7. `expand_research`：采用新检索式或显式翻页。已经执行的相同检索页不会再次执行。
8. `synthesize_evidence`：模型只根据登记证据生成答案，不接收研究工具目录。
9. `claims` / `judge`：检查引用有效性、数字出处及证据支持程度；有限修订后生成最终结果。

用户指定的工作区操作仍有单独的确认流程；知识关系/样本统计请求可以使用原有业务工具。
普通聊天仍走无工具的直接回答。

## 书目发现快捷路径

仅要求找/推荐论文时走 `prepare → search → select_bibliography → finish`。数量由中文/英文表达解析并贯穿 plan、react、状态输出和界面；无明确数量时默认推荐最多五篇。达到指定数量即结束；不足时在既有预算内补检索，不以缺少正文或无法抽引文作为书目失败理由。

筛选要求标题或摘要匹配全部必需概念，允许同义改写，但不删除应用、医疗等硬约束。RAG 按 retrieval-augmented generation 含义检索，排除同名基因缩写。多组 OR 视图按各来源语法编译；Semantic Scholar 的布尔视图使用 bulk 接口的 `+`、`|` 运算符。

内部 `papers` 保留候选池；`recommended_ids` 明确入选集合。公开状态的 `papers` / `recommended` 仅包含入选论文，`candidates` 为未选用候选，`candidate_count` 为本次总候选数。后续分析/保存优先使用同一入选集合，不再从池首重新取篇数。

界面先展示推荐卡片、关联依据和收藏，再折叠显示候选与过程摘要；历史任务只恢复答案明确点名的推荐，其余作为历史候选展示。书目信息不伪装成逐字引文；候选不是已读，也不会自动导入工作区。资料不足、来源失败仍明确保留。

明确要求分析、比较、研究综述的请求继续走阅读、提取原文、覆盖评估与科研结论复核。

## 工具调整

研究路径不再向模型暴露 `get_paper_analysis → analyze_paper → retrieve_paper_evidence → read_paper_context` 这串需要模型记住顺序的底层调用。
这些前置条件由工作流承担，针对用户当前问题直接提取证据，无需先生成整篇论文分析报告。
已有结构化分析 API 与页面仍保留。

操作路径使用独立提示词和收敛后的目录：搜索、详情、工作区列表/增删、参考文献/被引、图谱/样本统计。
不再提示模型调用已经从该目录移除的阅读工具；不允许靠反复扩展目录调用它们。
底层业务 API 未物理删除，因为论文页面、分析功能和旧任务兼容仍会使用它们。
旧 ReAct 节点保留用于操作和原有检查点续接，新建研究任务全部进入新工作流。

## 范围与材料边界

- 已选论文表示本地访问授权，不是禁止联网。`mixed` 可以同时读取已选论文和搜索新候选。
- 新发现论文仅读取其公开来源。即使其 ID 恰好对应库中未选中的 PDF，也不能使用那个本地全文。
- 获取公开内容使用 `fulltext.prepare(..., use_stored=False)`，不隐式收藏或加入工作区。
- 每个阶段重新检查本地授权；恢复时撤销授权会停止任务并清空先前收集的本地证据。
- 仅提供摘要时，答案必须明确边界，不能据此声称验证了正文的实验细节。
- 搜索论文时默认用摘要筛选；用户要求细节、比较或分析时才尝试读取全文。
- 当前证据研究任务最多逐篇细读 5 篇；混合发现与分析任务先读最多 2 篇，为补检索保留空间。纯书目发现不进入细读，不能宣称这些论文全部已读。

## 可靠性与恢复

- 每个阶段具有独立 LangGraph 检查点，暂停/取消在阶段边界生效。
- 成功读取与原文提取结果有任务级持久缓存；重复进入阶段不会再次下载或重复调用模型。
- 只对暂时性的读取超时/传输错误最多重试一次。无效参数、授权失败等不盲目重试。
- 搜索服务与模型已有自己的退避机制，工作流不会再套一层整任务重试。
- 单篇失败登记缺口，继续处理其他论文；提取服务失败时保留实际检索片段并标记降级。
- 没有科研原文时不能生成论文细节结论；只有元数据时仅可报告书目信息。
- 默认最多初始搜索加两次补检索，复核最多两轮，保留原有时间与模型预算。
- 每个节点更新服务器任务快照，轮询与 SSE 均能获取阶段进度。
- 证据池淘汰旧记录后不复用证据编号，避免旧引用被新证据覆盖。

缓存、校验和模型复核都不能保证所有科研结论正确。全文可获取性、解析质量、上游限流以及模型判断仍然影响结果。

## 验证

使用 `backend/tests/run_offline.py` 执行测试，阻止未模拟的外部 HTTP 和付费调用。

```powershell
.\.venv\Scripts\python.exe backend/tests/run_offline.py test_research_workflow test_qa_scope test_conversations test_assistant_chats test_analysis_resilience test_parse_tool test_index_tool
```

该检查覆盖混合搜索、无本地授权时的公开发现、伪造引文拒绝、降级、来源部分失败、补检索预算、读取去重、撤销授权、SQLite 重启恢复、后台轮询进度及现有数据链路。
原有 `test_agent_scenarios.py` 的旧调用序列场景已由 `test_research_workflow.py` 中的行为测试取代，保留并扩展了限流、材料不足、引用检查、有限修订、预算、取消、重启恢复和 SSE 覆盖。
缓存测试改为调用实际工作流节点，收藏审批改为验证当前业务工具及真实工作区成员变化，不再测试已经移出目录的旧别名。
