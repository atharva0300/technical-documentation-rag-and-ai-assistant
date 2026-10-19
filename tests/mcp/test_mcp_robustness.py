"""
MCP Client robustness tests: oversized output truncation, the process-wide circuit
breaker tripping and resetting. Requires a live MCP server (see tests/mcp/conftest.py).
"""
import pytest
from app.mcp.client import MCPClient, CircuitBreakerOpenException

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_mcp_invalid_tool_name():
    client = MCPClient()
    res = await client.call_tool_async("invalid_unknown_tool", {})
    assert res["status"] == "error"
    assert "not found" in res["error"]


@pytest.mark.asyncio
async def test_mcp_oversized_output_truncation():
    client = MCPClient(server_url="http://127.0.0.1:8765/mcp", max_output_chars=50)

    res = await client.call_tool_async("search_documentation", {"query": "Kubernetes", "top_k": 5})

    assert res["status"] == "success"
    assert len(str(res["result"])) <= 100
    assert "[TRUNCATED]" in str(res["result"])


@pytest.mark.asyncio
async def test_mcp_circuit_breaker_trips_and_resets():
    client = MCPClient(server_url="http://127.0.0.1:8765/mcp", max_failures=2)
    client.reset_circuit_breaker()

    # 1st failure
    res1 = await client.call_tool_async("nonexistent_tool", {})
    assert res1["status"] == "error"
    assert client.circuit_open is False

    # 2nd failure -> Circuit breaker trips
    res2 = await client.call_tool_async("nonexistent_tool", {})
    assert res2["status"] == "error"
    assert client.circuit_open is True

    # Subsequent calls fail fast with CircuitBreakerOpenException
    with pytest.raises(CircuitBreakerOpenException):
        await client.call_tool_async("search_documentation", {"query": "test"})

    # Reset circuit breaker
    client.reset_circuit_breaker()
    assert client.circuit_open is False
    res3 = await client.call_tool_async("search_documentation", {"query": "test"})
    assert res3["status"] == "success"
