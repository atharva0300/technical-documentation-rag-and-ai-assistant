import pytest
from app.agents.planner import PlannerAgent, PlanOutput
from app.agents.verifier import VerifierAgent
from app.agents.memory import MemoryLoader
from app.agents.graph import run_v2_agent_workflow

def test_direct_lookup_intent():
    plan = PlannerAgent.plan("How to create an HNSW index in pgvector?")
    assert plan.intent == "single_lookup"
    assert plan.needs_retrieval is True
    assert len(plan.sub_questions) == 1

def test_multi_hop_decomposition():
    plan = PlannerAgent.plan("Explain HNSW architecture and how does efConstruction affect index building?")
    assert plan.intent == "multi_hop"
    assert plan.needs_retrieval is True
    assert len(plan.sub_questions) >= 2

def test_comparison_intent_decomposition():
    plan = PlannerAgent.plan("Compare Docker volumes vs Kubernetes persistent volumes")
    assert plan.intent == "comparison"
    assert plan.needs_retrieval is True
    assert len(plan.sub_questions) >= 2

def test_exact_term_flag_preservation():
    """
    "exact_term" is no longer a distinct `intent` value -- lexical-specificity
    handling moved to the retrieval-fusion layer (app.retrieval.query_analysis),
    computed from the corpus's own BM25 statistics instead of a planner fast-path
    hardcoded to this demo's vocabulary (kubectl/docker). What must still hold is
    that the CLI flag text survives verbatim into the sub-question sent to retrieval.
    """
    query = "What does the --memory flag do in PostgreSQL configuration?"
    plan = PlannerAgent.plan(query)
    assert plan.intent in ("single_lookup", "multi_hop", "comparison")
    assert "--memory" in plan.sub_questions[0]

def test_followup_contextualization_and_conversation_isolation():
    history_a = [
        {"role": "user", "content": "What is a Kubernetes Service?"},
        {"role": "assistant", "content": "A Service routes traffic to pods."}
    ]
    history_b = [
        {"role": "user", "content": "What is Docker Compose?"},
        {"role": "assistant", "content": "Docker Compose manages multi-container apps."}
    ]

    query = "What types are available?"
    rewritten_a = MemoryLoader.rewrite_query(query, history_a)
    rewritten_b = MemoryLoader.rewrite_query(query, history_b)

    assert "Kubernetes Service" in rewritten_a
    assert "Docker Compose" in rewritten_b
    assert rewritten_a != rewritten_b

def test_verifier_catches_invalid_citation_and_retries():
    res = VerifierAgent.verify(
        answer="HNSW uses iterative scanning [99].",
        evidence=[{"content": "HNSW index docs"}], # Only 1 evidence item exists
        retry_count=0,
        max_retries=2
    )
    assert res.status == "RETRY"
    assert "Invalid citation" in res.feedback

def test_verifier_catches_unsupported_technical_flag():
    res = VerifierAgent.verify(
        answer="Set configuration flag --fake-unsupported-flag=1 [1].",
        evidence=[{"content": "PostgreSQL memory configuration --shared-buffers"}],
        retry_count=0,
        max_retries=2
    )
    assert res.status in ("RETRY", "PARTIAL")

def test_verifier_retry_budget_exhaustion_refuses():
    res = VerifierAgent.verify(
        answer="Set configuration flag --fake-unsupported-flag=1 [1].",
        evidence=[{"content": "PostgreSQL memory configuration"}],
        retry_count=2, # Retries exhausted
        max_retries=2
    )
    assert res.status in ("PARTIAL", "REFUSE")

def test_unanswerable_query_workflow():
    res = run_v2_agent_workflow("What is quantum teleportation protocol in Postgres 99?", conversation_id="test_unans")
    assert res["verification_status"] in ("VALID", "PARTIAL", "REFUSE")


def test_verifier_refuses_uncited_answer_after_retries_exhausted():
    from app.agents.verifier import UNGROUNDED_REFUSAL
    res = VerifierAgent.verify(
        answer="Create a DataBinder bean and call addValidators() on it.",
        evidence=[{"content": "DataBinder binds request parameters to a target object."}],
        retry_count=2,  # Retries exhausted
        max_retries=2
    )
    assert res.status == "REFUSE"
    assert res.cleaned_answer == UNGROUNDED_REFUSAL


def test_verifier_retries_uncited_answer_while_retries_remain():
    res = VerifierAgent.verify(
        answer="Create a DataBinder bean and call addValidators() on it.",
        evidence=[{"content": "DataBinder binds request parameters to a target object."}],
        retry_count=0,
        max_retries=2
    )
    assert res.status == "RETRY"
    assert res.retry_reason == "citation_format"


def test_verifier_refuses_out_of_range_citations_after_retries_exhausted():
    res = VerifierAgent.verify(
        answer="HNSW uses iterative scanning [99].",
        evidence=[{"content": "HNSW index docs"}],
        retry_count=2,
        max_retries=2
    )
    assert res.status == "REFUSE"


def test_verifier_keeps_partial_answer_with_grounded_citation_after_retries_exhausted():
    res = VerifierAgent.verify(
        answer="The pgvector HNSW index speeds up search [1]. Set --fake-unsupported-flag=1 [1].",
        evidence=[{"content": "The pgvector HNSW index speeds up approximate nearest neighbour search."}],
        retry_count=2,
        max_retries=2
    )
    assert res.status == "PARTIAL"
    assert "[1]" in res.cleaned_answer
    assert "--fake-unsupported-flag" not in res.cleaned_answer


def test_workflow_refuses_when_graph_stops_before_verification(monkeypatch):
    """If the graph ends while the verifier still wants a retry (step ceiling or LLM-call
    budget hit), the unverified draft must not be returned as the answer."""
    from app.agents import graph
    from app.agents.verifier import UNGROUNDED_REFUSAL

    async def fake_ainvoke(state):
        return {
            **state,
            "answer_draft": "Invented, uncited DataBinder configuration.",
            "verification_status": "RETRY",
            "verification_feedback": "Answer missing bracketed citation tags [n].",
            "retry_count": 1,
        }

    monkeypatch.setattr(graph.v2_supervisor_graph, "ainvoke", fake_ainvoke)
    res = run_v2_agent_workflow("how to configure a databinder ?", conversation_id="test_stop_before_verify")
    assert res["verification_status"] == "REFUSE"
    assert res["answer"] == UNGROUNDED_REFUSAL
