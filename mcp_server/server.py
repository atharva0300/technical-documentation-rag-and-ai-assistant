import logging
import os
import threading
import time
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp_server.tools.documentation import tool_search_documentation, tool_search_code_examples
from mcp_server.tools.graph_tools import tool_find_related, tool_survey_topic
from mcp_server.tools.navigation import (
    tool_expand_passage,
    tool_get_section,
    tool_get_table_of_contents,
    tool_list_documents,
)

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
        "A focused, self-contained search query containing the key technical terms from the question."
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



@mcp.tool()
def search_code_examples(
    query: Annotated[str, Field(description=(
        "What the code should show, including the class, annotation or element names from the question."
    ))],
    language: Annotated[Literal["java", "xml"] | None, Field(description=(
        "Only return code in this language: 'java' (classes, annotations) or 'xml' "
        "(bean definitions, namespaces). Leave empty for either."
    ))] = None,
    top_k: Annotated[int, Field(description="How many passages to return (3-8).")] = 5,
) -> str:
    """Search only passages that contain code samples (Java or XML configuration). Use when
    the user asks for code, an example, a snippet, or configuration to copy."""
    return tool_search_code_examples(query=query, language=language, top_k=top_k)


@mcp.tool()
def get_section(
    section: Annotated[str, Field(description=(
        "The section heading, in the form '<number> <title>' as shown in a search result's "
        "section or the table of contents."
    ))],
    document_id: Annotated[str | None, Field(description=(
        "Only needed when several documents are indexed: the document_id from list_documents."
    ))] = None,
) -> str:
    """Return the full text of one section, from its heading to where the next section
    starts. Use when the question needs a whole section, or a search result from that
    section is cut off mid-explanation, mid-list or mid-code-sample."""
    return tool_get_section(section=section, document_id=document_id)


@mcp.tool()
def expand_passage(
    chunk_id: Annotated[int, Field(description="The numeric chunk_id of a passage from a search result.")],
    window: Annotated[int, Field(description="How many neighbouring passages to add on each side (0-5).")] = 2,
) -> str:
    """Return a passage together with the passages just before and after it, in reading
    order. Use when a search result looks cut off and you want its surrounding text."""
    return tool_expand_passage(chunk_id=chunk_id, window=window)


@mcp.tool()
def list_documents() -> str:
    """List the indexed documents with their document_id, passage count and last update.
    Use when the user asks what documentation is available, or to get a document_id."""
    return tool_list_documents()


@mcp.tool()
def get_table_of_contents(
    document_id: Annotated[str | None, Field(description=(
        "Only needed when several documents are indexed: the document_id from list_documents."
    ))] = None,
    under: Annotated[str | None, Field(description=(
        "Only headings under this chapter/section number (e.g. '9' for chapter 9) or "
        "containing this word. Leave empty for the whole document (long documents are truncated)."
    ))] = None,
) -> str:
    """Return a document's section headings with page numbers. Use when the user asks what a
    document or chapter covers, or which sections discuss a topic."""
    return tool_get_table_of_contents(document_id=document_id, under=under)


@mcp.tool()
def find_related(
    entity: Annotated[str, Field(description=(
        "The component, class or concept from the question to find connections for."
    ))],
    top_k: Annotated[int, Field(description="How many passages to return (3-8).")] = 5,
) -> str:
    """Find passages about what relates to, depends on, or is affected by a component, using
    the knowledge graph of entities that co-occur across the documentation (falls back to
    ordinary search when the graph has nothing for it). Use for 'what depends on X',
    'what uses X', 'how does X connect to Y'."""
    return tool_find_related(entity=entity, top_k=top_k)


@mcp.tool()
def survey_topic(
    topic: Annotated[str, Field(description="The topic from the question to survey across the whole corpus.")],
    max_communities: Annotated[int, Field(description="How many topic overviews to return (1-8).")] = 5,
) -> str:
    """Return corpus-wide overviews of a topic: summaries of the clusters of related passages
    across all documents. Use for 'everything about X', 'every place X is discussed', or
    'what are all the ...' questions whose answer is spread across many sections."""
    return tool_survey_topic(topic=topic, max_communities=max_communities)

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
