import pytest
import uuid
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

from app.config import settings
from app.api.v2.routes import verify_api_key, AgentQueryRequest
from app.agents.planner import PlannerAgent, PlanOutput
from app.agents.graph import plan_node
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


def test_planner_prefers_configured_general_search_when_several_tools_take_a_query():
    code_tool = {"name": "search_code_examples", "input_schema": {"properties": {"query": {"type": "string"}}}}
    plan = PlannerAgent.plan("explain the --memory flag in detail", available_tools=[code_tool, SEARCH_DOC_TOOL])
    assert plan.selected_tool == settings.DEFAULT_RETRIEVAL_TOOL_FALLBACK == "search_documentation"


def test_planner_records_no_tool_when_manifest_has_none_matching():
    plan = PlannerAgent.plan("explain the --memory flag in detail", available_tools=[{"name": "list_documents", "input_schema": {"properties": {}}}])
    assert plan.selected_tool is None


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
