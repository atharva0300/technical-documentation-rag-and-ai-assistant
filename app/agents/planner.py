from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel, Field
import re
import logging
from app.mcp.client import mcp_client, tool_accepts_field
from app.config import settings

logger = logging.getLogger(__name__)


class PlanOutput(BaseModel):
    intent: Literal["single_lookup", "multi_hop", "comparison", "chitchat", "out_of_scope"] = "single_lookup"
    # How retrieval should be carried out for this query -- a separate axis
    # from `intent`. "point_lookup" is the common case (plain top-k hybrid
    # retrieval); "exhaustive" and "relational" route to GraphRAG-lite's
    # global/local search (see app/graph/) instead of plain chunk ranking,
    # because widening top_k or re-weighting RRF can't give a single-chunk
    # retrieval step a notion of "coverage across the whole corpus."
    retrieval_strategy: Literal["point_lookup", "exhaustive", "relational"] = "point_lookup"
    needs_retrieval: bool = True
    needs_tools: bool = False
    selected_tool: Optional[str] = None
    tool_args: Optional[dict] = None
    sub_questions: List[str] = Field(default_factory=list)
    # The semantic router's confident category (None when it wasn't confident).
    router_category: Optional[str] = None
    reasoning: Optional[str] = None


def resolve_default_tool(available_tools: List[Dict[str, Any]]) -> Optional[str]:
    """
    Generic, structural resolution of "which tool handles a free-text
    query" -- the first tool in the LIVE manifest whose declared MCP input
    schema accepts a `query` field. This is what lets the deterministic
    fast-paths below need retrieval without ever naming a tool: they ask the
    tool's own declared contract, not a hardcoded string. If a second
    retrieval-shaped tool is ever added to the MCP server, this still works
    without a code change here -- the ambiguous case (more than one
    candidate) falls through to the LLM path, which gets the full manifest
    and picks explicitly.
    """
    if not available_tools:
        # Live discovery has nothing to go on yet (MCP server unreachable, or
        # this is the very first request before anything warmed the tool
        # cache) -- degrade to the configured fallback rather than silently
        # skipping retrieval. See settings.DEFAULT_RETRIEVAL_TOOL_FALLBACK.
        return settings.DEFAULT_RETRIEVAL_TOOL_FALLBACK or None

    candidates = [t.get("name") for t in available_tools if t.get("name") and tool_accepts_field(t, "query")]
    if len(candidates) == 1:
        return candidates[0]
    # Several tools take a free-text query (e.g. a general search and a code-only search):
    # use the configured general-purpose one if it's among them.
    if settings.DEFAULT_RETRIEVAL_TOOL_FALLBACK in candidates:
        return settings.DEFAULT_RETRIEVAL_TOOL_FALLBACK
    # Zero or multiple matches among a REAL, non-empty manifest is a genuine
    # "don't guess" case -- unlike the empty-manifest case above, we actually
    # know what tools exist and none of them is an unambiguous choice.
    return None


class PlannerAgent:
    """
    Cheap first pass over a query: short-circuits the cases that obviously need no
    documentation (greetings, clearly off-topic requests). Everything else goes to the
    tool-calling agent (app/agents/tool_agent.py), where the model itself decides which
    MCP tools to call (search, code search, sections, table of contents, GraphRAG-lite
    related/survey tools) and with what arguments.

    Classification uses the semantic router (`app.retrieval.semantic_router`):
    embedding similarity against example utterances per category, not keyword lists.
    The router never selects a tool. Its confident retrieval verdict is passed on only
    as `router_category`, which lets the agent fall back to a plain search if the model
    makes no valid tool call for a question that clearly needs the docs.
    """

    @classmethod
    def plan(
        cls,
        query: str,
        history: Optional[List[dict]] = None,
        available_tools: Optional[List[Dict[str, Any]]] = None,
    ) -> PlanOutput:
        clean_query = query.strip()

        if available_tools is None:
            available_tools = mcp_client.get_cached_tools()
        # Only used for the GraphRAG strategies' fallback retrieval and the agent's
        # deterministic fallback; the agent picks tools from the live manifest itself.
        default_tool = resolve_default_tool(available_tools)

        category, score = None, 0.0
        try:
            from app.retrieval.semantic_router import route_query
            category, score = route_query(clean_query)
        except Exception as e:
            logger.warning(f"Semantic router unavailable ({e}); using minimal degraded-mode fallback.")
            # Narrow safety net for when the embedding model itself can't be loaded, so
            # bare greetings still don't trigger a retrieval round.
            if clean_query.lower() in {"hi", "hello", "hey", "good morning", "good evening", "who are you"}:
                category, score = "chitchat", 1.0

        if category == "chitchat":
            return PlanOutput(
                intent="chitchat",
                needs_retrieval=False,
                needs_tools=False,
                sub_questions=[],
                router_category=category,
                reasoning=f"Semantic router matched 'chitchat' (similarity={score:.2f})."
            )

        if category == "out_of_scope":
            return PlanOutput(
                intent="out_of_scope",
                needs_retrieval=False,
                needs_tools=False,
                sub_questions=[],
                router_category=category,
                reasoning=f"Semantic router matched 'out_of_scope' (similarity={score:.2f})."
            )

        intent, strategy, sub_q = "single_lookup", "point_lookup", [clean_query]
        if category == "comparison":
            parts = re.split(r"\bvs\.?\b|\bcompare\b|\bdifference between\b|\bversus\b", clean_query, flags=re.IGNORECASE)
            parts = [p.strip() for p in parts if len(p.strip()) > 2][:2]
            if len(parts) >= 2:
                intent, sub_q = "comparison", parts
        elif category == "multi_hop":
            parts = [p.strip() for p in re.split(r"\band\b", clean_query, flags=re.IGNORECASE) if len(p.strip()) > 3][:3]
            if len(parts) >= 2:
                intent, sub_q = "multi_hop", parts

        if category is None:
            reasoning = (f"Semantic router not confident (best similarity={score:.2f}); "
                         "the tool-calling agent decides what to retrieve.")
        else:
            reasoning = f"Semantic router matched '{category}' (similarity={score:.2f})."

        return PlanOutput(
            intent=intent,
            retrieval_strategy=strategy,
            needs_retrieval=True,
            needs_tools=bool(default_tool),
            selected_tool=default_tool,
            tool_args={"query": clean_query} if default_tool else None,
            sub_questions=sub_q,
            router_category=category,
            reasoning=reasoning
        )
