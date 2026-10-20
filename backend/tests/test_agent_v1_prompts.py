"""Prompt wiring for agent v1: three-step find-papers flow in react/discovery prompts."""

import unittest

from app.agents.prompts import AGENT_PROMPT_VERSION, DOMAIN_PROMPTS, REACT_SYSTEM
from app.tools import research


class AgentV1PromptTests(unittest.TestCase):
    def test_react_system_mentions_v1_flow(self):
        self.assertIn("decompose_question", REACT_SYSTEM)
        self.assertIn("rerank_papers", REACT_SYSTEM)
        self.assertIn("recommended 一致", REACT_SYSTEM)
        self.assertIn("decompose_question", DOMAIN_PROMPTS["discovery"])
        self.assertIn("rerank_papers", DOMAIN_PROMPTS["discovery"])
        self.assertTrue(AGENT_PROMPT_VERSION.startswith("v18"), AGENT_PROMPT_VERSION)

    def test_catalog_descriptions_mention_flow(self):
        self.assertIn("不调用论文 API", research.CATALOG["decompose_question"]["description"])
        self.assertIn("不足原因", research.CATALOG["rerank_papers"]["description"])
        self.assertIn("筛选", research.CATALOG["search_papers"]["description"])
