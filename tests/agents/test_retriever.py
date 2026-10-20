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
        results = await RetrievalAgent.retrieve_subquestion_async("what is X?", top_k=5, tool_name="search_documentation")
    assert len(results) == 1
    assert results[0]["sub_question"] == "what is X?"


@pytest.mark.asyncio
async def test_retrieve_subquestion_returns_empty_on_tool_error():
    mock_result = {"status": "error", "error": "boom"}
    with patch("app.agents.retriever.mcp_client.call_tool_async", new=AsyncMock(return_value=mock_result)):
        results = await RetrievalAgent.retrieve_subquestion_async("what is X?", top_k=5, tool_name="search_documentation")
    assert results == []


@pytest.mark.asyncio
async def test_retrieve_subquestion_handles_open_circuit_breaker_gracefully():
    """A tripped circuit breaker must not crash the calling graph node (Mistake #4 in the audit)."""
    with patch(
        "app.agents.retriever.mcp_client.call_tool_async",
        new=AsyncMock(side_effect=CircuitBreakerOpenException("breaker open")),
    ):
        results = await RetrievalAgent.retrieve_subquestion_async("what is X?", top_k=5, tool_name="search_documentation")
    assert results == []


@pytest.mark.asyncio
async def test_retrieve_subquestion_skips_call_when_no_tool_resolved():
    """
    No hardcoded tool name exists in RetrievalAgent any more -- if the planner
    resolved no tool (tool_name=None), this must not call the MCP client at
    all, since there's nothing generic to call it with.
    """
    with patch("app.agents.retriever.mcp_client.call_tool_async", new=AsyncMock()) as mock_call:
        results = await RetrievalAgent.retrieve_subquestion_async("what is X?", top_k=5, tool_name=None)
    mock_call.assert_not_called()
    assert results == []


@pytest.mark.asyncio
async def test_retrieve_subquestion_works_with_any_tool_name():
    """
    The whole point of the fix: this class has no special-cased knowledge of
    `search_documentation` -- it must work identically for any tool name the
    planner resolves from the live MCP manifest.
    """
    mock_result = {"status": "success", "result": [{"chunk_id": 7, "content": "future tool result"}]}
    with patch("app.agents.retriever.mcp_client.call_tool_async", new=AsyncMock(return_value=mock_result)) as mock_call:
        results = await RetrievalAgent.retrieve_subquestion_async("query future stuff", top_k=5, tool_name="some_future_tool")
    mock_call.assert_called_once()
    assert mock_call.call_args.args[0] == "some_future_tool"
    assert len(results) == 1
