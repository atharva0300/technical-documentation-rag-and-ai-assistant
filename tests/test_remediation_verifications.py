import pytest
import uuid
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

from app.config import settings
from app.api.v2.routes import verify_api_key, AgentQueryRequest
from app.agents.planner import PlannerAgent, PlanOutput
from app.agents.graph import _invoke_selected_tool, plan_node
from app.agents.analysis import EvidenceMerger
from app.agents.state import V2AgentState
from app.core.llm_gateway import llm_gateway, LLMBudgetExhaustedError
from app.mcp.client import MCPClient
from app.retrieval.bm25 import search_bm25
from app.ingestion.chunker import TokenAwareChunker
from mcp_server.server import AuthMiddleware

SEARCH_DOC_TOOL = {
    "name": "search_documentation",
    "description": "Search indexed technical documentation using hybrid retrieval.",
    "input_schema": {"properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}},
}

def test_verify_api_key_strict_rejection():
    """Verify that verify_api_key rejects missing, empty, or invalid API keys with 401 HTTP exception."""
    with pytest.raises(HTTPException) as exc_missing:
        verify_api_key(None)
    assert exc_missing.value.status_code == 401

    with pytest.raises(HTTPException) as exc_empty:
        verify_api_key("")
    assert exc_empty.value.status_code == 401

    with pytest.raises(HTTPException) as exc_invalid:
        verify_api_key("wrong-key")
    assert exc_invalid.value.status_code == 401

    valid = verify_api_key(settings.API_KEY)
    assert valid == settings.API_KEY

def test_planner_resolves_fallback_tool_from_live_manifest():
    """
    The planner never hardcodes a tool name: the tool it records (used only for
    GraphRAG fallbacks and the agent's deterministic fallback) is resolved from the
    live manifest's declared schema. Choosing tools for a question is the
    tool-calling agent's job (see tests/agents/test_tool_agent.py).
    """
    plan = PlannerAgent.plan("explain the --memory flag in detail", available_tools=[SEARCH_DOC_TOOL])
    assert plan.selected_tool == "search_documentation"
    assert plan.needs_retrieval is True


def test_planner_records_no_tool_when_manifest_has_none_matching():
    plan = PlannerAgent.plan("explain the --memory flag in detail", available_tools=[{"name": "list_documents", "input_schema": {"properties": {}}}])
    assert plan.selected_tool is None


@pytest.mark.asyncio
async def test_invoke_selected_tool_executes_selected_tool_generically():
    """
    The graph-search fallback path must call whatever tool the planner resolved, via
    the generic RetrievalAgent path — no tool name is hardcoded in the graph.
    """
    state: V2AgentState = {
        "conversation_id": "test_conv",
        "query": "explain --memory flag",
        "rewritten_query": None,
        "history": [],
        "intent": "single_lookup",
        "retrieval_strategy": "point_lookup",
        "needs_retrieval": True,
        "needs_tools": True,
        "selected_tool": "search_documentation",
        "tool_args": {"query": "explain --memory flag"},
        "sub_questions": ["explain --memory flag"],
        "filters": None,
        "evidence": [],
        "answer_draft": None,
        "citations": [],
        "verification_status": None,
        "verification_feedback": None,
        "unsupported_sentences": [],
        "step_count": 0,
        "llm_call_count": 0,
        "tool_call_count": 0,
        "retry_count": 0,
        "trace_events": [],
        "error": None
    }

    with patch("app.agents.retriever.mcp_client.call_tool_async", return_value={"status": "success", "result": "[]"}) as mock_call:
        res = await _invoke_selected_tool(state)
        mock_call.assert_called_once_with("search_documentation", {"query": "explain --memory flag", "top_k": 5})
        assert res == []


@pytest.mark.asyncio
async def test_invoke_selected_tool_preserves_cli_hyphens_on_retry():
    """Verify retry broadening preserves exact CLI hyphens (--memory, --max-old-space-size) during retry broadening."""
    state: V2AgentState = {
        "conversation_id": "test_cli",
        "query": "--memory limit",
        "rewritten_query": None,
        "history": [],
        "intent": "single_lookup",
        "retrieval_strategy": "point_lookup",
        "needs_retrieval": True,
        "needs_tools": True,
        "selected_tool": "search_documentation",
        "tool_args": None,
        "sub_questions": ["--memory limit", "--max-old-space-size"],
        "filters": None,
        "evidence": [],
        "answer_draft": None,
        "citations": [],
        "verification_status": "RETRY",
        "verification_feedback": "Missing CLI details",
        "unsupported_sentences": [],
        "step_count": 1,
        "llm_call_count": 1,
        "tool_call_count": 1,
        "retry_count": 1,
        "trace_events": [],
        "error": None
    }

    with patch("app.agents.graph.RetrievalAgent.retrieve_subquestion_async", return_value=[]) as mock_retrieve:
        await _invoke_selected_tool(state)
        called_queries = [call.kwargs["query"] for call in mock_retrieve.call_args_list]
        for q in called_queries:
            assert q.startswith("--memory") or q.startswith("--max-old-space-size")

def test_llm_gateway_budget_exhaustion():
    """Verify llm_gateway raises LLMBudgetExhaustedError when exceeding MAX_LLM_CALLS."""
    llm_gateway.reset_call_count()
    llm_gateway.llm_call_count = settings.MAX_LLM_CALLS

    with pytest.raises(LLMBudgetExhaustedError):
        llm_gateway.generate("test query")
    llm_gateway.reset_call_count()

@pytest.mark.asyncio
async def test_mcp_client_budget_exhaustion():
    """Verify MCPClient enforces MAX_TOOL_CALLS ceiling."""
    client = MCPClient()
    client.total_tool_calls = settings.MAX_TOOL_CALLS
    res = await client.call_tool_async("search_documentation", {"query": "test"})
    assert res["status"] == "error"
    assert "Budget Exhausted" in res["error"]

def test_evidence_merger_reranker_score_ordering():
    """Verify EvidenceMerger preserves reranker_score sorting order."""
    evidence = [
        {"content": "chunk 1", "reranker_score": 0.25, "source": "doc1"},
        {"content": "chunk 2", "reranker_score": 0.95, "source": "doc2"},
        {"content": "chunk 3", "reranker_score": 0.50, "source": "doc3"}
    ]
    merged = EvidenceMerger.merge_evidence(evidence, max_evidence=3)
    assert merged[0]["content"] == "chunk 2"
    assert merged[1]["content"] == "chunk 3"
    assert merged[2]["content"] == "chunk 1"

def test_token_aware_chunker_max_token_bound():
    """Verify TokenAwareChunker guarantees token count <= 240 tokens per chunk."""
    chunker = TokenAwareChunker(max_tokens=240, overlap_tokens=30)
    long_text = "Kubernetes deployment configmap persistentvolume secret container pod \n" * 100
    chunks = chunker.chunk_text(long_text)
    
    for c in chunks:
        content = c["content"]
        if chunker.tokenizer:
            tok_count = len(chunker.tokenizer.encode(content))
            assert tok_count <= 240

@pytest.mark.asyncio
async def test_invoke_selected_tool_makes_no_call_when_no_tool_resolved():
    """
    When no tool was resolved from the live manifest, the fallback retrieval path
    makes no call at all — it never guesses a tool name.
    """
    state_invalid: V2AgentState = {
        "conversation_id": "test_inv",
        "query": "test query",
        "rewritten_query": None,
        "history": [],
        "intent": "single_lookup",
        "retrieval_strategy": "point_lookup",
        "needs_retrieval": True,
        "needs_tools": False,
        "selected_tool": None,
        "tool_args": {},
        "sub_questions": ["test query"],
        "filters": None,
        "evidence": [],
        "answer_draft": None,
        "citations": [],
        "verification_status": None,
        "verification_feedback": None,
        "unsupported_sentences": [],
        "step_count": 0,
        "llm_call_count": 0,
        "tool_call_count": 0,
        "retry_count": 0,
        "trace_events": [],
        "error": None,
        "stream_handler": None
    }

    with patch("app.agents.retriever.mcp_client.call_tool_async") as mock_call:
        res = await _invoke_selected_tool(state_invalid)
        mock_call.assert_not_called()
        assert res == []

@pytest.mark.asyncio
async def test_concurrency_budget_isolation():
    """Verify concurrent requests have isolated ContextVar budgets."""
    import asyncio
    
    async def task_a():
        llm_gateway.reset_call_count()
        llm_gateway.llm_call_count = 5
        await asyncio.sleep(0.02)
        assert llm_gateway.llm_call_count == 5

    async def task_b():
        llm_gateway.reset_call_count()
        llm_gateway.llm_call_count = 1
        await asyncio.sleep(0.02)
        assert llm_gateway.llm_call_count == 1

    await asyncio.gather(task_a(), task_b())

def test_active_ingestion_pipeline_chunk_limit():
    """Verify the live ingestion pipeline's chunker uses configured MAX_CHUNK_SIZE (240)."""
    chunker = TokenAwareChunker()
    assert chunker.max_tokens == settings.MAX_CHUNK_SIZE
    assert chunker.overlap_tokens == settings.CHUNK_OVERLAP
