import pytest
import os
import json
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as StarletteTestClient

from mcp_server.server import mcp, AuthMiddleware
from app.config import settings

@pytest.fixture
def live_mcp_test_client():
    app = mcp.streamable_http_app(host="127.0.0.1")
    app.add_middleware(AuthMiddleware)
    with StarletteTestClient(app) as client:
        yield client

def test_live_mcp_unauthenticated_request_rejected(live_mcp_test_client):
    """Verify live HTTP request to MCP server without auth header returns HTTP 401 Unauthorized."""
    response = live_mcp_test_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 401
    assert "Unauthorized" in response.json().get("error", "")

def test_live_mcp_wrong_credential_rejected(live_mcp_test_client):
    """Verify live HTTP request with incorrect MCP API key returns HTTP 401 Unauthorized."""
    headers = {"X-MCP-API-Key": "invalid-secret-key"}
    response = live_mcp_test_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers)
    assert response.status_code == 401
    assert "Unauthorized" in response.json().get("error", "")

def test_live_mcp_valid_header_accepted(live_mcp_test_client):
    """Verify live HTTP request with correct X-MCP-API-Key header is accepted through AuthMiddleware."""
    headers = {"X-MCP-API-Key": settings.MCP_API_KEY}
    response = live_mcp_test_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers)
    assert response.status_code != 401

def test_live_mcp_valid_bearer_accepted(live_mcp_test_client):
    """Verify live HTTP request with correct Bearer authorization header is accepted through AuthMiddleware."""
    headers = {"Authorization": f"Bearer {settings.MCP_API_KEY}"}
    response = live_mcp_test_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers)
    assert response.status_code != 401
