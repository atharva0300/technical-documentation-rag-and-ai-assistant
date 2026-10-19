"""
MCP Client Integration Tests

Server process lifecycle is automatically managed via fixture in tests/mcp/conftest.py.
Tests will execute successfully on fresh checkouts without requiring manual server startup.

Execution Options:
- Run all MCP tests (auto-starts server process via fixture):
  PYTHONPATH=. .venv/bin/pytest tests/mcp/test_mcp_client.py -v
- Exclude integration tests requiring subprocess/live server:
  PYTHONPATH=. .venv/bin/pytest -m "not integration" -v
"""
import pytest
from app.mcp.client import MCPClient, CircuitBreakerOpenException

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_mcp_client_tool_execution():
    client = MCPClient(server_url="http://127.0.0.1:8765/mcp", timeout_seconds=30.0)
    res = await client.call_tool_async("search_documentation", {"query": "PostgreSQL memory", "top_k": 2})
    assert res["status"] == "success"
    assert isinstance(res["result"], (list, dict))


@pytest.mark.asyncio
async def test_mcp_client_invalid_tool():
    client = MCPClient(server_url="http://127.0.0.1:8765/mcp", timeout_seconds=30.0)
    res = await client.call_tool_async("nonexistent_tool", {})
    assert res["status"] == "error"
    assert "not found" in res["error"].lower()


@pytest.mark.asyncio
async def test_mcp_client_circuit_breaker():
    client = MCPClient(server_url="http://127.0.0.1:8765/mcp", max_failures=2)
    client.reset_circuit_breaker()

    # Tool call to nonexistent_tool fails and increments consecutive_failures
    res1 = await client.call_tool_async("nonexistent_tool", {})
    assert res1["status"] == "error"
    assert client.circuit_open is False

    res2 = await client.call_tool_async("nonexistent_tool", {})
    assert res2["status"] == "error"
    assert client.circuit_open is True

    with pytest.raises(CircuitBreakerOpenException):
        await client.call_tool_async("search_documentation", {"query": "test"})

    client.reset_circuit_breaker()
