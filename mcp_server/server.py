import logging
import os
import threading
import time
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp_server.tools.documentation import tool_search_documentation

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("mcp_server")

mcp = MCPServer("TechnicalDocumentationServer")


class SearchFilters(BaseModel):
    """Exact filters applied before ranking (see app/retrieval/dense.py and bm25.py)."""
    model_config = ConfigDict(extra="forbid")

    document_id: str | None = Field(None, description="Only search this document (an id from list_documents).")
    source: str | None = Field(None, description="Only search the document at this source path.")
    section: str | None = Field(None, description="Only passages whose section heading contains this text, e.g. '9.8 Spring Validation'.")
    updated_after: str | None = Field(None, description="Only documents updated after this ISO date.")


@mcp.tool()
def search_documentation(
    query: Annotated[str, Field(description=(
        "A focused, self-contained search query with the key technical terms, e.g. "
        "'Spring declarative transaction management @Transactional example'."
    ))],
    top_k: Annotated[int, Field(description="How many passages to return (3-8).")] = 5,
    filters: Annotated[SearchFilters | None, Field(description=(
        "Optional exact filters. Leave empty unless the question names a specific "
        "document or section."
    ))] = None,
) -> str:
    """Search the indexed technical documentation (hybrid semantic + keyword search with
    reranking). Returns the most relevant passages, each with its section heading, page,
    document_id and chunk_id. Use for any question about what the documentation says."""
    return tool_search_documentation(
        query=query,
        top_k=top_k,
        filters=filters.model_dump(exclude_none=True) if filters else None,
    )


class AuthMiddleware:
    """Raw ASGI auth middleware; BaseHTTPMiddleware can break MCP streaming responses."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in {"/health", "/docs"}:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
        x_api_key = headers.get(b"x-mcp-api-key", b"").decode("latin-1").strip()
        expected_key = os.getenv("MCP_API_KEY", "mcp-secret-token")

        if expected_key and (token == expected_key or x_api_key == expected_key):
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            {"error": "Unauthorized: Invalid or missing MCP API key"},
            status_code=401,
        )
        await response(scope, receive, send)


def _warm_up_search() -> None:
    """Run one throwaway search so the embedding model, the cross-encoder reranker and
    the BM25 index are loaded before the first real request. Cold, that first search
    takes ~20s on CPU, long enough for the api's tool call to time out and answer the
    user's first question with no evidence."""
    started = time.monotonic()
    try:
        tool_search_documentation(query="warm up", top_k=1)
        logger.info("Search warm-up finished in %.1fs", time.monotonic() - started)
    except Exception:
        logger.exception("Search warm-up failed; the first real search will load models instead")


def main() -> None:
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8765"))
    logger.info("Starting MCP Technical Documentation Server on %s:%s", host, port)

    # In the background so /health and the server come up immediately.
    threading.Thread(target=_warm_up_search, name="search-warm-up", daemon=True).start()

    app = mcp.streamable_http_app(host=host)
    app.add_middleware(AuthMiddleware)

    async def health(request):
        return JSONResponse({"status": "ok"})

    app.add_route("/health", health, methods=["GET"])

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("MCP server process failed")
        raise
