"""Live finder prompt and tool schema agree; legacy prompts remain separate."""

import unittest

from app.agents.prompts import AGENT_PROMPT_VERSION, FINDING_SYSTEM, ROUTER_SYSTEM
from app.tools import finding_tools


class AgentV1PromptTests(unittest.TestCase):
    def test_live_finder_only_describes_native_tools(self):
        for name in ("search_papers", "get_paper_detail", "submit_papers"):
            self.assertIn(name, FINDING_SYSTEM)
        self.assertNotIn("decompose_question", FINDING_SYSTEM + ROUTER_SYSTEM)
        self.assertNotIn("rerank_papers", FINDING_SYSTEM)
        self.assertEqual(set(finding_tools.MODELS), {"search_papers", "get_paper_detail", "submit_papers"})
        self.assertTrue(AGENT_PROMPT_VERSION.startswith("v20"), AGENT_PROMPT_VERSION)

    def test_years_are_tool_arguments_not_a_separate_planning_gate(self):
        schema = finding_tools.SearchArgs.model_json_schema()
        self.assertEqual(set(schema["properties"]), {"query", "year_from", "year_to"})
        self.assertEqual(schema["required"], ["query"])
        self.assertFalse(schema["additionalProperties"])
