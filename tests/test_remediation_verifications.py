import pytest
import uuid
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

from app.config import settings
from app.api.v2.routes import verify_api_key, AgentQueryRequest
from app.agents.planner import PlannerAgent, PlanOutput
from app.agents.graph import invoke_tools_node, plan_node
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

def test_planner_selects_tool_dynamically_from_live_manifest():
    """
    The planner must never hardcode a tool name — it selects from whatever
    `available_tools` reports (normally the MCP server's live `list_tools`
    manifest, injected here for a deterministic test), and must reject
    anything the LLM proposes that isn't in that manifest.
    """
    def mock_llm_fn(user_query: str, system: str) -> str:
        import json
        # The tool manifest must be visible to the LLM via the system prompt.
        assert "search_documentation" in system
        return json.dumps({
            "intent": "single_lookup",
            "retrieval_strategy": "point_lookup",
            "needs_retrieval": True,
            "selected_tool": "search_documentation",
            "sub_questions": [user_query],
            "reasoning": "needs documentation lookup"
        })

    plan = PlannerAgent.plan("some ambiguous query", llm_fn=mock_llm_fn, available_tools=[SEARCH_DOC_TOOL])
    assert plan.selected_tool == "search_documentation"


def test_planner_rejects_tool_not_in_live_manifest():
    """A tool the LLM proposes that isn't in the live manifest must be nulled out, not trusted."""
    def mock_llm_fn(user_query: str, system: str) -> str:
        import json
        return json.dumps({
            "intent": "single_lookup",
            "retrieval_strategy": "point_lookup",
            "needs_retrieval": True,
            "selected_tool": "unauthorized_tool_xyz",
            "sub_questions": [user_query],
            "reasoning": "hallucinated tool"
        })

    plan = PlannerAgent.plan("some ambiguous query", llm_fn=mock_llm_fn, available_tools=[SEARCH_DOC_TOOL])
    assert plan.selected_tool != "unauthorized_tool_xyz"


@pytest.mark.asyncio
async def test_invoke_tools_node_executes_selected_tool_generically():
    """
    invoke_tools_node must call whatever tool the planner selected, via the
    generic RetrievalAgent path — no tool name is hardcoded in the graph
    node itself. needs_retrieval is the single gate now (needs_tools is
    informational only), unlike the old mcp_executor_node which had a second,
    independent gate that `retrieve_node` bypassed entirely.
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
        res = await invoke_tools_node(state)
        mock_call.assert_called_once_with("search_documentation", {"query": "explain --memory flag", "top_k": 5})
        assert res["error"] is None


@pytest.mark.asyncio
async def test_invoke_tools_node_preserves_cli_hyphens_on_retry():
    """Verify invoke_tools_node preserves exact CLI hyphens (--memory, --max-old-space-size) during retry broadening."""
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
        await invoke_tools_node(state)
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
async def test_invoke_tools_node_records_error_when_no_tool_resolved():
    """
    Tool-name validation against the live manifest now happens entirely in
    the planner (see test_planner_rejects_tool_not_in_live_manifest) — by the
    time state reaches invoke_tools_node, `selected_tool` is already either
    a real tool or None. This verifies the execution-layer guarantee: when
    needs_retrieval is true but no tool was resolved, the node makes no call
    at all (never falls back to guessing a tool name) and surfaces an error.
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
        res = await invoke_tools_node(state_invalid)
        mock_call.assert_not_called()
        assert res.get("error") is not None
        assert len(res["evidence"]) == 0

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
