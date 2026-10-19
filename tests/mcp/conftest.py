import os
import subprocess
import time
import urllib.request
import pytest

@pytest.fixture(scope="module", autouse=True)
def mcp_server_process():
    """
    Module-scoped fixture to manage MCP server process lifecycle for tests.
    Ensures tests pass on fresh checkouts without requiring manual server startup.
    """
    server_url = "http://127.0.0.1:8765/mcp"
    
    # Check if server is already running and healthy
    already_running = False
    try:
        req = urllib.request.Request(server_url, method="POST", data=b'{"jsonrpc":"2.0","method":"ping","id":1}')
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=1) as resp:
            if resp.status in (200, 400, 405):
                already_running = True
    except Exception:
        already_running = False

    proc = None
    if not already_running:
        server_env = {**os.environ, "MCP_PORT": "8765", "PYTHONPATH": "."}
        proc = subprocess.Popen(
            [".venv/bin/python", "-m", "mcp_server.server"],
            env=server_env
        )
        # Wait up to 10 seconds for server healthcheck
        start = time.time()
        while time.time() - start < 10:
            try:
                req = urllib.request.Request(server_url, method="POST", data=b'{"jsonrpc":"2.0","method":"ping","id":1}')
                req.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(req, timeout=1) as resp:
                    break
            except Exception:
                time.sleep(0.5)

    yield server_url

    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
