"""Named budget presets for research tasks, loaded at task creation.

Presets cap API/LLM calls, extra searches, deep reads, ReAct turns and judge
rounds. "medium" reproduces the legacy module-level constants so existing
behaviour is the default; "fast" trims long loops for cheap ad-hoc queries;
"high" widens them for deep surveys.
"""

from dataclasses import dataclass

from app.agents.state import MAX_API, MAX_DEEP, MAX_EXTRA_SEARCH, MAX_JUDGE_ROUNDS, MAX_LLM, MAX_REACT


@dataclass(frozen=True, slots=True)
class BudgetPreset:
    label: str
    max_api_calls: int
    max_llm_calls: int
    max_extra_search: int
    max_deep_reads: int
    max_react: int
    max_judge_rounds: int


_LEGACY = dict(max_api_calls=MAX_API, max_llm_calls=MAX_LLM, max_extra_search=MAX_EXTRA_SEARCH,
               max_deep_reads=MAX_DEEP, max_react=MAX_REACT, max_judge_rounds=MAX_JUDGE_ROUNDS)

BUDGET_PRESETS: dict[str, BudgetPreset] = {
    "fast": BudgetPreset("fast", max_api_calls=4, max_llm_calls=10, max_extra_search=1, max_deep_reads=2,
                         max_react=10, max_judge_rounds=1),
    "medium": BudgetPreset("medium", **_LEGACY),
    "high": BudgetPreset("high", max_api_calls=12, max_llm_calls=40, max_extra_search=3, max_deep_reads=8,
                         max_react=36, max_judge_rounds=3),
}

DEFAULT_BUDGET = "medium"


def resolve_budget(name: str | None) -> BudgetPreset:
    return BUDGET_PRESETS.get(name or "", BUDGET_PRESETS[DEFAULT_BUDGET])


__all__ = ["BudgetPreset", "BUDGET_PRESETS", "DEFAULT_BUDGET", "resolve_budget"]
