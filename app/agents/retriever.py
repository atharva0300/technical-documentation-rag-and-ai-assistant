import json
import logging
from typing import List, Dict, Any, Optional
from app.mcp.client import mcp_client, CircuitBreakerOpenException

logger = logging.getLogger(__name__)


class RetrievalAgent:
    """
    Retrieval Agent responsible for executing documentation search
    for single queries or sub-questions via the MCP client.

    `tool_name` is a parameter, not a constant: this class has no hardcoded
    notion of "the" retrieval tool being `search_documentation` specifically.
    It calls whatever tool name it's given (resolved upstream by the planner,
    generically, against the MCP server's live tool manifest -- see
    `app.agents.planner.resolve_default_tool` and `PlannerAgent.plan`), so the
    same code path works unchanged for any tool the server exposes, now or
    in the future. If no tool is resolved (e.g. the MCP server currently
    advertises nothing that accepts a free-text query), this returns no
    results rather than guessing a name.

    Note: an earlier version of this gated results behind a `MIN_RERANK_SCORE`
    threshold and looped up to `MAX_RETRIEVER_ITERATIONS` times, broadening the query
    and doubling top_k when the score was too low. That threshold was never calibrated
    against this corpus's real score distribution (it defaulted to -10.0, below which
    the reranker/RRF scores never actually fall), so the loop always returned on its
    first pass — it was dead weight pretending to be an active feature. Removed in
    favor of a single, honest retrieval call; recalibrate and reintroduce only with
    real score data backing the threshold.
    """

    @classmethod
    async def retrieve_subquestion_async(
        cls,
        query: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if not tool_name:
            logger.info(f"No tool resolved for '{query}' — skipping tool call (not a hardcoded no-op: the planner found no matching tool).")
            return []

        call_args = dict(tool_args or {})
        call_args.setdefault("query", query)
        call_args.setdefault("top_k", top_k)
        if filters:
            call_args.setdefault("filters", filters)

        try:
            res = await mcp_client.call_tool_async(tool_name, call_args)
        except CircuitBreakerOpenException as e:
            logger.warning(f"Retrieval skipped for '{query}': {e}")
            return []

        results = []
        if res.get("status") == "success":
            raw_result = res.get("result")
            if isinstance(raw_result, list):
                results = raw_result
            elif isinstance(raw_result, str):
                try:
                    results = json.loads(raw_result)
                except Exception:
                    results = []
        elif res.get("status") == "error":
            logger.warning(f"Retrieval error for '{query}': {res.get('error')}")

        for doc in results:
            if isinstance(doc, dict):
                doc["sub_question"] = query

        return results
