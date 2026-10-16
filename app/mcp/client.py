import json
import logging
import asyncio
import os
import threading
import concurrent.futures
import contextvars
from typing import Dict, Any, Optional
from mcp.client import Client

from app.config import settings

logger = logging.getLogger(__name__)

# Tool-call count is a genuine per-request budget, so it stays request-scoped.
_mcp_tool_call_count_var: contextvars.ContextVar[int] = contextvars.ContextVar("mcp_tool_call_count_var", default=0)

# Circuit breaker state is intentionally module-level (process-wide), not a ContextVar:
# its entire purpose is to protect the shared MCP server process ACROSS requests. A
# per-request ContextVar would reset on every call and could never actually trip.
_circuit_lock = threading.Lock()
_consecutive_failures = 0
_circuit_open = False


class CircuitBreakerOpenException(Exception):
    """Raised when circuit breaker trips due to repeated tool failures."""
    pass


class MCPClient:
    """
    MCP Client connecting over real MCP HTTP transport with timeouts,
    output caps, error validation, per-request invocation accounting, and a
    process-wide circuit breaker.
    """

    def __init__(
        self,
        server_url: Optional[str] = None,
        timeout_seconds: float = 30.0,
        max_output_chars: int = 10000,
        max_failures: int = 3
    ):
        self.server_url = server_url or os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8765/mcp")
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars
        self.max_failures = max_failures

    @property
    def consecutive_failures(self) -> int:
        with _circuit_lock:
            return _consecutive_failures

    @property
    def circuit_open(self) -> bool:
        with _circuit_lock:
            return _circuit_open

    def _record_success(self):
        global _consecutive_failures
        with _circuit_lock:
            _consecutive_failures = 0

    def _record_failure(self) -> int:
        global _consecutive_failures, _circuit_open
        with _circuit_lock:
            _consecutive_failures += 1
            if _consecutive_failures >= self.max_failures:
                _circuit_open = True
            return _consecutive_failures

    @property
    def total_tool_calls(self) -> int:
        return _mcp_tool_call_count_var.get()

    @total_tool_calls.setter
    def total_tool_calls(self, value: int):
        _mcp_tool_call_count_var.set(value)

    def reset_tool_call_count(self):
        _mcp_tool_call_count_var.set(0)

    async def call_tool_async(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if self.circuit_open:
            raise CircuitBreakerOpenException("MCP Circuit Breaker is OPEN due to previous consecutive tool failures.")

        current_calls = _mcp_tool_call_count_var.get()
        if current_calls >= settings.MAX_TOOL_CALLS:
            return {
                "status": "error",
                "error": f"MCP Tool Call Budget Exhausted: {current_calls} calls made (Max limit: {settings.MAX_TOOL_CALLS})"
            }

        _mcp_tool_call_count_var.set(current_calls + 1)

        try:
            async def _execute():
                import httpx
                from mcp.client.streamable_http import streamable_http_client
                headers = {
                    "Authorization": f"Bearer {settings.MCP_API_KEY}",
                    "X-MCP-API-Key": settings.MCP_API_KEY
                }
                async with httpx.AsyncClient(headers=headers) as http_cl:
                    async with Client(streamable_http_client(self.server_url, http_client=http_cl)) as client:
                        res = await client.call_tool(tool_name, arguments)
                        return res

            res = await asyncio.wait_for(_execute(), timeout=self.timeout_seconds)

            if getattr(res, "is_error", False):
                raise RuntimeError(f"Tool '{tool_name}' execution error: {res}")

            raw_result = ""
            if getattr(res, "content", None):
                raw_result = "".join([getattr(c, "text", str(c)) for c in res.content])

            self._record_success()

            if isinstance(raw_result, str) and len(raw_result) > self.max_output_chars:
                raw_result = raw_result[:self.max_output_chars] + "... [TRUNCATED]"

            try:
                parsed = json.loads(raw_result)
                return {"status": "success", "result": parsed}
            except Exception:
                return {"status": "success", "result": raw_result}

        except Exception as e:
            failures = self._record_failure()
            if failures >= self.max_failures:
                logger.error(f"MCP Circuit breaker tripped after {failures} failures.")

            error_msg = f"MCP tool execution failed: {str(e)}"
            if "not found" in str(e).lower() or "unknown tool" in str(e).lower():
                error_msg = f"Tool '{tool_name}' not found in MCP tool registry."

            return {
                "status": "error",
                "error": error_msg
            }

    def reset_circuit_breaker(self):
        global _consecutive_failures, _circuit_open
        with _circuit_lock:
            _circuit_open = False
            _consecutive_failures = 0


mcp_client = MCPClient()
