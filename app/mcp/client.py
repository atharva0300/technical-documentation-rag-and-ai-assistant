import json
import logging
import asyncio
import os
import time
import threading
import contextvars
from typing import Dict, Any, List, Optional
from mcp.client import Client

from app.config import settings

logger = logging.getLogger(__name__)

# Tool-call count is a genuine per-request budget, so it stays request-scoped.
_mcp_tool_call_count_var: contextvars.ContextVar[int] = contextvars.ContextVar("mcp_tool_call_count_var", default=0)

# Circuit breaker state is intentionally module-level (process-wide), not a ContextVar:
# its entire purpose is to protect the shared MCP server process ACROSS requests. A
# per-request ContextVar would reset on every call and could never actually trip.
#
# State machine: CLOSED -> (max_failures consecutive failures) -> OPEN
#   -> (half_open_cooldown_seconds elapse) -> HALF_OPEN (exactly one trial call allowed)
#   -> success -> CLOSED  |  failure -> OPEN again (cooldown restarts)
# Before this, the breaker only had CLOSED/OPEN with no way back to CLOSED short of a
# manual reset_circuit_breaker() call that nothing in the codebase ever invoked — a
# transient MCP server blip meant permanently disabled retrieval until a process restart.
_circuit_lock = threading.Lock()
_consecutive_failures = 0
_circuit_open = False
_circuit_opened_at: Optional[float] = None
_half_open_trial_active = False

# Tool manifest cache: populated via the real MCP `list_tools` call, not hardcoded.
# Shared process-wide (like the circuit breaker) so every planner/executor call site
# reads the same, cheaply-refreshed view of "what tools actually exist right now" instead
# of each call hitting the server. A sync accessor (get_cached_tools) lets callers that
# can't await (e.g. the planner's deterministic fast-paths) still consult live data.
_tool_manifest_lock = threading.Lock()
_tool_manifest_cache: List[Dict[str, Any]] = []
_tool_manifest_fetched_at: float = 0.0
TOOL_MANIFEST_TTL_SECONDS = 60.0


class CircuitBreakerOpenException(Exception):
    """Raised when circuit breaker trips due to repeated tool failures."""
    pass


class MCPClient:
    """
    MCP Client connecting over real MCP HTTP transport with timeouts,
    output caps, error validation, per-request invocation accounting, a
    process-wide circuit breaker with automatic half-open recovery, and
    live tool discovery (no tool name is ever hardcoded here or in callers —
    `call_tool_async` takes whatever tool name/arguments it's given, and
    `list_tools_async`/`get_cached_tools` report whatever the MCP server
    currently advertises).
    """

    def __init__(
        self,
        server_url: Optional[str] = None,
        timeout_seconds: float = 30.0,
        max_output_chars: int = 10000,
        max_failures: int = 3,
        half_open_cooldown_seconds: float = 30.0,
    ):
        self.server_url = server_url or os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8765/mcp")
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars
        self.max_failures = max_failures
        self.half_open_cooldown_seconds = half_open_cooldown_seconds

    @property
    def consecutive_failures(self) -> int:
        with _circuit_lock:
            return _consecutive_failures

    @property
    def circuit_open(self) -> bool:
        with _circuit_lock:
            return _circuit_open

    def _record_success(self):
        global _consecutive_failures, _circuit_open, _circuit_opened_at, _half_open_trial_active
        with _circuit_lock:
            _consecutive_failures = 0
            _circuit_open = False
            _circuit_opened_at = None
            _half_open_trial_active = False

    def _record_failure(self) -> int:
        global _consecutive_failures, _circuit_open, _circuit_opened_at, _half_open_trial_active
        with _circuit_lock:
            _consecutive_failures += 1
            _half_open_trial_active = False
            if _consecutive_failures >= self.max_failures:
                _circuit_open = True
                _circuit_opened_at = time.monotonic()
            return _consecutive_failures

    def _acquire_call_permission(self) -> bool:
        """
        Returns True if this call may proceed. False means the breaker is OPEN and
        still cooling down, so the caller should fail fast without one more failure
        resetting the cooldown clock. This is the half-open gate: once the cooldown
        has elapsed, exactly one in-flight call is let through as a trial (guarded by
        `_half_open_trial_active`) while every other concurrent caller still fails fast
        until that trial resolves — otherwise a burst of concurrent requests would all
        hit the still-failing dependency at once the instant the cooldown expires.
        """
        global _half_open_trial_active
        with _circuit_lock:
            if not _circuit_open:
                return True

            if _circuit_opened_at is None:
                return True

            elapsed = time.monotonic() - _circuit_opened_at
            if elapsed < self.half_open_cooldown_seconds:
                return False

            if _half_open_trial_active:
                return False

            _half_open_trial_active = True
            return True

    @property
    def total_tool_calls(self) -> int:
        return _mcp_tool_call_count_var.get()

    @total_tool_calls.setter
    def total_tool_calls(self, value: int):
        _mcp_tool_call_count_var.set(value)

    def reset_tool_call_count(self):
        _mcp_tool_call_count_var.set(0)

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.MCP_API_KEY}",
            "X-MCP-API-Key": settings.MCP_API_KEY
        }

    async def list_tools_async(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Fetches the live tool manifest from the MCP server (real `list_tools` protocol
        call) and caches it for TOOL_MANIFEST_TTL_SECONDS. This is what makes tool
        selection elsewhere in the codebase generic: the planner and executor only ever
        see "whatever this returns," never a hardcoded tool name — adding a new tool to
        the MCP server (or renaming/removing the existing one) requires no change here.
        Returns each tool as {"name", "description", "input_schema"}.
        On failure, logs and returns whatever is currently cached (possibly empty) rather
        than raising, since tool discovery failing shouldn't crash the calling graph node.
        """
        global _tool_manifest_cache, _tool_manifest_fetched_at

        now = time.monotonic()
        with _tool_manifest_lock:
            if not force_refresh and _tool_manifest_cache and (now - _tool_manifest_fetched_at) < TOOL_MANIFEST_TTL_SECONDS:
                return list(_tool_manifest_cache)

        try:
            import httpx
            from mcp.client.streamable_http import streamable_http_client

            async def _execute():
                async with httpx.AsyncClient(headers=self._auth_headers()) as http_cl:
                    async with Client(streamable_http_client(self.server_url, http_client=http_cl)) as client:
                        return await client.list_tools()

            raw_tools = await asyncio.wait_for(_execute(), timeout=self.timeout_seconds)

            tools: List[Dict[str, Any]] = []
            for t in raw_tools:
                schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or {}
                tools.append({
                    "name": getattr(t, "name", None),
                    "description": getattr(t, "description", "") or "",
                    "input_schema": schema,
                })

            with _tool_manifest_lock:
                _tool_manifest_cache = tools
                _tool_manifest_fetched_at = time.monotonic()

            return list(tools)
        except Exception as e:
            logger.warning(f"MCP list_tools failed, serving cached/stale tool manifest: {e}")
            with _tool_manifest_lock:
                return list(_tool_manifest_cache)

    def get_cached_tools(self) -> List[Dict[str, Any]]:
        """
        Synchronous, non-blocking read of the tool manifest cache — no network call.
        Used by code paths that can't await (the planner's deterministic fast-paths).
        Returns [] if `list_tools_async` has never successfully populated the cache yet
        (e.g. very first request after a cold start, before anything has warmed it).
        """
        with _tool_manifest_lock:
            return list(_tool_manifest_cache)

    async def call_tool_async(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not self._acquire_call_permission():
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
                async with httpx.AsyncClient(headers=self._auth_headers()) as http_cl:
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
        global _consecutive_failures, _circuit_open, _circuit_opened_at, _half_open_trial_active
        with _circuit_lock:
            _circuit_open = False
            _consecutive_failures = 0
            _circuit_opened_at = None
            _half_open_trial_active = False


def tool_accepts_field(tool: Dict[str, Any], field_name: str) -> bool:
    """
    Generic structural check against a tool's own declared MCP input schema —
    "does this tool accept a parameter called `field_name`" — used to pick a
    tool by its declared contract rather than by matching its name. This is how
    fast-path code can resolve "which tool handles a free-text query" without
    hardcoding a tool's name anywhere: it asks the live schema, not a string.
    """
    schema = tool.get("input_schema") or {}
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    return field_name in properties


mcp_client = MCPClient()
