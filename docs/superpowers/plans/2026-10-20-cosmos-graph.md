# 论文关系宇宙图谱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把论文关系页改造为宇宙风格多中心力导向图谱：最多 5 篇主论文星球、每篇 6 篇子论文（3 角度 × 2 篇）、重叠双连线、主论文距离按 embedding 相似度、滚轮缩放等完整交互。

**Architecture:** 后端新增 `POST /api/papers/relate` 批量接口（复用现有单中心 bundle 缓存链路，部分成功 + warnings）；前端新增纯计算模块 `cosmosLayout.ts`（可测试）+ G6 v5 组件 `CosmosGraph.tsx`；ComparePage 顶部论文条改多选并同步 URL `?seeds=`。

**Tech Stack:** FastAPI + httpx（后端，已有）；React 18 + antd 5 + @antv/g6 v5（前端，已有）；node:test + ts.transpileModule（前端测试，已有模式）。

**Spec:** `docs/superpowers/specs/2026-10-20-cosmos-graph-design.md`

## Global Constraints

- 配色必须是 spec 给定值：论文 1–5 `#4D8DFF` `#A66BFF` `#FFB454` `#39D9A0` `#FF6FAE`；背景 `#080D1D`；子论文节点 `#64748B`；默认连线 `#334155`；选中节点文字 `#FFFFFF`。
- 每篇主论文每角度恰好取前 2 篇（`SATELLITES_PER_CATEGORY = 2`），每篇主论文子论文上限 6。
- 最多 5 篇主论文；前端选第 6 篇弹 `message.warning("最多支持 5 篇核心论文")` 并拒绝。
- 429 退避重试行为**不得改变**（`backend/tests/test_provider_resilience.py` 锁定）；只允许给 Semantic Scholar 增加 5xx 重试。
- 旧接口 `/api/papers/{id}/recommendations`、`/network` 及 `RecommendPage`/`RelationGraph.tsx` 行为不得变。
- 后端测试一律用 `python backend/tests/run_offline.py <模块名>`（拦截外部 HTTP、清空模型 API key），从仓库根目录跑。
- 提交信息用仓库现有 conventional commits 风格（`feat:` / `fix:` / `refactor:`，中英均可）。
- 禁止新增 npm/pip 依赖。

## Review Focus

1. **部分失败**：2–5 个 id 中某一个解析失败时，接口返回成功部分 + warnings 含失败原因（Task 2 测试钉住）。
2. **超限 422**：传 6 个 id 必须返回 422（Task 2 测试钉住）。
3. **embedding 不可用降级**：`SILICONFLOW_API_KEY` 为空时 `similarities` 全部为 `null` 且有警告，不能抛异常（Task 1 测试钉住；run_offline 天然清空 key）。
4. **重叠子论文**：同一子论文命中两个主论文 → 每个主论文各一条边、出现在 `overlaps`（Task 1 测试钉住）。
5. **主论文互为候选**：B 在 A 的候选列表中 → `center_edges` 出现 A→B 边（Task 1 测试钉住）。
6. **第 6 篇选择被拒**：`toggleSelection` 超限时返回 `null`（Task 4 测试钉住）。

---

### Task 1: 后端 `relate()` 多中心关系服务

**Files:**
- Create: `backend/app/services/retrieval/relate.py`
- Modify: `backend/app/services/retrieval/recommend.py`（`bundle()` 增加可选 `seed` 参数）
- Test: `backend/tests/test_relate.py`

**Interfaces:**
- Consumes: `recommend.load_seed(raw) -> PaperOut`、`recommend.bundle(raw, refresh) -> {"recommendations": {"groups": RecommendationGroup[], "items": RecommendationItem[], "warnings": [...]}, "network": ...}`（groups 元素含 `key: "citation"|"semantic"|"keyword"` 和 `items`，item 含 `id/title/relations/categories/strength`）、`recommend.canonical_id(paper)`、`relation_score.cosine(left, right) -> float`、`relation_score.embed_input(title, abstract) -> str`、`embeddings.embed_siliconflow(texts, persist=False) -> (meta, dict[text, vector])`（抛 `embeddings.EmbeddingError`，有 `.message`）。
- Produces:
  ```python
  SATELLITES_PER_CATEGORY = 2
  SATELLITES_PER_CENTER = 6
  async def relate(ids: list[str], refresh: bool = False) -> dict
  ```
  返回 `{"centers": [RelateCenter], "satellites": [RelateSatelliteGroup], "center_edges": [RelateCenterEdge], "overlaps": [{"paper_id", "center_ids"}], "warnings": [...], "computed_at": iso8601}`，其中：
  - `RelateCenter = {"id": canonical_id, "local_id": str|None, "title", "year", "authors": [...], "venue", "citation_count", "color_index": int, "similarities": {其他 center id: float|None}}`
  - `RelateSatelliteGroup = {"center_id", "category": "citation"|"semantic"|"keyword", "items": [RecommendationItem]}`（每类最多 2 篇）
  - `RelateCenterEdge = {"source", "target", "type", "label", "directed", "category", "strength"}`（字段语义与 `similarity.py` 现有 network edges 一致）

- [ ] **Step 1: 写失败测试 `backend/tests/test_relate.py`**

unittest 风格（参考 `backend/tests/test_recommend.py`），全部 `unittest.mock.patch.object` 打 `recommend.load_seed` / `recommend.bundle`，不调真实 HTTP：
```python
# 假 bundle payload：groups 每类给 3 个 item（id 形如 "p{a}{cat}{n}"），items 为按 strength 排序的融合列表
# 1) test_two_per_category：satellites 每组恰好 2 篇，取该类前 2
# 2) test_overlap_two_centers：p1 的 citation item 与 p2 的 semantic item 同 id →
#    satellites 中两组各保留；overlaps 含 {"paper_id": 该id, "center_ids": [c1, c2]}
# 3) test_center_as_candidate：B 的 id 出现在 A 的 citation items →
#    center_edges 含 {"source": A_id, "target": B_id, "category": "citation"}
# 4) test_partial_failure：load_seed 对 "bad" 抛 ResolveError →
#    返回 centers 只剩成功的，warnings 含 "bad"
# 5) test_embedding_unavailable：embed_siliconflow 抛 EmbeddingError →
#    所有 similarities 为 None，warnings 非空，不抛异常
# 6) test_backfill：某中心三类各 1 篇（共 3 篇）→ 从融合 items 按序补到 6 篇
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python backend/tests/run_offline.py test_relate`
Expected: FAIL（`ModuleNotFoundError: app.services.retrieval.relate`）

- [ ] **Step 3: 实现 `relate()`**

`backend/app/services/retrieval/relate.py`：
- 先修改 `recommend.bundle` 签名为 `async def bundle(raw: str, refresh: bool, seed=None)`，内部 `seed = seed or await load_seed(raw)`（避免 remote 论文二次抓取；现有调用方不受影响）。
- 按 `ids` 顺序遍历：try `seed = await recommend.load_seed(raw)`、`payload = await recommend.bundle(raw, refresh, seed=seed)`；except `Exception as exc`：`_warn(warnings, f"论文 {raw} 关系计算失败：{exc}")` 并跳过（部分成功语义）。
- centers 元数据来自 seed PaperOut；`local_id` 取 `getattr(seed, "local_id", None) or raw`。
- 子论文：对每个 center，遍历 `payload["recommendations"]["groups"]`，取 `group["items"][:2]` 放入对应 `category` 组。记录已选 id；若总数 < 6，从 `payload["recommendations"]["items"]`（strength 降序）顺序补 id 未选过的项到 6 篇为止（补充项的 category 记为其 `categories[0]`，无则 "semantic"）。
- overlaps：跨 center 统计子论文 id（排除 center id）出现次数 >1 → `{"paper_id", "center_ids": 按 center 顺序}`。
- center_edges：某 center A 的子论文 item id 命中其他 center id B 时，按 item 的 `relations` 生成边：`cites`→`{"type":"cites","label":"引用","directed":True}`，`similar`→`{"type":"similar","label":"主题相似","directed":False}`，`keyword`→`{"type":"keyword","label":"关键词关联","directed":False}`；`category` 用该 item 所在组；`strength` 用 item 对应分数（citation_score/semantic_score/keyword_score）；(A, B, category) 去重。
- pairwise 相似度：对成功 centers 的 `embed_input(title, abstract)` 一次性 `embed_siliconflow(texts, persist=False)`，两两 `cosine` round 4 位填入双方 `similarities`；except `embeddings.EmbeddingError as exc`：`_warn(warnings, f"{exc.message}；主论文距离按均布展示")`，similarities 留 `{}`。
- warnings 收集各 payload warnings 去重保序；`computed_at` 用 `datetime.now(timezone.utc).isoformat()`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python backend/tests/run_offline.py test_relate`
Expected: PASS（6 个测试全绿）

- [ ] **Step 5: 跑相关回归**

Run: `python backend/tests/run_offline.py test_recommend test_similarity_search`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/retrieval/relate.py backend/app/services/retrieval/recommend.py backend/tests/test_relate.py
git commit -m "feat: 多中心论文关系计算服务 relate()"
```

---

### Task 2: `POST /api/papers/relate` 接口

**Files:**
- Modify: `backend/app/main.py`（新增 endpoint 与请求模型）
- Test: `backend/tests/test_relate.py`（追加接口层测试）

**Interfaces:**
- Consumes: Task 1 的 `relate.relate(ids, refresh)`。
- Produces: `POST /api/papers/relate`，body `{"ids": [...], "refresh": false}`；1–5 个 id，超限 422；成功返回 Task 1 的 dict。

- [ ] **Step 1: 写失败测试（追加到 `backend/tests/test_relate.py`）**

httpx ASGI 模式（参考 test_recommend.py 的 endpoint 用例）：
```python
# 1) test_endpoint_rejects_six_ids：POST {"ids": ["a","b","c","d","e","f"]} → 422
# 2) test_endpoint_partial_success：patch relate.relate 返回单 center dict（patch 目标 "app.main.relate" 或按 main.py 实际导入路径）→
#    200 且 body["centers"] 长度 1；patch 抛 ResolveError 时不适用（relate 内部已兜住）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python backend/tests/run_offline.py test_relate`
Expected: FAIL（404）

- [ ] **Step 3: 实现 endpoint**

`backend/app/main.py`（放在 `/network` endpoint 之后，约 :294）：
```python
class RelateRequest(BaseModel):
    ids: list[str]
    refresh: bool = False

@app.post("/api/papers/relate")
async def papers_relate(payload: RelateRequest):
    if not payload.ids or len(payload.ids) > 5:
        raise HTTPException(status_code=422, detail="请选择 1 到 5 篇论文")
    try:
        return await relate.relate(payload.ids, payload.refresh)
    except (sqlite3.Error, OSError):
        logging.exception("relate lookup failed")
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试")
```
导入 `from app.services.retrieval import relate`（注意 main.py 已有 `recommend` 导入处）。不加 response_model（自由 dict）。

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `python backend/tests/run_offline.py test_relate test_recommend`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_relate.py
git commit -m "feat: POST /api/papers/relate 批量关系接口"
```

---

### Task 3: Semantic Scholar 5xx 退避重试

**Files:**
- Modify: `backend/app/services/retrieval/search.py`（`request()` 内 429 重试循环，:397-442）
- Test: `backend/tests/test_provider_resilience.py`（追加）

**Interfaces:**
- Consumes: 现有 `request(client, url, params, headers, label, ...)`；现有 429 循环用 `attempt` 计数器、`SHORT_ATTEMPTS`。
- Produces: 行为变化仅：label == "Semantic Scholar" 且状态 500–599 时最多再试 2 次（退避 2s、4s），之后走原有 `>= 400` 报错路径。429 与其他源行为不变。

- [ ] **Step 1: 写失败测试（追加到 `test_provider_resilience.py`）**

httpx.MockTransport 模式（参考该文件现有 S2 用例，patch `search.s2_headers` 避免依赖环境变量）：
```python
# 1) test_s2_500_retries_then_success：响应序列 500, 500, 200 →
#    search.search_semantic_scholar(...) 成功且传输层被调 3 次
# 2) test_s2_500_exhausts_raises：响应恒 500 → 抛 SourceFailure，消息含 "500"，共调 3 次
# 3) test_arxiv_500_not_retried：arXiv 恒 500 → 只调 1 次（行为不变）
```
测试内把退避 sleep 打 patch（`unittest.mock.patch("asyncio.sleep", new=AsyncMock())`）避免真实等待。

- [ ] **Step 2: 跑测试确认失败**

Run: `python backend/tests/run_offline.py test_provider_resilience`
Expected: 新测试 FAIL（500 直接抛错、调用次数 1）

- [ ] **Step 3: 实现**

`search.py` `request()` 的 `while True` 循环内，`:433` 的 `if response.status_code != 429: break` 改为：
```python
if response.status_code != 429:
    if not (label == "Semantic Scholar" and 500 <= response.status_code < 600 and attempt <= 2):
        break
    wait = float(2 ** attempt)  # 2s, 4s
    logger.warning("%s upstream HTTP %s; retrying in %.0fs", label, response.status_code, wait)
    await asyncio.sleep(max(0, wait))
    continue
```
（`attempt` 自增发生在循环开头，第一次失败时 attempt==1 → sleep 2s；第三次失败 attempt==3 → 跳出，落入原有 :459 抛错。）

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `python backend/tests/run_offline.py test_provider_resilience test_regressions test_recommend`
Expected: PASS（含既有 429 行为测试）

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/retrieval/search.py backend/tests/test_provider_resilience.py
git commit -m "fix: Semantic Scholar 5xx 退避重试，减少暂不可用误报"
```

---

### Task 4: 纯计算模块 `cosmosLayout.ts` + 测试

**Files:**
- Create: `frontend/src/cosmosLayout.ts`
- Create: `frontend/tests/cosmosLayout.test.mjs`
- Modify: `frontend/package.json`（加 `test:cosmos` 脚本）

**Interfaces:**
- Produces（Task 5/6 依赖的精确签名）：
  ```ts
  export const PLANET_COLORS: string[];  // ["#4D8DFF","#A66BFF","#FFB454","#39D9A0","#FF6FAE"]
  export const COSMOS_BG = "#080D1D";
  export const SATELLITE_COLOR = "#64748B";
  export const EDGE_COLOR = "#334155";
  export const MAX_CENTERS = 5;
  export function toggleSelection(selected: string[], key: string): string[] | null;
  // 已选则移除并返回新数组；未选且 length<5 追加；超限返回 null（调用方弹 warning）
  export function parseSeeds(raw: string | null, valid: string[]): string[];
  // raw 为 "?seeds=" 值；按逗号拆分，仅保留 valid 中的 id 且去重保序
  export function similarityDistance(sim: number | null): number;
  // null→0.5 默认值；返回 560 - sim*300（sim=1→260，sim=0→560）
  export function planetPositions(sims: (number | null)[][]): { x: number; y: number }[];
  // 贪心放置：p0=(0,0)；pi 取与已放置点中最近者 j 的距离 similarityDistance(sims[i][j])，
  // 方向角 i * 137.5°（黄金角），坐标 = pj + 极坐标偏移
  export function edgeDash(category: string): number[];
  // "citation"→[]，"semantic"→[5,4]，"keyword"→[2,4]，其他→[]
  ```

- [ ] **Step 1: 写失败测试 `frontend/tests/cosmosLayout.test.mjs`**

node:test + ts.transpileModule 模式（完全照抄 `frontend/tests/knowledgeGraph.test.mjs` 的加载方式）：
```js
// 1) toggle：追加保序、再点移除、第 6 个返回 null、数组不可变（原数组未被修改）
// 2) parseSeeds：过滤非法 id、去重保序、raw=null → []
// 3) similarityDistance：null→410；1→260；0→560
// 4) planetPositions：2 中心 sims=[[null,0.9],[0.9,null]] → 两点距离 260±1；
//    5 中心返回 5 个点；sims 对角线 null 不报错
// 5) edgeDash：三个 category 值 + 未知值 []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && node --test tests/cosmosLayout.test.mjs`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `frontend/src/cosmosLayout.ts`**

按 Interfaces 块签名实现（纯函数，无 React/G6 依赖）。package.json scripts 增加 `"test:cosmos": "node --test tests/cosmosLayout.test.mjs"`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm run test:cosmos`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/cosmosLayout.ts frontend/tests/cosmosLayout.test.mjs frontend/package.json
git commit -m "feat: 宇宙图谱纯计算模块（配色/选择/距离/坐标/线型）"
```

---

### Task 5: G6 宇宙图组件 `CosmosGraph.tsx`

**Files:**
- Create: `frontend/src/components/CosmosGraph.tsx`
- Create: `frontend/src/styles/cosmos.css`
- Test: 无组件测试（与项目现状一致）；样式/布局常量已在 Task 4 测试

**Interfaces:**
- Consumes: Task 4 全部导出；`RelateResponse` 类型（Task 6 定义，先在本文件用内联类型或局部 interface，Task 6 收敛到 types.ts）；`workspacePaperPath`、`paperLocation` from `../workspace`（见 PaperRelationMap.tsx:6 用法）；G6 v5 `Graph`（RelationGraph.tsx 用法）。
- Produces:
  ```ts
  export function CosmosGraph(props: {
    data: RelateResponse;        // Task 6 将移到 types.ts 并 import
    workspaceId: string;
    from: string;                // 详情页返回用
  }): JSX.Element;
  ```
  内部状态：选中卫星节点的详情面板 `{ item: RecommendationItem; categories: string[] } | null`。

- [ ] **Step 1: 实现组件与样式**

`CosmosGraph.tsx`（G6 v5，参照 RelationGraph.tsx 的 Graph 构造/销毁/ResizeObserver 模式）：
- **数据映射**：centers → 星球节点（`data: {kind:"planet", ...center, color: PLANET_COLORS[color_index]}`，带上 `fx/fy` = `planetPositions(sims 矩阵)` 的结果）；satellites 按 item.id 去重 → 卫星节点（`kind:"satellite"`，fill SATELLITE_COLOR，size 22，星球 size 56）；边：每个 `satellites` 组每条 item 一条 `center→sat` 边（`category` 决定 `lineDash` = `edgeDash(category)`，引用类含 `endArrow`）；`center_edges` 直接映射（type "cites" 带箭头）。边默认 stroke EDGE_COLOR，线宽 1 + strength/50。
- **布局**：`{ type: "force", preventOverlap: true, nodeSize: 90, linkDistance: 170 }`；星球靠 `fx/fy` 钉住（d3-force 语义）。实现时验证 G6 5.1.1 force 布局确实尊重 `fx/fy`；若不被尊重，改为把布局换成 `preset` 并自行给卫星算初始环绕坐标（以各星球为中心、半径 150–260 的环，按组错开角度）——两种方案都不加依赖，验证后取可行者。
- **发光星球**：node style 用 `shadowColor: color`、`shadowBlur: 24`；若当前 G6 版本 style 不支持 shadow，则退回 `halo: true` + `haloStroke`/`haloOpacity` 同色系（实现时验证）。
- **交互**：behaviors `["drag-canvas", "zoom-canvas", "drag-element"]`（滚轮缩放由此获得）；node/edge style 配 `state: { active: {...}, inactive: { opacity: 0.15 } }`，`NodeEvent.POINTER_ENTER` 时把该节点及其一跳邻居 `graph.setElementState(id, "active")`、其余 `"inactive"`，LEAVE 时清空；`NodeEvent.CLICK`：planet → `navigate(workspacePaperPath(workspaceId, local_id ?? id, from))`；satellite → setPanel。
- **工具栏**（右上角竖排，仿现有 map-toolbar）：放大 `graph.zoomBy(1.2)`、缩小 `graph.zoomBy(0.8)`、适配 `graph.fitView()`；若 `zoomBy` 不存在改用 `zoomTo(getZoom()*1.2)`。
- **图例**（左下角）：三种 category 线型样例 + 5 星球色点（有颜色才显示）。
- **详情面板**：容器内右侧绝对定位卡片（标题/年份/作者/各 category 分数与 reasons/共同关键词/「查看论文详情 →」`Link to={paperLocation(item.id, from)}`，关闭按钮）。注意 center 节点 planet 的 local_id 可能为 null（非工作区论文），此时回退 `paperLocation(id, from)`。
- **星空背景**：`cosmos.css` 中 `.cosmos-graph-wrap { background: #080D1D }`，用 `::before`/`::after` 两层 `box-shadow` 星点（多层小点坐标直接写在 CSS 里，白色低透明度）+ `@keyframes` 闪烁动画；纯 CSS 实现，不引依赖。容器圆角 16px，高 ≥ 560px。

- [ ] **Step 2: 类型检查与构建**

Run: `cd frontend && npm run build`
Expected: 构建通过（RelateResponse 内联类型先可用）

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/CosmosGraph.tsx frontend/src/styles/cosmos.css
git commit -m "feat: G6 宇宙力导向关系图组件"
```

---

### Task 6: ComparePage 多选改造与接口对接

**Files:**
- Modify: `frontend/src/types.ts`（追加 Relate 类型）
- Modify: `frontend/src/api.ts`（追加 `fetchRelate`）
- Modify: `frontend/src/pages/workspace/ComparePage.tsx`（多选 + URL 同步 + 渲染 CosmosGraph）
- Modify: `frontend/src/styles/paper-relations.css`（暗色化；或追加 cosmos-page 覆盖样式，写在 cosmos.css 里）
- Delete: `frontend/src/components/PaperRelationMap.tsx`（先 `Grep` 确认仅 ComparePage 引用）

**Interfaces:**
- Consumes: Task 1-5 全部；现有 `useWorkspaceProject`、`MissingWorkspace`（ComparePage 现状）。
- Produces:
  ```ts
  // types.ts
  export interface RelateCenter { id: string; local_id: string | null; title: string; year: number | null; authors: string[]; venue: string | null; citation_count: number | null; color_index: number; similarities: Record<string, number | null>; }
  export interface RelateSatelliteGroup { center_id: string; category: string; items: RecommendationItem[]; }
  export interface RelateCenterEdge { source: string; target: string; type: string; label: string; directed: boolean; category: string; strength: number | null; }
  export interface RelateResponse { centers: RelateCenter[]; satellites: RelateSatelliteGroup[]; center_edges: RelateCenterEdge[]; overlaps: { paper_id: string; center_ids: string[] }[]; warnings: string[]; computed_at: string; }
  // api.ts
  export async function fetchRelate(ids: string[], refresh: boolean, signal?: AbortSignal): Promise<RelateResponse>;
  // POST /api/papers/relate，body {"ids", "refresh"}，非 2xx 抛 Error(await errorMessage(response))
  ```

- [ ] **Step 1: types.ts 与 api.ts**

按 Interfaces 块追加。CosmosGraph 改为从 `../types` import `RelateResponse`（删掉内联定义）。

- [ ] **Step 2: ComparePage 改造**

- `requested`/`seed` 逻辑替换：`selected = parseSeeds(params.get("seeds"), entries.map(([k]) => k))`；`params.get("seeds")` 为 null 且工作区有论文时默认 `[entries[0][0]]`。
- `choose(key)` 改 `toggle(key)`：`const next = toggleSelection(selected, key); if (next === null) { message.warning("最多支持 5 篇核心论文"); return; } setParams({ seeds: next.join(",") })`；同时 `setRequest(seq+1, refresh:false)`。
- 卡片选中态：`selected.includes(key)`；`aria-pressed` 同步；卡片左边框/角标显示星球色 `PLANET_COLORS[selected.indexOf(key)]`（未选中无色）。
- 数据获取 effect 依赖改为 `seeds = selected.join(",")`；缓存键 `${id}:${seeds}`；调 `fetchRelate(selected, request.refresh, signal)`。
- 加载文案改 "关系计算中，多篇论文可能需要 1 分钟左右"；空态改 "选择 1 到 5 篇论文，生成宇宙关系图谱"。
- 渲染：`<CosmosGraph data={data} workspaceId={id} from={here} />`，`here = /w/${id}/compare?seeds=${seeds}`；warnings 沿用现有 Alert 列表。
- 页面暗色：根元素 className 加 `cosmos-page`，在 cosmos.css 中覆盖 `relations-heading`、`seed-picker`、卡片、Alert 配色为深色系（文字 `#E2E8F0`，次级 `#94A3B8`）。
- 删除 `PaperRelationMap.tsx`（先 Grep 确认仅剩 ComparePage 引用）及其在 ComparePage 的 import。

- [ ] **Step 3: 构建 + 前端测试全量**

Run: `cd frontend && npm run build && npm run test:cosmos && npm run test:graph && npm run test:research`
Expected: 全 PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/pages/workspace/ComparePage.tsx frontend/src/styles/paper-relations.css frontend/src/styles/cosmos.css frontend/src/components/CosmosGraph.tsx frontend/src/components/PaperRelationMap.tsx
git commit -m "feat: 论文关系页多选改造与宇宙图谱对接"
```

---

### Task 7: 端到端验证

**Files:** 无新代码；只做验证与收尾。

- [ ] **Step 1: 后端全量测试**

Run: `python backend/tests/run_offline.py test_relate test_recommend test_provider_resilience test_similarity_search test_regressions test_analysis`
Expected: 全绿

- [ ] **Step 2: 前端构建与测试**

Run: `cd frontend && npm run build && npm run test:cosmos && npm run test:graph && npm run test:research`
Expected: 全 PASS

- [ ] **Step 3: 手动端到端**

后端 `cd backend && python -m uvicorn app.main:app --port 8000` + 前端 `cd frontend && npm run dev`，在论文关系页验证：
- 选 1 篇 → 单星球 + 环绕卫星，滚轮可缩放、可拖拽、点节点出面板；
- 选 2 篇（含主题相近的一对）→ 两星球距离明显近于另一对；若子论文重叠可见双连线；
- 选到第 6 篇 → 弹 "最多支持 5 篇核心论文"；
- 刷新页面 → 选中状态保留（URL `?seeds=`）；
- 「更新关系」可用。
（手动验证结果记录在最终交付说明中；如遇问题按 superpowers:systematic-debugging 排查。）

- [ ] **Step 4: 收尾提交（如有改动）**

```bash
git add -A && git commit -m "fix: 端到端验证修正"   # 仅在 Step 3 有改动时执行
```
