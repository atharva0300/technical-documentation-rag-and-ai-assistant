import logging
import os

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    from mcp.server import Server as FastMCP

from mcp_server.tools.documentation import tool_search_documentation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_server")

mcp = FastMCP("TechnicalDocumentationServer")


@mcp.tool()
def search_documentation(query: str, top_k: int = 5, filters: dict = None) -> str:
    """Search indexed technical documentation using hybrid retrieval (dense + BM25 + RRF) and cross-encoder reranking."""
    return tool_search_documentation(query=query, top_k=top_k, filters=filters)


from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        expected_key = os.getenv("MCP_API_KEY", "mcp-secret-token")
        auth_header = request.headers.get("authorization", "")
        token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
        x_api_key = request.headers.get("x-mcp-api-key", "").strip()

        if request.url.path in ["/health", "/docs"]:
            return await call_next(request)

        if expected_key and (token == expected_key or x_api_key == expected_key):
            return await call_next(request)

        return JSONResponse({"error": "Unauthorized: Invalid or missing MCP API key"}, status_code=401)


def main():
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8765"))
    logger.info(f"Starting MCP Technical Documentation Server on {host}:{port}...")

    starlette_app = mcp.streamable_http_app(host=host)
    starlette_app.add_middleware(AuthMiddleware)

    @starlette_app.route("/health")
    async def health(request):
        return JSONResponse({"status": "ok"})

    import uvicorn
    import anyio
    config = uvicorn.Config(
        starlette_app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    anyio.run(server.serve)


if __name__ == "__main__":
    main()
