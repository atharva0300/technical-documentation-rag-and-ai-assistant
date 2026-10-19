import pytest
from unittest.mock import patch, AsyncMock
from app.agents.retriever import RetrievalAgent
from app.mcp.client import CircuitBreakerOpenException


@pytest.mark.asyncio
async def test_retrieve_subquestion_returns_results_on_success():
    mock_result = {
        "status": "success",
        "result": [{"chunk_id": 1, "title": "Doc A", "content": "some content"}]
    }
    with patch("app.agents.retriever.mcp_client.call_tool_async", new=AsyncMock(return_value=mock_result)):
        results = await RetrievalAgent.retrieve_subquestion_async("what is X?", top_k=5)
    assert len(results) == 1
    assert results[0]["sub_question"] == "what is X?"


@pytest.mark.asyncio
async def test_retrieve_subquestion_returns_empty_on_tool_error():
    mock_result = {"status": "error", "error": "boom"}
    with patch("app.agents.retriever.mcp_client.call_tool_async", new=AsyncMock(return_value=mock_result)):
        results = await RetrievalAgent.retrieve_subquestion_async("what is X?", top_k=5)
    assert results == []


@pytest.mark.asyncio
async def test_retrieve_subquestion_handles_open_circuit_breaker_gracefully():
    """A tripped circuit breaker must not crash the calling graph node (Mistake #4 in the audit)."""
    with patch(
        "app.agents.retriever.mcp_client.call_tool_async",
        new=AsyncMock(side_effect=CircuitBreakerOpenException("breaker open")),
    ):
        results = await RetrievalAgent.retrieve_subquestion_async("what is X?", top_k=5)
    assert results == []
