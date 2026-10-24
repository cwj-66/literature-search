# QA 结构感知父子分块：审计与验收

日期：2026-10-23。本节记录 QA 父子索引阶段。未提交 GitHub，未替换生产 API，也未重建推荐/关系使用的 FAISS。后续研究助手定位、页面说明与新增验证见 [research-assistant-policy.md](research-assistant-policy.md)，工作区原有修改保留。

## 改造前核实

- QA：`hybrid.split_text` 按 **2400 字符、重叠 400 字符**切分；上一轮 `qa_index` 能使用 MinerU 章节/块/页码分组，但长块最终仍按字符窗口切分。没有父子关联。
- 推荐/关系 FAISS：`tools/index_paper.py` 按 **800 字符、重叠 100 字符**。本次保留。
- 线上向量模型：硅基流动 `BAAI/bge-m3`；重排：`BAAI/bge-reranker-v2-m3`。未替换服务、模型或密钥。
- 官方 BGE-M3 `tokenizer_config.json` 声明 `model_max_length=8192`。使用官方 XLM-R tokenizer，关闭本地截断和 padding；保留两个特殊 token，因此默认正文硬上限 **8190 tokens**。
- 没有证据证明这批论文的旧 QA 块被向量服务截断：RankRAG、SFR-RAG、FaithfulRAG 的旧 2400 字符窗口最大分别为 **1590、1372、1354 个 BGE tokens**，均远低于 8192。已确认的损失来自机械语义边界、答案证据摘录截断及检索污染，不能把这些问题归咎于 Embedding 超长。
- MinerU 现有解析已提供 Markdown、blocks、sections、section.parent_id、block.section_id、表格/图注文本与物理页码。Markdown 本身不能保证页码，缺失时继续返回 null。

## 实际实现

### 分块和参数

新增 `app/services/retrieval/qa_chunking.py`，使用 `tokenizers` 与后端自带的压缩 BGE tokenizer 资源离线计数，不下载模型权重，不在请求中联网下载分词器，也不用字符数冒充 tokens。

| 配置 | 默认值 | 规则 |
| --- | ---: | --- |
| `QA_CHILD_TOKENS` | 350 | 正文子块目标；向量与 FTS5 共用同一文本和 ID |
| `QA_CHILD_OVERLAP_TOKENS` | 50 | 普通正文重叠目标；句子边界和剩余容量可能减少重叠，不跨父块或切碎表格复制 |
| `QA_PARENT_TOKENS` | 1000 | 同一章节路径内的上下文目标，不将无关章节拼成父块 |
| `QA_EMBED_MAX_TOKENS` | 8192 | 包含特殊 token；已知 BGE 上限不能被配置调大 |
| `QA_TOKENIZER_PATH` | 内置 BGE 资源 | 更换 Embedding 模型时必须显式提供对应 tokenizer 与输入上限 |

优先使用可与保存正文匹配的 MinerU 块和章节路径；结构不可用或与正文不一致时，回退到 Markdown 章节、段落、句子、表格、展示公式和代码围栏。只有超长普通句子才退回真实 token 边界。所有新块记录保存正文中的精确字符起止位置。

表格、展示公式和图注尽量原子保留，**350/1000 是目标值，不是必须切碎对象的硬限制**。完整对象可超过目标，但不能超过模型正文上限。超过 8190 tokens 的不可分对象会使该次新索引失败，保留原资料和旧索引；不会静默截断后标记成功。超大表格逐行拆分及重复表头尚未实现。

### 存储、检索与引用

- `paper_chunks` 新增 `parent_id`、`section_path`、`source_start`、`source_end`、`token_count`；旧字段和旧数据保留。
- 新增 `qa_parent_chunks`，只保存上下文文本和来源，不计算父块向量，不写入 FTS5。
- 子块继续使用 SQLite JSON 向量、FTS5、RRF 和原 BGE 重排。普通问答排除参考文献列表；明确查询参考文献时仍可检索它。
- 论文 ID 从检索查询中去噪，论文范围始终由单篇 SQL 条件控制。规划器按内容维度拆解查询，防止“每个论文 ID 各写一条查询”与后台逐篇执行产生交叉污染。
- 复杂问题命中子块后，仅获取**同一论文、同一版本**父块，按父块 ID 去重，保留命中的 child IDs。继续控制每篇份额、块数和总字符预算；放不下父块时保留子块。
- 新父子证据不再经过 900/2400 字符的机械摘录。长表格完整进入重排；超过 BGE 重排成对输入上限时保持 RRF 顺序，不截断表格。
- `E0001` 等编号和 Judge 保留，响应仅添加可选来源字段。证据能定位父块、原子块、章节路径、页码与保存正文的准确位置。未知页码不推测。
- 实测发现模型可能违反 JSON 格式：仅修复格式一次，修复结果仍经 Judge；失败时不将原始 JSON 当答案展示。

### 迁移、幂等与失败恢复

1. 兼容读取旧 `c2400-o400-v1`、`sections-c2400-o400-v2` 和新 `parent-child-tokens-v3` 索引。
2. `qa_index_jobs` 增加 `split_config`，分词资源指纹、版本和参数纳入 revision。配置或模型变化由已有后台 worker 每轮最多回填 20 条，不要求用户重新上传 PDF。
3. 所有新子块向量完整且有效后，才在**同一 SQLite 事务**替换父块、子块和 FTS。旧版本在成功替换前继续可用。
4. 重复保存同版本不重复索引；新版本/别名规范 ID 有并发写入保护。父块丢失会触发修复。保留三次有限失败重试、退避及过期租约恢复。
5. 解析成功后仍由 `documents.save` 自动入队，已有 worker 消费。解析或索引失败不删除已有摘要或旧全文，也不将失败状态报告为 ready。

## 修改文件

- `backend/app/services/retrieval/qa_chunking.py`：新分块器、模型 token 计数和配置。
- `backend/app/services/retrieval/qa_index.py`：父子原子写入、分块版本迁移与恢复。
- `backend/app/services/retrieval/hybrid.py`：兼容列/父表、正文检索、父块读取及完整对象重排。
- `backend/app/services/retrieval/qa_workflow.py`：受限父块扩展、查询去噪与逐篇证据公平分配。
- `backend/app/services/analysis/attribution.py`：完整父子证据、溯源可选字段与格式修复保护。
- `backend/app/services/papers/documents.py`：QA 可读取不去除首尾空白的原始正文，保证位置正确。
- `backend/app/assets/tokenizers/`、`backend/requirements.txt`：离线分词资源、校验和、来源及轻量运行依赖。
- `backend/tests/test_qa_parent_child.py`、`backend/tests/test_qa_workflow.py`：新增边界、迁移、对象完整性、引用与查询污染测试。

## 验证

改造前相关基线：**118 passed，5 个子测试通过**。

父子分块阶段相关回归：**217 passed，8 个子测试通过**。包含 QA、全文解析、上传、权限、访客隔离、引用、Judge、推荐、关系及 FAISS 相关测试；后续研究政策阶段的最新回归见研究助手文档。不是全仓库所有测试通过的声明。

真实服务验收使用公开演示论文的独立数据库和独立测试容器，复用真实 BGE、BGE 重排、MinerU 和阿里云 Qwen。没有修改访客数据。此前在生产容器内部并行测试触发过 512 MB 内存限制，测试进程被终止；生产 API 健康检查为 200。后续改用独立 1 GB 测试容器，并缓存分词资源指纹，避免反复序列化大型 tokenizer。

真实论文来源和结构审计：

| 论文 | 子块 | 父块 | 最大子块 tokens | 精确正文位置 | 已知页码 |
| --- | ---: | ---: | ---: | --- | --- |
| RankRAG，arXiv:2407.02485 | 104 | 58 | 4248（完整表格） | 全部匹配 | 104/104 |
| SFR-RAG，arXiv:2409.09916 | 50 | 25 | 1264（完整对象） | 全部匹配 | 50/50 |
| FaithfulRAG，arXiv:2506.08938 | 90 | 57 | 1984（完整对象） | 全部匹配 | 90/90 |

三篇合计 244 个子块、140 个父块。新旧 token 测量、原文哈希和位置包含关系记录在 `.qa/live-results/chunk-audit.json`。真实答案、每次检索、完整模型阶段日志及失败阶段保存在 `.qa/parent-child-results/`；验收脚本位于 `.qa/`，这些临时产物不包含服务密钥或访客数据库。

父子索引、真实 MinerU 解析、BGE 向量化、失败后摘要保留与重试幂等已完成实测。发现的查询污染、格式与跨论文归纳问题已纳入后续研究助手改造，参见上述文档。**接口返回 completed 不等于语义验收通过，也没有宣称全部问答 P0 已通过。**

## 剩余边界

- 超大不可分对象暂时采用明确失败和保留旧资料；尚未实现跨子块重复表头。
- 缺页码或解析错误不能由分块器自动恢复。字符位置针对保存的 MinerU 正文，PDF 位置仍依赖解析页码。
- 分词资源随部署增加约 4 MB；未来换模型必须配置对应 tokenizer。
- 结构化分块改善证据机会，不能保证每个问题检索完整。仍最多补检一次，不足就保留限制说明；召回质量、模型延迟及成本属于继续评估项。
- 新代码尚未部署为生产 API。生产旧代码不能同时消费新父子索引，因此本次真实验收严格使用独立数据，不迁移线上访客库。
