import logging
import asyncio
from typing import Dict, Any, List, Optional
from langgraph.graph import StateGraph, END
from app.agents.state import V2AgentState
from app.agents.planner import PlannerAgent
from app.agents.retriever import RetrievalAgent
from app.agents.analysis import EvidenceMerger
from app.agents.answerer import AnswererAgent
from app.agents.verifier import VerifierAgent
from app.agents.memory import MemoryLoader
from app.mcp.client import mcp_client
from app.core.llm_gateway import llm_gateway
from app.config import settings

logger = logging.getLogger(__name__)

def load_memory_node(state: V2AgentState) -> Dict[str, Any]:
    history = state.get("history") or []
    if not history and state.get("conversation_id"):
        history = MemoryLoader.load_history(state["conversation_id"])

    original_query = state["query"]
    rewritten = MemoryLoader.rewrite_query(original_query, history)

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
            "sub_questions": plan.sub_questions,
            "needs_retrieval": plan.needs_retrieval,
            "needs_tools": plan.needs_tools,
            "selected_tool": plan.selected_tool,
            "tool_args": plan.tool_args
        }]
    }

async def mcp_executor_node(state: V2AgentState) -> Dict[str, Any]:
    query = state.get("rewritten_query") or state["query"]
    tool_count = state.get("tool_call_count", 0)
    selected_tool = state.get("selected_tool")
    tool_args = state.get("tool_args")
    needs_tools = state.get("needs_tools", False)

    mcp_evidence = []
    executed_calls = 0
    error_msg = None

    if needs_tools:
        if not selected_tool:
            error_msg = "Controlled tool-selection error: selected_tool missing when tools required."
            logger.warning(error_msg)
        elif selected_tool not in PlannerAgent.ALLOWED_TOOLS:
            error_msg = f"Controlled authorization error: tool '{selected_tool}' is not allowlisted."
            logger.warning(error_msg)
        else:
            if tool_count < settings.MAX_TOOL_CALLS:
                try:
                    args = tool_args or {"query": query}
                    res = await mcp_client.call_tool_async(selected_tool, args)
                    executed_calls += 1
                    if res.get("status") == "success" and res.get("result"):
                        mcp_evidence.append({
                            "source": f"mcp:{selected_tool}",
                            "content": str(res["result"]),
                            "similarity": 1.0,
                            "metadata": args
                        })
                    elif res.get("status") == "error":
                        error_msg = f"MCP tool '{selected_tool}' returned error: {res.get('error')}"
                except Exception as e:
                    error_msg = f"MCP executor tool call error for {selected_tool}: {e}"
                    logger.warning(error_msg)

    return {
        "evidence": mcp_evidence,
        "tool_call_count": mcp_client.total_tool_calls,
        "llm_call_count": llm_gateway.llm_call_count,
        "step_count": state.get("step_count", 0) + 1,
        "error": error_msg,
        "trace_events": [{
            "node": "mcp_executor",
            "executed_calls": executed_calls,
            "evidence_retrieved": len(mcp_evidence),
            "selected_tool": selected_tool,
            "error": error_msg
        }]
    }

async def retrieve_node(state: V2AgentState) -> Dict[str, Any]:
    sub_questions = state.get("sub_questions") or [state.get("rewritten_query") or state["query"]]
    sub_questions = sub_questions[:settings.MAX_SUB_QUESTIONS]
    filters = state.get("filters")
    retry_count = state.get("retry_count", 0)

    # `evidence` is a plain (non-accumulating) state field — see app/agents/state.py.
    # mcp_executor_node only ever runs once, before the first retrieval pass, so on that
    # first pass we fold in whatever it already gathered. On a retry, evidence starts from
    # a clean slate instead of piling new results onto stale ones from the rejected attempt
    # (which also shifted citation numbering between attempts — see the audit's Mistake #2).
    carried_evidence = (state.get("evidence") or []) if retry_count == 0 else []

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

    all_evidence = list(carried_evidence)
    for sq in sub_questions:
        results = await RetrievalAgent.retrieve_subquestion_async(query=sq, top_k=5, filters=filters)
        all_evidence.extend(results)

    return {
        "evidence": all_evidence,
        "tool_call_count": mcp_client.total_tool_calls,
        "llm_call_count": llm_gateway.llm_call_count,
        "step_count": state.get("step_count", 0) + 1,
        "trace_events": [{
            "node": "retriever",
            "retry_count": retry_count,
            "sub_questions_count": len(sub_questions),
            "evidence_retrieved": len(all_evidence)
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
def route_after_planner(state: V2AgentState) -> str:
    intent = state.get("intent")
    if intent in ("chitchat", "out_of_scope"):
        return "answer_node"
    if state.get("needs_tools"):
        return "mcp_executor_node"
    if state.get("needs_retrieval", True):
        return "retrieve_node"
    return "answer_node"

def route_after_mcp_executor(state: V2AgentState) -> str:
    if state.get("needs_retrieval", True):
        return "retrieve_node"
    return "evidence_merger_node"

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
        # the claims — that's what re-retrieving is actually for.
        return "retrieve_node"

    return END

# Build LangGraph StateGraph
builder = StateGraph(V2AgentState)

builder.add_node("load_memory_node", load_memory_node)
builder.add_node("plan_node", plan_node)
builder.add_node("mcp_executor_node", mcp_executor_node)
builder.add_node("retrieve_node", retrieve_node)
builder.add_node("evidence_merger_node", evidence_merger_node)
builder.add_node("answer_node", answer_node)
builder.add_node("verify_node", verify_node)

builder.set_entry_point("load_memory_node")

builder.add_edge("load_memory_node", "plan_node")

builder.add_conditional_edges(
    "plan_node",
    route_after_planner,
    {
        "mcp_executor_node": "mcp_executor_node",
        "retrieve_node": "retrieve_node",
        "answer_node": "answer_node"
    }
)

builder.add_conditional_edges(
    "mcp_executor_node",
    route_after_mcp_executor,
    {
        "retrieve_node": "retrieve_node",
        "evidence_merger_node": "evidence_merger_node"
    }
)

builder.add_edge("retrieve_node", "evidence_merger_node")
builder.add_edge("evidence_merger_node", "answer_node")
builder.add_edge("answer_node", "verify_node")

builder.add_conditional_edges(
    "verify_node",
    route_after_verifier,
    {
        "retrieve_node": "retrieve_node",
        "answer_node": "answer_node",
        END: END
    }
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
    return {
        "answer": final_state.get("answer_draft", ""),
        "sources": final_state.get("citations", []),
        "intent": final_state.get("intent", "single_lookup"),
        "verification_status": final_state.get("verification_status", "VALID"),
        "verification_feedback": final_state.get("verification_feedback"),
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
