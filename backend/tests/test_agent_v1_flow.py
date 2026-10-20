"""The retired v1 pipeline must not be reachable from the conversation graph.

Behavioral acceptance now lives in test_agent_native_flow, including public
API search, parameter correction, provenance and failure/stop behavior.
"""
import unittest

from app.agents import agent


class FindingGraphWiringTests(unittest.TestCase):
    def test_search_route_uses_the_two_researcher_nodes(self):
        compiled = agent.build()
        nodes = set(compiled.nodes)
        self.assertIn("finding_agent", nodes)
        self.assertIn("finding_tools", nodes)
        self.assertTrue({"plan_finding", "search_finding", "select_finding"}.isdisjoint(nodes))
        self.assertEqual(agent.route_route({"mode":"react", "research_intent":"discover", "task_kind":"research"}), "finding")
        self.assertEqual(agent.route_route({"mode":"direct", "research_intent":"auto", "task_kind":"chat"}), "direct")
