import json
import pytest
from app.agents.planner import PlannerAgent, PlanOutput

def test_planner_bare_and_uses_llm_fallback():
    """
    Test that queries containing bare 'and' (e.g. 'configure a volume in Kubernetes and PostgreSQL')
    do NOT trigger the naive regex comparison fast-path (which split on bare 'and'),
    and instead route to the LLM path producing the correct classification.
    """
    query = "configure a volume in Kubernetes and PostgreSQL"

    def mock_llm_fn(user_query: str, system: str) -> str:
        assert user_query == query
        return json.dumps({
            "intent": "single_lookup",
            "needs_retrieval": True,
            "needs_tools": False,
            "sub_questions": [query],
            "reasoning": "Determined by LLM fallback that query is a single lookup despite bare word 'and'."
        })

    plan = PlannerAgent.plan(query, llm_fn=mock_llm_fn)
    assert plan.intent == "single_lookup"
    assert plan.sub_questions == [query]
    assert "LLM Classification" in plan.reasoning
    assert "single lookup despite bare word 'and'" in plan.reasoning

def test_planner_unambiguous_comparison_fast_path():
    """
    Test that unambiguous comparison queries (with 'vs' or 'compare') are
    classified via the semantic router (embedding similarity to example
    comparison utterances), not a keyword/regex fast path.
    """
    query = "Compare Kubernetes vs Docker Swarm"
    plan = PlannerAgent.plan(query)
    assert plan.intent == "comparison"
    assert len(plan.sub_questions) == 2
    assert "router matched 'comparison'" in plan.reasoning.lower()
