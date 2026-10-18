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
        self.assertEqual(len(TOOLS), 14)
        self.assertEqual({t["name"] for t in BACKGROUND_TOOLS}, {"acquire_paper_fulltext", "parse_paper", "index_paper"})
        self.assertEqual({t["module"] for t in TOOLS}, {"discovery", "workspace", "analysis", "knowledge"})
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

    async def test_list_and_cached_analysis_do_not_call_model(self):
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=AssertionError("no model"))):
            result = await research.execute("list_workspace_papers", {}, self.ctx)
            self.assertEqual(len(result["papers"]), 1)
            self.assertEqual(result["papers"][0]["analysis"]["status"], "missing")
            result = await research.execute("get_paper_analysis", {"paper_id": self.a.id}, self.ctx)
            self.assertEqual(result["status"], "missing")
            with self.assertRaises(ValueError):
                await research.execute("get_paper_analysis", {"paper_id": self.b.id}, self.ctx)

    async def test_retrieve_real_index_has_pages_and_never_leaks(self):
        source = "Alpha retrieval evidence. " * 60
        structure = {"text": source.strip(), "blocks": [{"text": source.strip(), "page_number": 3, "section_id": "s1"}],
                     "sections": [{"id": "s1", "title": "Methods"}]}
        documents.save(self.a.local_id, self.a.id, source.strip(), "a.pdf", structure)
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=embed):
            await index_tool.index_paper(self.a.local_id, text=source)
            await index_tool.index_paper(self.b.local_id, text="Beta SECRET other workspace.")
            result = await research.execute("retrieve_paper_evidence", {"query": "retrieval"}, self.ctx)
        self.assertTrue(result["evidence"])
        self.assertTrue(all(item["paper_id"] == self.a.id for item in result["evidence"]))
        self.assertEqual(result["evidence"][0]["page_number"], 3)
        self.assertEqual(result["evidence"][0]["section"], "Methods")
        context = await research.execute("read_paper_context", {"chunk_id": result["evidence"][0]["chunk_id"]}, self.ctx)
        self.assertTrue(context["blocks"])
        with self.assertRaises(ValueError):
            await research.execute("read_paper_context", {"chunk_id": f"C0001:{self.b.local_id}"}, self.ctx)

    async def test_retrieval_does_not_reindex_and_degrades_without_embedding(self):
        empty = await research.execute("retrieve_paper_evidence", {"query": "retrieval"}, self.ctx)
        self.assertEqual(empty["status"], "not_indexed")
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=embed):
            await index_tool.index_paper(self.a.local_id, text="retrieval supports evidence")
        from app.infrastructure.embeddings import EmbeddingError
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=AsyncMock(side_effect=EmbeddingError("missing key"))), \
             patch("app.tools.index_paper.index_paper", new=AsyncMock(side_effect=AssertionError("no indexing"))):
            result = await research.execute("retrieve_paper_evidence", {"query": "retrieval"}, self.ctx)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["evidence"])
        self.assertIsNone(result["evidence"][0]["page_number"])

    async def test_remove_preserves_shared_paper_and_documents(self):
        workspaces.add("two", self.a.local_id)
        documents.save(self.a.local_id, self.a.id, "source", "a.pdf")
        await research.execute("remove_paper_from_workspace", {"paper_id": self.a.id}, self.ctx)
        self.assertEqual(workspaces.list_papers("one")["papers"], [])
        self.assertEqual(len(workspaces.list_papers("two")["papers"]), 2)
        self.assertEqual(documents.get_text(self.a.id), "source")

    async def test_add_queues_once_and_cannot_change_chat_workspace(self):
        result = await research.execute("add_papers_to_workspace", {"paper_ids": [self.b.id]}, self.ctx)
        self.assertEqual(result["results"][0]["processing"]["status"], "pending")
        stamp = processing.status(self.b.local_id)["updated_at"]
        again = await research.execute("add_papers_to_workspace", {"paper_ids": [self.b.id]}, self.ctx)
        self.assertEqual(again["already_exists"], 1)
        self.assertEqual(processing.status(self.b.local_id)["updated_at"], stamp)
        with self.assertRaises(ValueError):
            await research.execute("add_papers_to_workspace", {"workspace_id": "two", "paper_ids": [self.b.id]}, self.ctx)

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

    async def test_agent_evidence_binds_actual_paper_and_restores_scope(self):
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=embed):
            await index_tool.index_paper(self.a.local_id, text="Alpha retrieval supports original evidence.")
            state = agent.initial_state("question", context=self.ctx.model_dump())
            content, updates = await agent._run_business_tool(state, "retrieve_paper_evidence", {"query": "retrieval"})
        self.assertEqual(set(updates["evidence_papers"].values()), {self.a.id})
        self.assertEqual(json.loads(content)["evidence"][0]["evidence_id"], "E0001")
        self.assertEqual(agent.public_state(state)["workspace_id"], "one")

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
            self.assertEqual(len(catalog["chat_tools"]), 14)
            response = await client.post("/api/research/tools/list_workspace_papers", json={"context": self.ctx.model_dump()})
            self.assertEqual(response.status_code, 200, response.text)
            bad = await client.post("/api/tools/retrieve_paper_evidence", json={"arguments": {"query": " "}})
            self.assertEqual(bad.status_code, 422)
            escape = await client.post("/api/tools/get_paper_analysis", json={"arguments": {"paper_id": self.b.id}, "context": self.ctx.model_dump()})
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

    async def test_new_chat_tool_hitl_resume_executes_only_approved_ids(self):
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.graph import StateGraph, START, END
        from langgraph.types import Command
        builder = StateGraph(agent.AgentState)
        builder.add_node("tools", agent.tool_node)
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=MemorySaver())
        state = agent.initial_state("添加论文", context=self.ctx.model_dump())
        state["messages"] = [{"role": "assistant", "content": "", "tool_calls": [
            {"id": "add1", "name": "add_papers_to_workspace", "args": {"paper_ids": [self.b.id]}, "type": "tool_call"}]}]
        config = {"configurable": {"thread_id": "tools-hitl"}}
        first = await graph.ainvoke(state, config)
        self.assertTrue(first.get("__interrupt__"))
        self.assertEqual(len(workspaces.list_papers("one")["papers"]), 1)
        final = await graph.ainvoke(Command(resume={"approved_ids": [self.b.id, "unrequested"]}), config)
        self.assertEqual(len(workspaces.list_papers("one")["papers"]), 2)
        self.assertEqual(processing.status(self.b.local_id)["status"], "pending")
        self.assertTrue(any(getattr(message, "name", None) == "add_papers_to_workspace" for message in final["messages"]))

    async def test_metadata_and_sample_statistics_are_not_original_paper_evidence(self):
        state = agent.initial_state("列出工作区论文", context=self.ctx.model_dump())
        content, updates = await agent._run_business_tool(state, "list_workspace_papers", {})
        records = json.loads(content)["record_evidence"]
        self.assertEqual(records[0]["material"], "metadata")
        self.assertEqual(updates["evidence_papers"], {})
        self.assertEqual(updates["evidence_sources"]["E0001"]["tool"], "list_workspace_papers")
