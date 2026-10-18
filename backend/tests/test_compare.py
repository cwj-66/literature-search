import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.main import app
from app.schemas import AnalysisClaim, AnalysisFields, PaperOut
from app.services.analysis import analysis_store
from app.services.analysis.evidence_analysis import UNKNOWN
from app.services.analysis.fulltext import PreparedText
from app.services.papers import library


def fields(**extra) -> AnalysisFields:
    values = dict(
        research_problem="研究问题甲", main_content="主要内容", technical_method="核心方法甲",
        innovations=["作者声称的贡献甲"], experimental_results="主要结果甲", limitations="局限甲",
        keywords=["检索"], analysis_basis="abstract",
        claims=[
            AnalysisClaim(
                kind="research_problem", text="研究问题甲", status="supported",
                evidence=[{"paragraph_id": "P0001", "quote": "The stated research problem is here."}],
            ),
            AnalysisClaim(
                kind="evaluation", text="准确率 91%", status="supported",
                dataset="数据集甲", metric="准确率", result="91%", conditions="条件甲", baseline="基线甲",
                evidence=[{"paragraph_id": "P0002", "quote": "Accuracy is 91 percent on dataset A."}],
            ),
        ],
    )
    values.update(extra)
    return AnalysisFields(**values)


def finish(local_id: str, ref: str, payload: AnalysisFields) -> None:
    decision = analysis_store.create_task(ref, {ref, local_id}, local_id, False, "test", "v")
    task_id = decision.task_row["task_id"]
    assert analysis_store.transition(task_id, "pending", "preparing")
    assert analysis_store.transition(task_id, "preparing", "analyzing")
    prepared = PreparedText("摘要", "abstract", "abstract", None, None, {"sections": []})
    assert analysis_store.save_success(task_id, local_id, payload, prepared, "test", "v")


class CompareApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "ANALYSIS_WORKER": "0",
        })
        self.env.start()
        self.guards = [
            patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=AssertionError("compare must not call the model"))),
            patch("app.infrastructure.llm.analyze", new=AsyncMock(side_effect=AssertionError("compare must not analyze"))),
            patch("app.services.analysis.analysis.enqueue", new=AsyncMock(side_effect=AssertionError("compare must not start analysis"))),
            patch("app.services.retrieval.recommend.recommendations", new=AsyncMock(side_effect=AssertionError("compare must not recommend"))),
        ]
        for guard in self.guards:
            guard.start()

    def tearDown(self):
        for guard in self.guards:
            guard.stop()
        self.env.stop()
        self.temp.cleanup()

    def add(self, index: int, payload: AnalysisFields | None = None, analyze: bool = True) -> str:
        ref = f"arxiv:{index}"
        saved, _ = library.save_paper(PaperOut(
            id=ref, arxiv_id=str(index), title=f"论文{index}", year=2020, authors=["作者"],
            abstract="摘要", venue="会议", sources=["arxiv"], citation_count=3,
        ), ref)
        if analyze:
            finish(saved.local_id, ref, payload or fields())
        return ref

    async def post(self, ids: list[str]):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/compare", json={"paper_ids": ids})

    def cell(self, body: dict, title: str, dimension: str) -> dict:
        row = next(item for item in body["rows"] if item["title"] == title)
        return next(item for item in row["cells"] if item["dimension"] == dimension)

    async def test_two_and_five_analyzed_papers_build_a_table(self):
        pair = [self.add(1), self.add(2)]
        two = await self.post(pair)
        self.assertEqual(two.status_code, 200, two.text)
        body = two.json()
        self.assertEqual(len(body["rows"]), 2)
        self.assertEqual(len(body["dimensions"]), 8)
        self.assertIn("不直接按数字排名", body["notes"][0])
        problem = self.cell(body, "论文1", "研究问题")
        self.assertEqual(problem["text"], "研究问题甲")
        self.assertEqual(problem["evidence"][0]["paragraph_id"], "P0001")
        self.assertEqual(problem["evidence_ids"], [])
        self.assertNotIn("综合评价", str(body))
        five = await self.post([self.add(index) for index in range(3, 8)])
        self.assertEqual(five.status_code, 200, five.text)
        self.assertEqual(len(five.json()["rows"]), 5)

    async def test_unfinished_analysis_is_rejected_without_a_model_call(self):
        ready = self.add(1)
        missing = self.add(2, analyze=False)
        response = await self.post([ready, missing])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("论文2（未分析）", response.json()["detail"])
        running = self.add(3)
        saved = library.get_paper(running)
        analysis_store.create_task(running, {running, saved.local_id}, saved.local_id, True, "test", "v")
        active = await self.post([ready, running])
        self.assertEqual(active.status_code, 409, active.text)
        self.assertIn("论文3（分析中）", active.json()["detail"])
        failed = self.add(4, analyze=False)
        paper = library.get_paper(failed)
        decision = analysis_store.create_task(failed, {failed, paper.local_id}, paper.local_id, False, "test", "v")
        analysis_store.fail(decision.task_row["task_id"], "分析失败")
        broken = await self.post([ready, failed])
        self.assertEqual(broken.status_code, 409, broken.text)
        self.assertIn("论文4（分析失败）", broken.json()["detail"])

    async def test_completed_analysis_keeps_missing_fields_blank(self):
        ref = self.add(1, fields(
            limitations=UNKNOWN, innovations=[UNKNOWN], experimental_results=UNKNOWN,
            claims=[
                AnalysisClaim(
                    kind="research_problem", text="只提取到研究问题", status="supported",
                    evidence=[{"paragraph_id": "P0003", "quote": "Only the problem sentence is present."}],
                ),
            ],
        ))
        response = await self.post([ref, self.add(2)])
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(self.cell(body, "论文1", "研究问题")["text"], "只提取到研究问题")
        for dimension in ("局限性", "作者声称的贡献", "数据集与实验任务", "主要结果"):
            cell = self.cell(body, "论文1", dimension)
            self.assertTrue(cell["missing"], dimension)
            self.assertEqual(cell["text"], "未提取到")
            self.assertEqual(cell["evidence"], [])
