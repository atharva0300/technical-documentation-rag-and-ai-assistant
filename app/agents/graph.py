import logging
import asyncio
from typing import Dict, Any, List, Optional
from langgraph.graph import StateGraph, END
from app.agents.state import V2AgentState
from app.agents.planner import PlannerAgent
from app.agents.retriever import RetrievalAgent
from app.agents.analysis import EvidenceMerger
from app.agents.answerer import AnswererAgent
from app.agents.verifier import VerifierAgent, UNGROUNDED_REFUSAL
from app.agents.memory import MemoryLoader
from app.mcp.client import mcp_client
from app.core.llm_gateway import llm_gateway
from app.config import settings

logger = logging.getLogger(__name__)

async def load_memory_node(state: V2AgentState) -> Dict[str, Any]:
    history = state.get("history") or []
    if not history and state.get("conversation_id"):
        history = MemoryLoader.load_history(state["conversation_id"])

    original_query = state["query"]
    rewritten = MemoryLoader.rewrite_query(original_query, history)

    # Opportunistically refresh the live MCP tool manifest (cheap: no-ops if
    # still within TOOL_MANIFEST_TTL_SECONDS of the last fetch). This is what
    # keeps `PlannerAgent.plan()`'s sync `mcp_client.get_cached_tools()` read
    # actually live, without plan_node itself needing to be async or await a
    # network call on every single query.
    try:
        await mcp_client.list_tools_async()
    except Exception as e:
        logger.warning(f"Tool manifest refresh failed (using whatever is cached): {e}")

    return {
        "history": history,
        "rewritten_query": rewritten,
        "step_count": state.get("step_count", 0) + 1,
        "llm_call_count": llm_gateway.llm_call_count,
        "tool_call_count": mcp_client.total_tool_calls,
        "trace_events": [{"node": "memory_loader", "status": "ok", "rewritten": rewritten}]
    }

def plan_node(state: V2AgentState) -> Dict[str, Any]:
    query_to_plan = state.get("rewritten_query") or state["query"]
    plan = PlannerAgent.plan(query_to_plan, state.get("history"))

    return {
        "intent": plan.intent,
        "retrieval_strategy": plan.retrieval_strategy,
        "needs_retrieval": plan.needs_retrieval,
        "needs_tools": plan.needs_tools,
        "selected_tool": plan.selected_tool,
        "tool_args": plan.tool_args,
        "sub_questions": plan.sub_questions,
        "step_count": state.get("step_count", 0) + 1,
        "llm_call_count": llm_gateway.llm_call_count,
        "tool_call_count": mcp_client.total_tool_calls,
        "trace_events": [{
            "node": "planner",
            "intent": plan.intent,
            "retrieval_strategy": plan.retrieval_strategy,
            "sub_questions": plan.sub_questions,
            "needs_retrieval": plan.needs_retrieval,
            "needs_tools": plan.needs_tools,
            "selected_tool": plan.selected_tool,
            "tool_args": plan.tool_args
        }]
    }

async def _invoke_selected_tool(state: V2AgentState) -> List[Dict[str, Any]]:
    """
    Shared retrieval helper: calls whatever tool the planner selected
    (`state["selected_tool"]`, resolved generically against the live MCP
    tool manifest -- see app.agents.planner) once per sub-question. This is
    the one place tool invocation actually happens, used by the plain
    point-lookup path (`invoke_tools_node`) AND as the fallback for
    local/global graph search when the knowledge graph has nothing useful
    for a given query -- so there's exactly one code path that calls an MCP
    tool, and it never hardcodes which one.
    """
    sub_questions = state.get("sub_questions") or [state.get("rewritten_query") or state["query"]]
    sub_questions = sub_questions[:settings.MAX_SUB_QUESTIONS]
    filters = state.get("filters")
    retry_count = state.get("retry_count", 0)
    selected_tool = state.get("selected_tool")
    tool_args = state.get("tool_args")

    if not selected_tool:
        logger.info("No tool resolved for retrieval — the live MCP manifest had no matching tool.")
        return []

    # On retry: broaden query while preserving exact CLI hyphens (--memory, --max-old-space-size) and adding verifier feedback
    if retry_count > 0:
        feedback = state.get("verification_feedback", "")
        broadened = []
        for sq in sub_questions:
            stripped = sq.strip()
            if feedback:
                hint = feedback.split(".")[0][:60] if "." in feedback else feedback[:60]
                stripped = f"{stripped} {hint}"
            broadened.append(stripped)
        sub_questions = broadened

    all_evidence = []
    for sq in sub_questions:
        results = await RetrievalAgent.retrieve_subquestion_async(
            query=sq, top_k=5, filters=filters, tool_name=selected_tool, tool_args=tool_args
        )
        all_evidence.extend(results)
    return all_evidence

async def invoke_tools_node(state: V2AgentState) -> Dict[str, Any]:
    """
    Generic tool-invocation node for the "point_lookup" retrieval strategy
    (the common case). Replaces the old `mcp_executor_node` + `retrieve_node`
    pair, which had two independent paths to the same tool: one gated by the
    planner's `needs_tools` decision, and one (`RetrievalAgent`) that called
    `search_documentation` unconditionally regardless of what the planner
    decided -- that unconditional call was the actual deterministic-calling
    bug. Now there is exactly one call path (`_invoke_selected_tool`), and it
    only runs when `needs_retrieval` is true and the planner (or its
    structural fast-path resolution) actually selected a tool.
    """
    retry_count = state.get("retry_count", 0)
    needs_retrieval = state.get("needs_retrieval", True)

    # `evidence` is a plain (non-accumulating) state field — see app/agents/state.py.
    # On a verifier retry, evidence starts from a clean slate instead of piling new
    # results onto stale ones from the rejected attempt (which also shifted citation
    # numbering between attempts — see the audit's Mistake #2).
    carried_evidence = (state.get("evidence") or []) if retry_count == 0 else []

    new_evidence = []
    error_msg = None
    if needs_retrieval:
        new_evidence = await _invoke_selected_tool(state)
        if not state.get("selected_tool"):
            error_msg = "Retrieval was needed but no MCP tool currently matches (live tool manifest had no query-accepting tool)."

    all_evidence = list(carried_evidence) + new_evidence

    return {
        "evidence": all_evidence,
        "tool_call_count": mcp_client.total_tool_calls,
        "llm_call_count": llm_gateway.llm_call_count,
        "step_count": state.get("step_count", 0) + 1,
        "error": error_msg,
        "trace_events": [{
            "node": "invoke_tools",
            "selected_tool": state.get("selected_tool"),
            "evidence_retrieved": len(all_evidence),
            "error": error_msg
        }]
    }

async def local_search_node(state: V2AgentState) -> Dict[str, Any]:
    """
    GraphRAG-lite local search for "relational" queries (what depends on X,
    what's affected by X) — entity-anchored graph traversal fused with
    ordinary dense+BM25 retrieval via RRF (see app/graph/local_search.py).
    Falls back to plain tool-based retrieval if the graph has nothing
    relevant for this query (e.g. no entity match, or the graph hasn't been
    built yet for this corpus).
    """
    from app.database.connection import SessionLocal
    from app.graph.local_search import local_search as graph_local_search

    query = state.get("rewritten_query") or state["query"]
    filters = state.get("filters")

    results = []
    db = SessionLocal()
    try:
        results = graph_local_search(db, query, top_k=5, filters=filters)
    except Exception as e:
        logger.warning(f"Local graph search failed, falling back to tool retrieval: {e}")
    finally:
        db.close()

    fallback_used = False
    if not results:
        results = await _invoke_selected_tool(state)
        fallback_used = True

    return {
        "evidence": results,
        "step_count": state.get("step_count", 0) + 1,
        "llm_call_count": llm_gateway.llm_call_count,
        "tool_call_count": mcp_client.total_tool_calls,
        "trace_events": [{"node": "local_search", "fallback_used": fallback_used, "evidence_retrieved": len(results)}]
    }

async def global_search_node(state: V2AgentState) -> Dict[str, Any]:
    """
    GraphRAG-lite global search for "exhaustive" queries (find every place X
    is mentioned, scattered across the corpus) — map-reduce over pre-built
    community summaries (see app/graph/global_search.py) instead of ranking
    individual chunks, which has no notion of whole-corpus coverage no
    matter how `top_k` is tuned. Falls back to plain tool-based retrieval if
    no knowledge graph has been built for this corpus yet, or nothing
    relevant was found in it.
    """
    from app.database.connection import SessionLocal
    from app.graph.global_search import answer_global

    query = state.get("rewritten_query") or state["query"]

    result = {"citations": [], "graph_available": False}
    db = SessionLocal()
    try:
        result = answer_global(db, query)
    except Exception as e:
        logger.warning(f"Global graph search failed, falling back to tool retrieval: {e}")
    finally:
        db.close()

    fallback_used = False
    if not result.get("graph_available") or not result.get("citations"):
        evidence = await _invoke_selected_tool(state)
        fallback_used = True
    else:
        evidence = [
            {
                "chunk_id": f"community_{c['citation_id']}",
                "source": c["source"],
                "content": c["content"],
                "similarity": 1.0,
            }
            for c in result["citations"]
        ]

    return {
        "evidence": evidence,
        "step_count": state.get("step_count", 0) + 1,
        "llm_call_count": llm_gateway.llm_call_count,
        "tool_call_count": mcp_client.total_tool_calls,
        "trace_events": [{
            "node": "global_search",
            "fallback_used": fallback_used,
            "communities_used": result.get("communities_used", 0),
            "evidence_retrieved": len(evidence)
        }]
    }

def evidence_merger_node(state: V2AgentState) -> Dict[str, Any]:
    raw_evidence = state.get("evidence") or []
    merged = EvidenceMerger.merge_evidence(raw_evidence, max_evidence=10)

    return {
        "citations": merged,
        "step_count": state.get("step_count", 0) + 1,
        "llm_call_count": llm_gateway.llm_call_count,
        "tool_call_count": mcp_client.total_tool_calls,
        "trace_events": [{"node": "evidence_merger", "merged_count": len(merged)}]
    }

async def answer_node(state: V2AgentState) -> Dict[str, Any]:
    intent = state.get("intent", "single_lookup")
    stream_handler = state.get("stream_handler")
    retry_count = state.get("retry_count", 0)

    if intent == "chitchat":
        msg = "Hello! I am your Engineering AI Assistant. How can I help you with your technical documentation today?"
        if stream_handler:
            for word in msg.split(" "):
                res = stream_handler(word + " ")
                if asyncio.iscoroutine(res):
                    await res
        return {
            "answer_draft": msg,
            "step_count": state.get("step_count", 0) + 1,
            "llm_call_count": llm_gateway.llm_call_count,
            "tool_call_count": mcp_client.total_tool_calls,
            "trace_events": [{"node": "answerer", "status": "chitchat"}]
        }

    if intent == "out_of_scope":
        msg = "I am an engineering assistant specialized in technical documentation. That request is outside my domain."
        if stream_handler:
            for word in msg.split(" "):
                res = stream_handler(word + " ")
                if asyncio.iscoroutine(res):
                    await res
        return {
            "answer_draft": msg,
            "step_count": state.get("step_count", 0) + 1,
            "llm_call_count": llm_gateway.llm_call_count,
            "tool_call_count": mcp_client.total_tool_calls,
            "trace_events": [{"node": "answerer", "status": "out_of_scope"}]
        }

    # Raw query is primary semantic source of truth for response generation
    query_text = state["query"]
    evidence = state.get("citations") or state.get("evidence") or []

    # Only the FIRST attempt streams live. A verifier RETRY regenerates (same evidence, on a
    # citation-format failure) or re-retrieves-then-regenerates (on an ungrounded-content
    # failure) server-side without pushing tokens to the client: the client already rendered
    # the first (rejected) attempt's tokens, and a second live stream through the same
    # channel would just read as that answer getting garbled/duplicated with no way to tell
    # the two apart. The SSE route (app/api/v2/routes.py) instead emits a single
    # `answer_replaced` event with the final, verified text once this resolves.
    use_live_stream = bool(stream_handler) and retry_count == 0

    if use_live_stream:
        token_chunks = []
        async for token in AnswererAgent.generate_answer_stream(query=query_text, evidence=evidence):
            token_chunks.append(token)
            res = stream_handler(token)
            if asyncio.iscoroutine(res):
                await res
        answer_text = "".join(token_chunks)
    else:
        answer_text = AnswererAgent.generate_answer(query=query_text, evidence=evidence)

    return {
        "answer_draft": answer_text,
        "llm_call_count": llm_gateway.llm_call_count,
        "tool_call_count": mcp_client.total_tool_calls,
        "step_count": state.get("step_count", 0) + 1,
        "trace_events": [{"node": "answerer", "status": "generated", "streamed_live": use_live_stream}]
    }

def verify_node(state: V2AgentState) -> Dict[str, Any]:
    intent = state.get("intent")
    if intent in ("chitchat", "out_of_scope"):
        return {
            "verification_status": "VALID",
            "verification_feedback": "Bypassed verification for direct non-retrieval intent.",
            "step_count": state.get("step_count", 0) + 1,
            "llm_call_count": llm_gateway.llm_call_count,
            "tool_call_count": mcp_client.total_tool_calls,
            "trace_events": [{"node": "verifier", "status": "VALID", "bypassed": True}]
        }

    answer_draft = state.get("answer_draft", "")
    evidence = state.get("citations") or state.get("evidence") or []
    retry_count = state.get("retry_count", 0)

    v_res = VerifierAgent.verify(
        answer=answer_draft,
        evidence=evidence,
        retry_count=retry_count,
        max_retries=settings.MAX_RETRIES
    )

    new_retry_count = retry_count + 1 if v_res.status == "RETRY" else retry_count
    return {
        "verification_status": v_res.status,
        "verification_feedback": v_res.feedback,
        "retry_reason": v_res.retry_reason,
        "unsupported_sentences": v_res.unsupported_sentences,
        "answer_draft": v_res.cleaned_answer,
        "retry_count": new_retry_count,
        "step_count": state.get("step_count", 0) + 1,
        "llm_call_count": llm_gateway.llm_call_count,
        "tool_call_count": mcp_client.total_tool_calls,
        "trace_events": [{
            "node": "verifier",
            "status": v_res.status,
            "retry_reason": v_res.retry_reason,
            "retry_count": new_retry_count,
            "feedback": v_res.feedback
        }]
    }

# Route Decisions
def _retrieval_node_for_strategy(state: V2AgentState) -> str:
    """
    Picks which retrieval node handles this query based on the planner's
    `retrieval_strategy` (point_lookup / exhaustive / relational — see
    app.agents.planner.PlanOutput), used both for the initial route after
    planning and for a verifier retry that needs another retrieval pass, so
    a retry re-runs the SAME strategy rather than always falling back to
    plain tool retrieval.
    """
    strategy = state.get("retrieval_strategy", "point_lookup")
    if strategy == "exhaustive":
        return "global_search_node"
    if strategy == "relational":
        return "local_search_node"
    return "invoke_tools_node"

def route_after_planner(state: V2AgentState) -> str:
    intent = state.get("intent")
    if intent in ("chitchat", "out_of_scope"):
        return "answer_node"
    if not state.get("needs_retrieval", True):
        return "answer_node"
    return _retrieval_node_for_strategy(state)

def route_after_verifier(state: V2AgentState) -> str:
    status = state.get("verification_status", "VALID")
    retry_count = state.get("retry_count", 0)
    step_count = state.get("step_count", 0)
    retry_reason = state.get("retry_reason")

    if status == "RETRY" and retry_count < settings.MAX_RETRIES:
        if step_count >= settings.MAX_AGENT_STEPS:
            logger.warning(f"Step ceiling reached ({step_count}/{settings.MAX_AGENT_STEPS}) during verifier retry.")
            return END
        # A citation-format failure (missing/out-of-range [n] tags) is a generation problem,
        # not a retrieval problem — the evidence was never the issue, so regenerate from the
        # SAME evidence instead of spending a retrieval round (and another LLM call) on it.
        if retry_reason == "citation_format":
            return "answer_node"
        # An ungrounded-content failure means the retrieved evidence itself didn't support
        # the claims — that's what re-retrieving (via the same strategy) is actually for.
        return _retrieval_node_for_strategy(state)

    return END

# Build LangGraph StateGraph
builder = StateGraph(V2AgentState)

builder.add_node("load_memory_node", load_memory_node)
builder.add_node("plan_node", plan_node)
builder.add_node("invoke_tools_node", invoke_tools_node)
builder.add_node("local_search_node", local_search_node)
builder.add_node("global_search_node", global_search_node)
builder.add_node("evidence_merger_node", evidence_merger_node)
builder.add_node("answer_node", answer_node)
builder.add_node("verify_node", verify_node)

builder.set_entry_point("load_memory_node")

builder.add_edge("load_memory_node", "plan_node")

_RETRIEVAL_ROUTE_MAP = {
    "invoke_tools_node": "invoke_tools_node",
    "local_search_node": "local_search_node",
    "global_search_node": "global_search_node",
    "answer_node": "answer_node",
}

builder.add_conditional_edges("plan_node", route_after_planner, _RETRIEVAL_ROUTE_MAP)

builder.add_edge("invoke_tools_node", "evidence_merger_node")
builder.add_edge("local_search_node", "evidence_merger_node")
builder.add_edge("global_search_node", "evidence_merger_node")
builder.add_edge("evidence_merger_node", "answer_node")
builder.add_edge("answer_node", "verify_node")

builder.add_conditional_edges(
    "verify_node",
    route_after_verifier,
    {**_RETRIEVAL_ROUTE_MAP, END: END}
)

v2_supervisor_graph = builder.compile()

async def run_v2_agent_workflow_async(
    query: str,
    conversation_id: str = "default",
    filters: Optional[Dict[str, Any]] = None,
    stream_callback: Optional[Any] = None
) -> Dict[str, Any]:
    initial_state: V2AgentState = {
        "conversation_id": conversation_id,
        "query": query,
        "rewritten_query": None,
        "history": [],
        "intent": None,
        "retrieval_strategy": "point_lookup",
        "needs_retrieval": True,
        "needs_tools": False,
        "selected_tool": None,
        "tool_args": None,
        "sub_questions": [],
        "filters": filters,
        "evidence": [],
        "answer_draft": None,
        "citations": [],
        "verification_status": None,
        "verification_feedback": None,
        "retry_reason": None,
        "unsupported_sentences": [],
        "step_count": 0,
        "llm_call_count": 0,
        "tool_call_count": 0,
        "retry_count": 0,
        "trace_events": [],
        "error": None,
        "stream_handler": stream_callback
    }

    mcp_client.reset_tool_call_count()
    llm_gateway.reset_call_count()

    final_state = await v2_supervisor_graph.ainvoke(initial_state)

    answer = final_state.get("answer_draft", "")
    verification_status = final_state.get("verification_status", "VALID")
    verification_feedback = final_state.get("verification_feedback")
    # The graph can stop while the verifier still wants a retry (step ceiling, or the LLM
    # call budget ran out). That draft was never verified, so refuse instead of returning it.
    if verification_status == "RETRY":
        answer = UNGROUNDED_REFUSAL
        verification_status = "REFUSE"
        verification_feedback = f"Stopped before the answer could be verified ({verification_feedback})"

    return {
        "answer": answer,
        "sources": final_state.get("citations", []),
        "intent": final_state.get("intent", "single_lookup"),
        "verification_status": verification_status,
        "verification_feedback": verification_feedback,
        "trace_events": final_state.get("trace_events", []),
        "step_count": final_state.get("step_count", 0),
        "llm_call_count": llm_gateway.llm_call_count,
        "tool_call_count": mcp_client.total_tool_calls,
        "retry_count": final_state.get("retry_count", 0),
        "error": final_state.get("error")
    }

def run_v2_agent_workflow(
    query: str,
    conversation_id: str = "default",
    filters: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Synchronous wrapper for callers outside an event loop (e.g. the v1-compat routes)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        raise RuntimeError(
            "run_v2_agent_workflow() is synchronous and cannot be called from inside a running "
            "event loop (e.g. an async FastAPI route) — await run_v2_agent_workflow_async() directly instead."
        )
    return asyncio.run(run_v2_agent_workflow_async(query, conversation_id, filters))

run_v2_agent_workflow_stream = run_v2_agent_workflow_async
