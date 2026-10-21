import pytest
from app.agents.planner import PlannerAgent, PlanOutput

def test_planner_defers_unconfident_queries_to_tool_calling_agent(monkeypatch):
    """
    The planner has no LLM classification tier any more: when the semantic router
    isn't confident, the query goes to retrieval via the tool-calling agent, which
    decides what to call. router_category stays None so the agent won't force a
    fallback search if the model chooses not to retrieve.
    """
    monkeypatch.setattr("app.retrieval.semantic_router.route_query", lambda q: (None, 0.31))
    query = "configure a volume in Kubernetes and PostgreSQL"
    plan = PlannerAgent.plan(query, available_tools=[])

    assert plan.intent == "single_lookup"
    assert plan.retrieval_strategy == "point_lookup"
    assert plan.needs_retrieval is True
    assert plan.router_category is None
    assert plan.sub_questions == [query]
    assert "tool-calling agent" in plan.reasoning


def test_planner_short_circuits_chitchat(monkeypatch):
    monkeypatch.setattr("app.retrieval.semantic_router.route_query", lambda q: ("chitchat", 0.97))
    plan = PlannerAgent.plan("hello there", available_tools=[])
    assert plan.intent == "chitchat"
    assert plan.needs_retrieval is False


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
