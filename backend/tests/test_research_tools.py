import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.agents import agent
from app.infrastructure import mineru
from app.main import app
from app.schemas import PaperOut, ResearchStart
from app.services.papers import documents, library, processing, upload, workspaces
from app.tools import BACKGROUND_TOOLS, TOOLS, research, index_paper as index_tool
from app.tools.research_scope import ToolContext, resolve
from app.tools import paper_evidence


async def embed(texts):
    return "BAAI/bge-m3", {text: [1., 0., 0., 0.] for text in texts}


class ResearchToolsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                          "PAPER_FAISS_PATH": str(Path(self.temp.name) / "vectors.faiss"),
                                          "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()
        workspaces.register("one", "一")
        workspaces.register("two", "二")
        self.a, _ = library.save_paper(PaperOut(id="arxiv:2401.12345", title="Alpha", year=2024, arxiv_id="2401.12345"), "arxiv:2401.12345")
        self.b, _ = library.save_paper(PaperOut(id="arxiv:2401.12346", title="Beta", year=2025), "arxiv:2401.12346")
        workspaces.add("one", self.a.local_id)
        workspaces.add("two", self.b.local_id)
        self.ctx = ToolContext(workspace_id="one", scope="workspace")

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    def test_catalog_has_exact_chat_and_background_sets(self):
        self.assertEqual(len(TOOLS), 9)
        self.assertEqual({t["name"] for t in BACKGROUND_TOOLS}, {"acquire_paper_fulltext", "parse_paper", "index_paper"})
        self.assertEqual({t["module"] for t in TOOLS}, {"discovery", "knowledge"})
        self.assertTrue(set(research.CATALOG) <= set(agent.TOOL_REGISTRY))
        self.assertTrue(all(t["parameters"]["type"] == "object" for t in TOOLS))
        for tool in BACKGROUND_TOOLS:
            self.assertNotIn(tool["name"], research.CATALOG)

    def test_scope_aliases_and_empty_selection_are_safe(self):
        self.assertEqual(resolve(self.ctx, [self.a.id])[0].local_id, self.a.local_id)
        with self.assertRaises(ValueError):
            resolve(self.ctx, [self.b.id])
        with self.assertRaises(ValueError):
            resolve(ToolContext(workspace_id="one"))
        with self.assertRaises(ValueError):
            resolve(ToolContext(workspace_id="one", scope="current", current_paper_id=self.b.id))
        self.assertEqual(resolve(ToolContext(scope="selected", selected_paper_ids=[self.a.id]))[0].id, self.a.id)


    async def test_conversation_grant_crosses_workspaces_without_reading_content(self):
        ctx = ToolContext(workspace_id="one", accessible_paper_ids=[self.a.id, self.b.local_id])
        with patch("app.services.papers.documents.get_text", side_effect=AssertionError("no fulltext read")), \
             patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=AssertionError("no model"))):
            self.assertEqual({p.local_id for p in resolve(ctx)}, {self.a.local_id, self.b.local_id})
            self.assertEqual(len(resolve(ctx)), 2)
            self.assertEqual(resolve(ctx, [self.b.id])[0].local_id, self.b.local_id)
        self.assertEqual(len(workspaces.list_papers("one")["papers"]), 1)

    async def test_empty_and_narrowed_grants_cannot_expand_through_arguments(self):
        for ids in ([], [self.a.id]):
            ctx = ToolContext(workspace_id="one", accessible_paper_ids=ids)
            with self.assertRaises(ValueError):
                resolve(ctx, [self.b.id])
            with self.assertRaises(ValueError):
                await research.execute("query_knowledge_graph", {"paper_ids": [self.b.id]}, ctx)
            with self.assertRaises(ValueError):
                await research.execute("analyze_research_trends", {"paper_ids": [self.b.id]}, ctx)
            graph = await research.execute("query_knowledge_graph", {}, ctx)
            self.assertEqual(graph["total_papers"], len(ids))
        self.assertEqual(resolve(ToolContext(workspace_id="one", accessible_paper_ids=[])), [])

    async def test_cross_workspace_vector_retrieval_and_revocation(self):
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=embed):
            await index_tool.index_paper(self.a.local_id, text="RAG retrieval evidence Alpha")
            await index_tool.index_paper(self.b.local_id, text="PDF cleaning retrieval Beta")
            both = ToolContext(workspace_id="one", accessible_paper_ids=[self.a.id, self.b.id])
            result = await paper_evidence.retrieve(resolve(both), "retrieval", 8)
            self.assertEqual({e["paper_id"] for e in result["evidence"]}, {self.a.id, self.b.id})
            narrowed = ToolContext(workspace_id="one", accessible_paper_ids=[self.a.id])
            result = await paper_evidence.retrieve(resolve(narrowed), "retrieval", 8)
            self.assertTrue(all(e["paper_id"] == self.a.id for e in result["evidence"]))
            with self.assertRaises(ValueError):
                paper_evidence.context(resolve(narrowed), f"C0001:{self.b.local_id}", 1)

    async def test_task_api_preserves_explicit_grant_and_history(self):
        runner = AsyncMock(return_value={"task_id": "access-test", "status": "done"})
        payload = {"goal": "结合这两篇继续研究", "workspace_id": "one",
                   "accessible_paper_ids": [self.a.id, self.b.id], "conversation_history": "此前讨论了检索质量"}
        with patch("app.agents.agent.run", new=runner):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/research/tasks", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        context = runner.call_args.kwargs["context"]
        self.assertEqual(context["accessible_paper_ids"], payload["accessible_paper_ids"])
        state = agent.initial_state(payload["goal"], context=context)
        self.assertEqual(agent.public_state(state)["conversation_history"], payload["conversation_history"])
        from app.agents.tooling import allowed_tools
        self.assertNotIn("request_deep_analysis", allowed_tools(state))
        updated = agent._merge_clarification(state, {"accessible_paper_ids": []})
        self.assertEqual(updated["accessible_paper_ids"], [])

    async def test_retrieve_real_index_has_pages_and_never_leaks(self):
        source = "Alpha retrieval evidence. " * 60
        structure = {"text": source.strip(), "blocks": [{"text": source.strip(), "page_number": 3, "section_id": "s1"}],
                     "sections": [{"id": "s1", "title": "Methods"}]}
        documents.save(self.a.local_id, self.a.id, source.strip(), "a.pdf", structure)
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=embed):
            await index_tool.index_paper(self.a.local_id, text=source)
            await index_tool.index_paper(self.b.local_id, text="Beta SECRET other workspace.")
            result = await paper_evidence.retrieve(resolve(self.ctx), "retrieval", 8)
        self.assertTrue(result["evidence"])
        self.assertTrue(all(item["paper_id"] == self.a.id for item in result["evidence"]))
        self.assertEqual(result["evidence"][0]["page_number"], 3)
        self.assertEqual(result["evidence"][0]["section"], "Methods")
        context = paper_evidence.context(resolve(self.ctx), result["evidence"][0]["chunk_id"], 1)
        self.assertTrue(context["blocks"])
        with self.assertRaises(ValueError):
            paper_evidence.context(resolve(self.ctx), f"C0001:{self.b.local_id}", 1)

    async def test_retrieval_does_not_reindex_and_degrades_without_embedding(self):
        empty = await paper_evidence.retrieve(resolve(self.ctx), "retrieval", 8)
        self.assertEqual(empty["status"], "not_indexed")
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=embed):
            await index_tool.index_paper(self.a.local_id, text="retrieval supports evidence")
        from app.infrastructure.embeddings import EmbeddingError
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=AsyncMock(side_effect=EmbeddingError("missing key"))), \
             patch("app.tools.index_paper.index_paper", new=AsyncMock(side_effect=AssertionError("no indexing"))):
            result = await paper_evidence.retrieve(resolve(self.ctx), "retrieval", 8)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["evidence"])
        self.assertIsNone(result["evidence"][0]["page_number"])

    async def test_remove_preserves_shared_paper_and_documents(self):
        workspaces.add("two", self.a.local_id)
        documents.save(self.a.local_id, self.a.id, "source", "a.pdf")
        workspaces.remove("one", [self.a.id])
        self.assertEqual(workspaces.list_papers("one")["papers"], [])
        self.assertEqual(len(workspaces.list_papers("two")["papers"]), 2)
        self.assertEqual(documents.get_text(self.a.id), "source")


    async def test_citation_unavailable_is_not_no_citations(self):
        with patch("app.services.retrieval.search.fetch_s2_links", new=AsyncMock(side_effect=research.search.SourceFailure("429"))), \
             patch("app.services.retrieval.search.semantic_enabled", return_value=True):
            result = await research.execute("get_paper_citations", {"paper_id": self.a.id}, self.ctx)
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(result["warnings"])

    async def test_graph_and_trends_only_describe_explicit_sample(self):
        graph = await research.execute("query_knowledge_graph", {}, self.ctx)
        self.assertEqual(graph["total_papers"], 1)
        stats = await research.execute("analyze_research_trends", {}, self.ctx)
        self.assertEqual(stats["sample_size"], 1)
        self.assertEqual(stats["year_distribution"], [{"year": 2024, "count": 1}])
        self.assertTrue(any("不能代表" in warning for warning in stats["warnings"]))


    async def test_processing_reuses_saved_source_and_index(self):
        documents.save(self.a.local_id, self.a.id, "saved source", "a.pdf")
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=embed):
            first = await processing.process(self.a.local_id)
        self.assertEqual(first["status"], "completed")
        with patch("app.services.papers.processing.parse_paper", new=AsyncMock(side_effect=AssertionError("must reuse"))), \
             patch("app.services.papers.processing.acquire_paper_fulltext", new=AsyncMock(side_effect=AssertionError("must reuse"))):
            self.assertTrue((await processing.process(self.a.local_id))["reused"])

    async def test_api_catalog_context_and_invalid_arguments(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            catalog = (await client.get("/api/tools")).json()
            self.assertEqual(len(catalog["chat_tools"]), 9)
            response = await client.post("/api/research/tools/query_knowledge_graph", json={"context": self.ctx.model_dump()})
            self.assertEqual(response.status_code, 200, response.text)
            bad = await client.post("/api/tools/query_knowledge_graph", json={"arguments": {"paper_ids": []}})
            self.assertEqual(bad.status_code, 422)
            escape = await client.post("/api/tools/query_knowledge_graph", json={"arguments": {"paper_ids": [self.b.id]}, "context": self.ctx.model_dump()})
            self.assertEqual(escape.status_code, 400)
            hidden = await client.post("/api/research/tools/index_paper", json={})
            self.assertEqual(hidden.status_code, 404)

    async def test_uploaded_structured_source_keeps_pages(self):
        parsed = {"markdown": "# Alpha\n\nsource", "text": "Alpha\n\nsource", "blocks": [
            {"id": "b1", "text": "Alpha", "page_number": 1}, {"id": "b2", "text": "source", "page_number": 2}], "sections": []}
        with patch("app.services.papers.upload.parse_paper", new=AsyncMock(return_value=parsed)), \
             patch("app.infrastructure.embeddings.embed_siliconflow", new=embed):
            result = await upload.ingest_pdf(b"%PDF test", "a.pdf", "one")
        saved = result["paper"]
        self.assertEqual(documents.get_structure(saved.local_id)["blocks"][1]["page_number"], 2)
        self.assertEqual(documents.get_text(saved.local_id), parsed["text"])


    async def test_metadata_and_sample_statistics_are_not_original_paper_evidence(self):
        state = agent.initial_state("查询论文详情", context=self.ctx.model_dump())
        content, updates = await agent._run_business_tool(state, "get_paper_details", {"paper_id": self.a.id})
        records = json.loads(content)["record_evidence"]
        self.assertEqual(records[0]["material"], "metadata")
        self.assertTrue(all(source["material"] == "metadata" for source in updates["evidence_sources"].values()))
        self.assertEqual(updates["evidence_sources"]["E0001"]["tool"], "get_paper_details")

    async def test_removed_tools_are_unavailable_in_catalog_agent_and_http(self):
        removed = {"list_workspace_papers", "add_papers_to_workspace", "remove_paper_from_workspace",
                   "analyze_paper", "get_paper_analysis", "retrieve_paper_evidence", "read_paper_context"}
        state = agent.initial_state("goal", context=self.ctx.model_dump())
        from app.agents.tooling import allowed_tools
        self.assertFalse(removed & allowed_tools(state))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for name in removed:
                with self.subTest(tool=name):
                    self.assertNotIn(name, research.CATALOG)
                    self.assertNotIn(name, agent.TOOL_REGISTRY)
                    with self.assertRaisesRegex(ValueError, "未知"):
                        await research.execute(name, {}, self.ctx)
                    content, updates = await agent._run_business_tool(state, name, {})
                    self.assertIn("error", json.loads(content))
                    self.assertEqual(updates, {})
                    for prefix in ("/api/tools/", "/api/research/tools/"):
                        response = await client.post(prefix + name, json={"arguments": {}, "context": self.ctx.model_dump()})
                        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(len(workspaces.list_papers("one")["papers"]), 1)
        self.assertIsNone(processing.status(self.b.local_id))

    async def test_page_add_endpoint_still_queues_once(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            payload = {"workspace_id": "one", "paper_ids": [self.b.id]}
            first = await client.post("/api/workspaces/papers/add", json=payload)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["results"][0]["processing"]["status"], "pending")
            stamp = processing.status(self.b.local_id)["updated_at"]
            again = await client.post("/api/workspaces/papers/add", json=payload)
            self.assertEqual(again.status_code, 200, again.text)
            self.assertEqual(again.json()["already_exists"], 1)
            self.assertEqual(processing.status(self.b.local_id)["updated_at"], stamp)
