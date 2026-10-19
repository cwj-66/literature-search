"""分档预算预设：fast/medium/high 载入初始状态并驱动循环上限。"""

import unittest

from app.agents import agent, budgets
from app.agents.state import MAX_API, MAX_DEEP, MAX_EXTRA_SEARCH, MAX_JUDGE_ROUNDS, MAX_LLM, MAX_REACT, initial_state


class BudgetPresetTests(unittest.TestCase):
    def test_medium_preset_matches_legacy_constants(self):
        preset = budgets.resolve_budget("medium")
        self.assertEqual(preset.max_api_calls, MAX_API)
        self.assertEqual(preset.max_llm_calls, MAX_LLM)
        self.assertEqual(preset.max_extra_search, MAX_EXTRA_SEARCH)
        self.assertEqual(preset.max_deep_reads, MAX_DEEP)
        self.assertEqual(preset.max_react, MAX_REACT)
        self.assertEqual(preset.max_judge_rounds, MAX_JUDGE_ROUNDS)

    def test_unknown_budget_name_falls_back_to_medium(self):
        self.assertEqual(budgets.resolve_budget("nonexistent"), budgets.resolve_budget("medium"))

    def test_fast_preset_is_stricter_than_medium(self):
        fast = budgets.resolve_budget("fast")
        medium = budgets.resolve_budget("medium")
        self.assertLess(fast.max_llm_calls, medium.max_llm_calls)
        self.assertLess(fast.max_react, medium.max_react)

    def test_initial_state_loads_named_budget(self):
        state = initial_state("goal", budget="fast")
        fast = budgets.resolve_budget("fast")
        self.assertEqual(state["budget"], "fast")
        self.assertEqual(state["max_llm_calls"], fast.max_llm_calls)
        self.assertEqual(state["max_api_calls"], fast.max_api_calls)
        self.assertEqual(state["max_react"], fast.max_react)
        self.assertEqual(state["max_judge_rounds"], fast.max_judge_rounds)

    def test_initial_state_default_budget_is_medium(self):
        state = initial_state("goal")
        self.assertEqual(state["budget"], "medium")
        self.assertEqual(state["max_llm_calls"], MAX_LLM)


class BudgetDrivenLoopTests(unittest.IsolatedAsyncioTestCase):
    def test_judge_route_respects_state_budget(self):
        state = {**initial_state("goal", budget="fast"),
                 "claims": [{"claim_id": "c1", "text": "t", "evidence_ids": ["E0001"], "status": "rule_failed"}]}
        fast = budgets.resolve_budget("fast")
        for rounds in range(fast.max_judge_rounds):
            state["review"] = {"rounds": rounds}
            self.assertEqual(agent.judge_route(state), "revise", f"round {rounds} 应继续补证")
        state["review"] = {"rounds": fast.max_judge_rounds}
        self.assertEqual(agent.judge_route(state), "finalize")

    async def test_react_agent_stops_at_state_budget(self):
        state = {**initial_state("goal", budget="fast"), "react_turns": budgets.resolve_budget("fast").max_react}
        update = await agent.react_agent(state)
        self.assertEqual(update["stop_reason"], "达到最大迭代次数")
