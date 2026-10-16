import json
import logging
from typing import List, Dict, Any, Optional
from app.mcp.client import mcp_client, CircuitBreakerOpenException

logger = logging.getLogger(__name__)


class RetrievalAgent:
    """
    Retrieval Agent responsible for executing documentation search
    for single queries or sub-questions via the MCP client.

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
    ) -> List[Dict[str, Any]]:
        tool_args = {
            "query": query,
            "top_k": top_k
        }
        if filters:
            tool_args["filters"] = filters

        try:
            res = await mcp_client.call_tool_async("search_documentation", tool_args)
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
