# 论文关系宇宙图谱 · 设计文档

日期：2026-10-20
状态：待用户批准

## 意图与原因

"论文关系"页（workspace `/w/:id/compare`，`frontend/src/pages/workspace/ComparePage.tsx`）现在是 `PaperRelationMap` 的手写静态 SVG：单一中心论文、三个分支各 5 篇、缩放只靠 +/- 按钮（没有滚轮事件）、无拖拽布局。用户希望它变成一个"宇宙图谱"：多颗主论文星球悬浮在星空中，子论文环绕，连线表达关系，交互流畅。这能让用户同时从多篇论文出发探索文献网络，并直观看到主论文之间以及主论文与子论文的关系。

成功标准：
- 顶部论文条可多选最多 5 篇主论文，按选中顺序分配星球配色；选第 6 篇时弹出提醒并拒绝。
- 每篇主论文从 3 个角度（引用关联 / 主题相似 / 关键词关联）各取 2 篇，共 6 篇子论文；两个主论文的子论文若重叠，重叠论文显示两条线分别连向两个主论文。
- 主论文之间的图上距离按 embedding 相似度计算：越相似越近。
- 滚轮缩放、拖拽平移、拖动节点、点击节点出详情面板、关系图例、工具栏（+ / − / 适配 / 重置）。
- 宇宙质感：深空背景 + 星空 + 发光星球节点，达到参考图（用户提供的 Image 2）的观感。

## 目标 / 非目标 / 反目标

目标：
1. ComparePage 顶部"中心论文"卡片条改造为多选（最多 5 篇），保留搜索过滤和横向滚动。
2. 新增 G6 v5 力导向宇宙图组件（新文件 `frontend/src/components/CosmosGraph.tsx`），替换 PaperRelationMap 在 ComparePage 中的位置。
3. 后端新增多中心关系接口：一次传入 2–5 篇论文 id，返回每篇的子论文（3 角度 × 2 篇）、跨主论文重叠关系、主论文两两相似度。
4. 滚轮缩放等交互（G6 `zoom-canvas` behavior 自带滚轮缩放）。
5. 减少 "semantic_scholar 暂不可用" 警告：给 S2 请求加 429/5xx 退避重试。

非目标：
- 不做全站暗色模式；只有论文关系页是深空暗色主题，其余页面保持现有浅色主题。
- 不改动旧 `RecommendPage` / `RelationGraph.tsx`（它们继续用单中心 `/network` 接口）。
- 不改 arXiv 检索链路。
- 子论文之间不连边（只连主论文）。

反目标：
- 不要为了视觉效果引入重型渲染引擎（three.js / WebGL）：G6 + CSS/Canvas 星空足够。
- 不要在缺 embedding 服务时阻塞图谱渲染（降级为均等布局 + 提示）。
- 不要破坏现有 `/recommendations`、`/network` 单中心接口的兼容性（有测试覆盖）。

## 用户已定的部分

- 技术选型：**G6 v5**（已装依赖，项目 `RelationGraph.tsx` / `ExplorerCanvas.tsx` 有现成用法）力导向图；ECharts/自研 Canvas 被明确放弃。
- 多选入口：**改造顶部中心论文条**，不另加按钮。
- 配色（用户指定，不可改）：
  - 论文 1 星际蓝 `#4D8DFF`
  - 论文 2 星云紫 `#A66BFF`
  - 论文 3 恒星橙 `#FFB454`
  - 论文 4 极光绿 `#39D9A0`
  - 论文 5 星耀粉 `#FF6FAE`
  - 背景 `#080D1D`；普通子论文节点 `#64748B`；默认连线 `#334155`；选中节点文字 `#FFFFFF`
- 每篇主论文 3 个角度 × 2 篇 = 6 篇子论文。
- 重叠子论文同时作为两个主论文的子论文，两条线连过去；主论文间距按相似度。

## 具体设计

### 后端

新接口（我的调用：POST 批量语义，避免前端多次请求重复计算）：

```
POST /api/papers/relate
body: {"ids": ["id1", "id2", ...]}   // 1–5 个，超限 422
resp: {
  "centers": [
    {"id","title","year","authors","color_index","pairwise":[{"other_id","similarity"}]}
  ],
  "satellites": [
    {"center_id","category":"citation|semantic|keyword",
     "items":[{"paper":{...},"relations":[...],"strength":...}]}   // 每类最多 2 篇
  ],
  "overlaps": [{"paper_id","center_ids":[...]}],
  "warnings": [...]
}
```

**已定案的语义**（用户确认，2026-10-20）：
- **部分成功**：单个 id 解析/计算失败不拖垮整请求；返回成功的主论文，失败者进 `warnings`（含失败原因和原 id）。前端图上只渲染成功的星球，并在图上方显示警告条。
- **主论文互为候选**：不排除。若主论文 B 出现在主论文 A 的候选子论文中，照常画 A→B 的引用/相似连线（此时距离与连线同时表达关系）；B 仍是大号星球节点，不是灰色子节点。
- **多选状态同步 URL**：`?seeds=a,b,c`（逗号分隔，顺序即配色顺序）；刷新、分享、从详情页返回后选中状态保留。

实现（`backend/app/services/retrieval/recommend.py` 新增 `relate(ids)`）：
- 对每个 id 复用现有 `bundle()` → `similarity.compute()` 缓存链路（改动最小，保留缓存行为）。
- 从每个 seed 的 `groups` 结果中每类按 `strength` 取前 2 篇。
- 重叠判定：用现有 `identity_keys()` 跨主论文去重；同一子论文命中多个主论文时，对每个主论文各保留一条边记录，并计入 `overlaps`。
- 若某主论文去重后不足 6 篇：从该主论文 strength 排序的后续候选补足（若有），否则就显示实际数量（我的调用）。
- 主论文两两相似度：`relation_score.cosine()` 作用于 `embed_input(title, abstract)`，向量由 `embeddings.embed_siliconflow()` 批量算（该函数不持久化，需当场重算，代码注释里注明）。
- embedding 服务不可用时的降级：similarity 置 null，前端用均等距离（我的调用）。
- 警告透传各 seed 的 `warnings`（包括 semantic_scholar 暂不可用）。

S2 退避重试（减少"暂不可用"）：
- **现状修正**（施工方核查发现）：`search.py:397-442` 的 `request()` **已经**对所有源做 429 退避重试（最多 3 次、尊重 `Retry-After`），并有测试锁定该行为。
- 因此本设计只做增量：**仅对 Semantic Scholar 增加 5xx 重试**（最多 2 次、指数退避 2s→4s）；429 行为一律不动。其他源不动（我的调用）。

加载时长（施工方提出的第 5 问，我的调用）：5 篇冷启动 seed 并行计算可能需要数十秒，采用"单个 antd Spin 全屏加载 + '关系计算中，多篇论文可能需要 1 分钟左右'提示"，不做渐进式渲染——与现有单中心页行为一致。

测试：在 `backend/tests/test_relate.py`（或并入 test_recommend）用 `run_offline.py` 拦截外部 HTTP，覆盖：每类 2 篇截断、重叠双归属、>5 篇返回 422、embedding 不可用时 similarity 为 null。

### 前端

**ComparePage 改造**（`frontend/src/pages/workspace/ComparePage.tsx`）：
- 顶部卡片条从单选 radio 行为改为多选 toggle；新增 `selected: string[]`（顺序即配色顺序）；点第 6 篇时 `message.warning("最多支持 5 篇核心论文")`。选中状态与 URL `?seeds=` 双向同步（`useSearchParams`），保留从详情页经 `from` 参数返回的现有行为。
- 选中 0 篇：显示空状态引导语。选中 ≥1 篇：调 `POST /api/papers/relate`，结果交给 `CosmosGraph`。加载期间显示全屏 Spin + "关系计算中，多篇论文可能需要 1 分钟左右"提示。部分失败时图上方显示 warnings 警告条，成功的星球正常渲染。保留"更新关系（refresh）"按钮。
- 该页整体切换为深空暗色主题（背景 `#080D1D`，文字浅色）——这是本页的独立主题，不动 `theme.ts` 全局 token（我的调用，用户已给背景色，视为认可本页暗色）。

**新组件 `frontend/src/components/CosmosGraph.tsx`**（G6 v5）：
- 数据映射：主论文 → 大号发光"星球"节点（keyshape circle + 光晕 halo，颜色按 color_index 查表）；子论文 → 小圆点 `#64748B`；重叠子论文仍是一个节点，两条边。
- 边样式按 category：引用关联 = 实线 + 箭头；主题相似 = 虚线；关键词关联 = 点线。边颜色默认 `#334155`，hover 高亮时变主论文星球色。主论文之间的候选连线（B 在 A 的候选中）同样按 category 画线，两端都是星球节点。
- 布局：G6 `force` 布局。主论文节点用 d3-force 的 `fx/fy` 钉住：位置由两两相似度预先计算（相似度高的主论文放得更近，映射函数 `distance = D_max - similarity * (D_max - D_min)`，D_min/D_max 为常量）；子论文自由受力环绕各自主论文。若 G6 钉住方案在该版本不可用，降级为纯计算坐标 + preset 布局（实现期验证，两者都不需要新依赖）。
- 交互（G6 behaviors / 事件）：
  - `zoom-canvas`（滚轮缩放，天然解决需求 2）+ 自定义工具栏按钮调 `graph.zoomBy()` / `fitView()` / 重置；
  - `drag-canvas` 平移、`drag-node` 拖节点；
  - hover 节点高亮其一跳邻居，其余淡出；
  - 点击节点 → 右侧详情面板（标题/作者/年份/关系类型/角度分数/查看详情入口），再次点击空白关闭；
  - 图例：三种关系线型 + 星球配色说明；
  - 星空背景：图容器内绝对定位的 Canvas/CSS 星点层（允许引用网上开源星点/星空 CSS），不影响 G6 事件。
- 空/降级态：embedding 不可用时主论文均布并给出轻提示条。

**类型**（`frontend/src/types.ts`）：新增 `RelateRequest`/`RelateResponse`；`api.ts` 加 `fetchRelate(ids, refresh)`。

**前端测试**：把纯逻辑抽成 `frontend/src/cosmosLayout.ts`（相似度→距离/坐标映射、颜色分配、重叠归并），用现有 node:test + ts.transpileModule 模式加 `frontend/tests/cosmosLayout.test.mjs`，跑法挂进 `package.json` 脚本（与现有 test:graph 同风格）。CosmosGraph 渲染本身不写测试（与现状一致，前端无组件测试设施）。

### 配色使用细则

- 星球节点主色 = 上表 5 色，光晕用同色系半透明渐变（CSS/G6 halo）。
- 子论文节点 `#64748B`，文字 `#94A3B8`（背景上的可读灰，我的调用）。
- 选中/激活节点文字 `#FFFFFF`。
- 背景 `#080D1D`，星点亮色 `#FFFFFF` 低透明度。

## 明确留给实现者的部分

- 星点层具体实现（纯 CSS box-shadow 星图 / 独立 canvas 逐帧闪烁），以性能流畅为准。
- G6 v5 钉住主论文的具体机制（fx/fy 或 preset）以实现期验证为准。
- 详情面板的字段排版细节。
- S2 重试是否也覆盖 `fetch_s2_links`（默认覆盖所有走 `request()` 的 S2 调用）。

## 验证

- 后端：`python backend/tests/run_offline.py test_relate test_recommend test_provider_resilience`（含新增测试）全绿。
- 前端：`npm run build` 通过；`npm run test:graph` 及新增 cosmos 测试通过。
- 端到端：启动后端 + `npm run dev`，在论文关系页选 1/2/5 篇论文，确认重叠双连线、滚轮缩放、距离与相似度一致、第 6 篇弹提醒。
