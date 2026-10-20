from typing import List, Literal, Optional, Callable, Dict, Any
from pydantic import BaseModel, Field
import json
import re
import logging
from app.core.llm_gateway import llm_gateway, OllamaUnavailableError
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
    # Zero or multiple matches among a REAL, non-empty manifest is a genuine
    # "don't guess" case -- unlike the empty-manifest case above, we actually
    # know what tools exist and none of them is an unambiguous choice.
    return None


class PlannerAgent:
    """
    Query planner: classifies intent/retrieval-strategy and selects an MCP
    tool when one is needed.

    Tool selection is entirely generic -- nothing in this class ever compares
    against a hardcoded tool name. `available_tools` (defaulting to whatever
    `mcp_client.get_cached_tools()` currently reports, live from the MCP
    server's own `list_tools`) is the only source of truth for which tools
    exist and what they accept; adding, renaming, or removing a tool on the
    MCP server requires no change here.

    Classification itself is split into two tiers:
      1. A semantic router (`app.retrieval.semantic_router`) over example
         utterances per category, via embedding similarity -- replaces what
         used to be keyword/regex lists (chitchat greetings, out-of-scope
         topics, "vs"/"compare" comparison detection, "and how does" multi-
         hop detection). A keyword list can only cover the phrasings someone
         thought to write down; cosine similarity against examples
         generalizes across paraphrase.
      2. An LLM fallback for whatever the router isn't confident about,
         which is handed the live tool manifest and asked to select a tool
         (if any) as part of its structured output -- this is what makes
         tool-calling probabilistic rather than the old unconditional
         "always call search_documentation" behavior in `RetrievalAgent`.
    """

    @classmethod
    def plan(
        cls,
        query: str,
        history: Optional[List[dict]] = None,
        llm_fn: Optional[Callable[[str, str], str]] = None,
        available_tools: Optional[List[Dict[str, Any]]] = None,
    ) -> PlanOutput:
        clean_query = query.strip()

        if available_tools is None:
            available_tools = mcp_client.get_cached_tools()
        tool_names = {t.get("name") for t in available_tools if t.get("name")}
        default_tool = resolve_default_tool(available_tools)

        # 1. Semantic router -- see module docstring. Not a keyword list.
        category, score = None, 0.0
        try:
            from app.retrieval.semantic_router import route_query
            category, score = route_query(clean_query)
        except Exception as e:
            logger.warning(f"Semantic router unavailable ({e}); using minimal degraded-mode fallback.")
            # This is NOT the classifier -- it's a narrow safety net for the
            # case where the embedding infrastructure itself can't be reached
            # at all (offline/unit-test environment), so greetings don't get
            # misrouted all the way to an LLM call that's also likely
            # unavailable in that same environment. The real classification
            # logic is the router above and the LLM fallback below; this
            # exact-match check only exists to degrade gracefully, not to
            # generalize across phrasings (it deliberately doesn't try to).
            if clean_query.lower() in {"hi", "hello", "hey", "good morning", "good evening", "who are you"}:
                category, score = "chitchat", 1.0

        if category == "chitchat":
            return PlanOutput(
                intent="chitchat",
                needs_retrieval=False,
                needs_tools=False,
                sub_questions=[],
                reasoning=f"Semantic router matched 'chitchat' (similarity={score:.2f})."
            )

        if category == "out_of_scope":
            return PlanOutput(
                intent="out_of_scope",
                needs_retrieval=False,
                needs_tools=False,
                sub_questions=[],
                reasoning=f"Semantic router matched 'out_of_scope' (similarity={score:.2f})."
            )

        if category in ("comparison", "multi_hop", "exhaustive", "relational", "point_lookup"):
            if category == "comparison":
                parts = re.split(r"\bvs\.?\b|\bcompare\b|\bdifference between\b|\bversus\b", clean_query, flags=re.IGNORECASE)
                sub_q = [p.strip() for p in parts if len(p.strip()) > 2][:2]
                intent, strategy = "comparison", "point_lookup"
            elif category == "multi_hop":
                parts = [p.strip() for p in re.split(r"\band\b", clean_query, flags=re.IGNORECASE) if len(p.strip()) > 3]
                sub_q = parts[:3]
                intent, strategy = "multi_hop", "point_lookup"
            else:
                sub_q, intent, strategy = [clean_query], "single_lookup", category

            if len(sub_q) < 2 and intent in ("comparison", "multi_hop"):
                sub_q = [clean_query]

            return PlanOutput(
                intent=intent,
                retrieval_strategy=strategy,
                needs_retrieval=True,
                needs_tools=bool(default_tool),
                selected_tool=default_tool,
                tool_args={"query": clean_query} if default_tool else None,
                sub_questions=sub_q,
                reasoning=f"Semantic router matched '{category}' (similarity={score:.2f})."
            )

        # 2. Ambiguous -- router wasn't confident about any category. Fall
        # through to the LLM, handed the SAME live tool manifest so tool
        # selection is dynamic/probabilistic here too, not hardcoded.
        tool_manifest_desc = "\n".join(
            f"- {t.get('name')}: {t.get('description', '')}" for t in available_tools if t.get('name')
        ) or "(no tools currently available)"

        system_prompt = (
            "You are a query classification and planning assistant for a technical RAG system.\n"
            f"Available tools you may select from (choose at most one, or none):\n{tool_manifest_desc}\n\n"
            "Analyze the user query and output a JSON object matching this schema:\n"
            "{\n"
            '  "intent": "single_lookup" | "multi_hop" | "comparison" | "chitchat" | "out_of_scope",\n'
            '  "retrieval_strategy": "point_lookup" | "exhaustive" | "relational",\n'
            '  "needs_retrieval": boolean,\n'
            '  "selected_tool": string or null (must be one of the available tool names above, or null),\n'
            '  "sub_questions": [string],\n'
            '  "reasoning": string\n'
            "}\n"
            "Use \"exhaustive\" if the query asks for every occurrence/all instances of something "
            "scattered across the corpus. Use \"relational\" if it asks what depends on/affects/relates "
            "to something. Otherwise use \"point_lookup\".\n"
            "Do not include markdown codeblocks or extra prose. Output pure JSON only."
        )

        try:
            if llm_fn is not None:
                llm_response = llm_fn(clean_query, system_prompt)
            else:
                llm_response = llm_gateway.generate(clean_query, system=system_prompt)

            cleaned_json = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", llm_response, flags=re.DOTALL).strip()
            data = json.loads(cleaned_json)

            tool = data.get("selected_tool")
            if tool and tool not in tool_names:
                logger.warning(f"Planner LLM selected unknown tool '{tool}'; ignoring (not in live manifest).")
                tool = None
            needs_retrieval = data.get("needs_retrieval", True)
            if not tool and needs_retrieval:
                tool = default_tool

            return PlanOutput(
                intent=data.get("intent", "single_lookup"),
                retrieval_strategy=data.get("retrieval_strategy", "point_lookup"),
                needs_retrieval=needs_retrieval,
                needs_tools=bool(tool),
                selected_tool=tool,
                tool_args={"query": clean_query} if tool else None,
                sub_questions=data.get("sub_questions", [clean_query]),
                reasoning=f"LLM Classification: {data.get('reasoning', 'Structured classification')}"
            )
        except Exception as e:
            logger.warning(f"LLM Planner classification failed/unavailable: {e}. Falling back to single_lookup.")
            return PlanOutput(
                intent="single_lookup",
                retrieval_strategy="point_lookup",
                needs_retrieval=True,
                needs_tools=bool(default_tool),
                selected_tool=default_tool,
                tool_args={"query": clean_query} if default_tool else None,
                sub_questions=[clean_query],
                reasoning=f"Fast-path heuristic fallback (LLM error: {str(e)})."
            )
