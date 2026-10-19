"""ReAct 提示词边界：复合指令应交回 plan 流水线，兜底路径不硬扛。"""

import unittest

from app.agents import prompts


class ReactPromptBoundaryTests(unittest.TestCase):
    def test_react_system_declares_plan_boundary(self):
        self.assertIn("plan", prompts.REACT_SYSTEM)
        self.assertIn("复合指令", prompts.REACT_SYSTEM)

    def test_prompt_version_bumped(self):
        self.assertNotEqual(prompts.AGENT_PROMPT_VERSION, "v9-evidence-workflow")


if __name__ == "__main__":
    unittest.main()
