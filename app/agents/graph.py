import logging
import asyncio
from typing import Dict, Any, List, Optional
from langgraph.graph import StateGraph, END
from app.agents.state import V2AgentState
from app.agents.planner import PlannerAgent
from app.agents.analysis import EvidenceMerger
from app.agents.answerer import AnswererAgent
from app.agents.verifier import VerifierAgent, UNGROUNDED_REFUSAL
from app.agents.memory import MemoryLoader
from app.agents.tool_agent import ToolCallingAgent
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
        "router_category": plan.router_category,
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

# Router categories that mean "this clearly needs the documentation". Used only to decide
# whether the tool-calling agent falls back to a plain search when the model makes no
# valid tool call -- never to choose a tool.
_RETRIEVAL_CATEGORIES = {"point_lookup", "comparison", "multi_hop", "exhaustive", "relational"}


async def agent_retrieval_node(state: V2AgentState) -> Dict[str, Any]:
    """
    Model-driven retrieval: the LLM chooses which MCP tools to call, with its own
    arguments, over a bounded loop (see app/agents/tool_agent.py). Replaces the
    planner-selected single tool call for the common "point_lookup" path, and is
    re-entered on a verifier RETRY for ungrounded content, with the verifier's
    feedback, so the model can search differently.
    """
    retry_count = state.get("retry_count", 0)
    question = state.get("rewritten_query") or state["query"]
    feedback = state.get("verification_feedback") if retry_count > 0 else None

    result = await ToolCallingAgent.gather_evidence(
        question=question,
        retrieval_expected=state.get("router_category") in _RETRIEVAL_CATEGORIES,
        feedback=feedback,
    )

    return {
        "evidence": result.evidence,
        "tool_call_count": mcp_client.total_tool_calls,
        "llm_call_count": llm_gateway.llm_call_count,
        "step_count": state.get("step_count", 0) + 1,
        "error": result.fallback_reason if result.used_fallback else None,
        "trace_events": [{
            "node": "agent_retrieval",
            "evidence_retrieved": len(result.evidence),
            "used_fallback": result.used_fallback,
            "fallback_reason": result.fallback_reason,
            "steps": result.trace,
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

    # The standalone question (references to earlier turns resolved by the memory step),
    # so the answer addresses what was actually asked in context.
    query_text = state.get("rewritten_query") or state["query"]
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
    """All retrieval goes through the model-driven tool loop; GraphRAG-lite search is one
    of the tools the model can choose (find_related, survey_topic)."""
    return "agent_retrieval_node"

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
builder.add_node("agent_retrieval_node", agent_retrieval_node)
builder.add_node("evidence_merger_node", evidence_merger_node)
builder.add_node("answer_node", answer_node)
builder.add_node("verify_node", verify_node)

builder.set_entry_point("load_memory_node")

builder.add_edge("load_memory_node", "plan_node")

_RETRIEVAL_ROUTE_MAP = {
    "agent_retrieval_node": "agent_retrieval_node",
    "answer_node": "answer_node",
}

builder.add_conditional_edges("plan_node", route_after_planner, _RETRIEVAL_ROUTE_MAP)

builder.add_edge("agent_retrieval_node", "evidence_merger_node")
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
        "router_category": None,
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
