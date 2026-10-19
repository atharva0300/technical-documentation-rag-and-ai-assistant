import pytest
from app.agents.planner import PlannerAgent
from app.agents.verifier import VerifierAgent
from app.agents.graph import run_v2_agent_workflow

def test_planner_chitchat_fastpath():
    plan = PlannerAgent.plan("hello")
    assert plan.intent == "chitchat"
    assert plan.needs_retrieval is False

def test_planner_out_of_scope_fastpath():
    plan = PlannerAgent.plan("write a poem about cars")
    assert plan.intent == "out_of_scope"
    assert plan.needs_retrieval is False

def test_planner_comparison_intent():
    plan = PlannerAgent.plan("Compare HNSW vs IVFFlat indexing")
    assert plan.intent == "comparison"
    assert plan.needs_retrieval is True
    assert len(plan.sub_questions) >= 1

def test_verifier_citation_range():
    res = VerifierAgent.verify(
        answer="HNSW uses iterative scan [10].",
        evidence=[{"content": "HNSW docs"}], # Only 1 item
        retry_count=0
    )
    assert res.status == "RETRY"
    assert "Invalid citation indices" in res.feedback

def test_verifier_token_grounding_catches_fabricated_flag():
    res = VerifierAgent.verify(
        answer="Use flag --nonexistent-fake-flag [1].",
        evidence=[{"content": "PostgreSQL configuration memory settings --memory-limit"}],
        retry_count=0
    )
    assert res.status in ("RETRY", "PARTIAL")

def test_v2_agent_workflow_chitchat():
    res = run_v2_agent_workflow("hi", conversation_id="test_conv")
    assert res["intent"] == "chitchat"
    assert "Hello" in res["answer"]
