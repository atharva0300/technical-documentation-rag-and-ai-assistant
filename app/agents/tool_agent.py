"""
Tool-calling retrieval agent: the model decides which MCP tools to call, with
what arguments, and when it has gathered enough evidence -- via Ollama's native
tool calling (`tools=[...]` on /api/chat), in a bounded loop.

Each round the model sees the question plus every tool result so far, and either
emits `tool_calls` (executed through the MCP client, results fed back as `tool`
messages) or stops. The final answer is NOT written here: the evidence gathered
goes through the existing merger -> answerer -> verifier path, so citations and
grounding checks work the same whichever tools the model chose.

Deterministic guardrails around the model's decisions:
  - tool names are checked against the live MCP manifest (allowlist);
  - arguments are validated against that tool's own MCP input schema, and an
    invalid call is answered with an error message the model can correct;
  - top_k is clamped to [AGENT_MIN_TOP_K, AGENT_MAX_TOP_K];
  - rounds are capped (AGENT_MAX_TOOL_ROUNDS) on top of the per-request LLM and
    MCP tool-call budgets and the MCP circuit breaker;
  - if the model can't be reached, or makes no valid call for a question the
    router confidently classified as needing documentation, one plain search with
    the question is run instead, so the user isn't left with nothing.
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import jsonschema

from app.config import settings
from app.core.llm_gateway import llm_gateway, OllamaUnavailableError, LLMBudgetExhaustedError
from app.mcp.client import mcp_client, to_ollama_tools, CircuitBreakerOpenException

logger = logging.getLogger(__name__)

AGENT_SYSTEM_PROMPT = """You are the research step of a technical documentation assistant.
Your job is to gather the documentation passages needed to answer the user's question by
calling the available tools. You do not write the final answer; another step does that
using what you find.

- Call the tool that best fits the question, with arguments you choose. Write search queries
  as focused, self-contained phrases with the key technical terms.
- Results include each passage's document_id and chunk_id, which other tools accept.
- Read the results. If they don't cover the question (for example, a comparison needs both
  concepts, or a result is cut off), call a tool again with a different query or a tool that
  fetches more context.
- When you have enough, or the question doesn't need the documentation at all, reply with a
  one-line note saying so and make no tool call."""

# How much of each result the model sees when deciding its next step. The full text is
# kept as evidence for the answerer; this only limits the decision prompt.
_RESULT_PREVIEW_CHARS = 500


@dataclass
class AgentResult:
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    trace: List[Dict[str, Any]] = field(default_factory=list)
    used_fallback: bool = False
    fallback_reason: Optional[str] = None


def _validate_call(
    call: Dict[str, Any], manifest_by_name: Dict[str, Dict[str, Any]]
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Returns (normalized_arguments, None) for a valid call, or (None, error_message)."""
    name = call.get("name")
    tool = manifest_by_name.get(name)
    if tool is None:
        return None, f"Unknown tool '{name}'. Available tools: {sorted(manifest_by_name)}."

    arguments = dict(call.get("arguments") or {})
    schema = tool.get("input_schema") or {}
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}

    # Models sometimes send optional arguments as explicit nulls or the wrong scalar
    # type for an integer; normalize those before validating.
    arguments = {k: v for k, v in arguments.items() if v is not None or k in schema.get("required", [])}
    for key, value in list(arguments.items()):
        if properties.get(key, {}).get("type") == "integer" and isinstance(value, str) and value.strip().isdigit():
            arguments[key] = int(value)

    unknown = sorted(set(arguments) - set(properties))
    if properties and unknown:
        # The MCP schema may not forbid extra keys, but the tool would silently ignore
        # them -- e.g. {"object": "DataBinder", "query": "configuration"} drops the subject.
        return None, (f"Invalid arguments for '{name}': unknown argument(s) {unknown}. "
                      f"Allowed arguments: {sorted(properties)}. Put all search terms in the "
                      "query text.")

    try:
        jsonschema.validate(arguments, schema)
    except jsonschema.ValidationError as e:
        return None, f"Invalid arguments for '{name}': {e.message}"

    if "top_k" in properties and "top_k" in arguments:
        arguments["top_k"] = max(settings.AGENT_MIN_TOP_K, min(settings.AGENT_MAX_TOP_K, int(arguments["top_k"])))

    return arguments, None


def _parse_tool_result(result: Any) -> List[Dict[str, Any]]:
    """MCP tools here return JSON: either a list of passages or an object with one."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict) and r.get("content")]
    if isinstance(result, dict):
        for key in ("results", "chunks", "passages"):
            if isinstance(result.get(key), list):
                return [r for r in result[key] if isinstance(r, dict) and r.get("content")]
    return []


def _render_for_model(result: Any, passages: List[Dict[str, Any]]) -> str:
    """What the model reads back as the tool message."""
    if passages:
        blocks = []
        for p in passages:
            label = " — ".join(x for x in (p.get("section"), p.get("title")) if x)
            content = (p.get("content") or "").strip()
            if len(content) > _RESULT_PREVIEW_CHARS:
                content = content[:_RESULT_PREVIEW_CHARS] + " …"
            ref = f"document_id={p.get('document_id')} chunk_id={p.get('chunk_id')}"
            blocks.append(f"[{label}] ({ref})\n{content}")
        return "\n\n".join(blocks)
    if isinstance(result, (dict, list)):
        # Non-passage results (e.g. a document list or table of contents) go back as-is.
        return json.dumps(result, default=str)[:4000]
    return str(result or "No results.")


async def _fallback_search(question: str, manifest: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One plain search with the question, using whichever live tool takes a free-text
    query (the same structural resolution the planner uses)."""
    from app.agents.planner import resolve_default_tool
    from app.agents.retriever import RetrievalAgent

    tool = resolve_default_tool(manifest)
    if not tool:
        return []
    return await RetrievalAgent.retrieve_subquestion_async(query=question, top_k=5, tool_name=tool)


class ToolCallingAgent:
    @classmethod
    async def gather_evidence(
        cls,
        question: str,
        retrieval_expected: bool,
        feedback: Optional[str] = None,
    ) -> AgentResult:
        """
        Runs the bounded tool-calling loop for `question` (already standalone: the
        contextualizer has resolved any references to earlier turns).

        `retrieval_expected` is the cheap router's confident verdict that this question
        needs the documentation; it only decides whether to fall back to a plain search
        when the model makes no valid tool call, never which tool the model uses.
        `feedback` is the verifier's reason when a previous answer failed grounding.
        """
        result = AgentResult()

        manifest = await mcp_client.list_tools_async()
        tools = to_ollama_tools(manifest)
        manifest_by_name = {t["name"]: t for t in manifest if t.get("name")}

        if not tools:
            result.fallback_reason = "No MCP tools available."
            result.trace.append({"event": "no_tools"})
            return result

        user_content = question
        if feedback:
            user_content += (
                "\n\n(An earlier answer to this question was rejected because it wasn't supported "
                f"by the passages found: {feedback} Look for better evidence.)"
            )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        seen_chunks = set()
        valid_calls = 0
        for round_no in range(1, settings.AGENT_MAX_TOOL_ROUNDS + 1):
            try:
                reply = await llm_gateway.achat_with_tools(
                    messages,
                    tools,
                    temperature=settings.AGENT_TEMPERATURE,
                    seed=settings.AGENT_SEED,
                    num_predict=settings.AGENT_NUM_PREDICT,
                )
            except (OllamaUnavailableError, LLMBudgetExhaustedError) as e:
                result.fallback_reason = f"Model unavailable for tool selection: {e}"
                result.trace.append({"event": "model_error", "round": round_no, "error": str(e)})
                break

            calls = reply["tool_calls"]
            if not calls:
                result.trace.append({"event": "model_finished", "round": round_no, "note": reply["content"][:200]})
                break

            messages.append({
                "role": "assistant",
                "content": reply["content"],
                "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls],
            })

            # Validate every call first, then run the valid ones concurrently: calls in one
            # round don't depend on each other. Tool messages are appended in call order.
            planned = []
            for call in calls:
                arguments, error = _validate_call(call, manifest_by_name)
                planned.append((call, arguments, error))

            # The per-request tool budget lives in a context variable, and tasks started by
            # gather work on copies of it, so reserve the budget here and record usage after.
            used_before = mcp_client.total_tool_calls
            remaining = max(0, settings.MAX_TOOL_CALLS - used_before)
            runnable = [i for i, (_, _, error) in enumerate(planned) if error is None][:remaining]

            async def _run(call, arguments):
                try:
                    return await mcp_client.call_tool_async(call["name"], arguments)
                except CircuitBreakerOpenException as e:
                    return {"status": "circuit_open", "error": f"Tool unavailable: {e}"}

            responses = dict(zip(runnable, await asyncio.gather(
                *(_run(planned[i][0], planned[i][1]) for i in runnable)
            )))
            mcp_client.total_tool_calls = used_before + len(runnable)

            for i, (call, arguments, error) in enumerate(planned):
                if error is None and i not in responses:
                    error = f"Tool-call budget exhausted ({settings.MAX_TOOL_CALLS} per question); work with the results you have."
                if error:
                    result.trace.append({"event": "invalid_call", "round": round_no, "tool": call.get("name"),
                                         "arguments": call.get("arguments"), "error": error})
                    messages.append({"role": "tool", "tool_name": call.get("name") or "unknown", "content": error})
                    continue

                response = responses[i]
                if response.get("status") != "success":
                    error = response.get("error", "Tool call failed.")
                    event = "circuit_open" if response.get("status") == "circuit_open" else "tool_error"
                    result.trace.append({"event": event, "round": round_no, "tool": call["name"],
                                         "arguments": arguments, "error": error})
                    messages.append({"role": "tool", "tool_name": call["name"], "content": error})
                    continue

                valid_calls += 1
                passages = _parse_tool_result(response.get("result"))
                new_passages = []
                for p in passages:
                    # A section fetched with get_section can repeat a searched chunk trimmed
                    # to the section boundary, so key on the text as well as the chunk.
                    key = (p.get("chunk_id"), p.get("content", "")[:80])
                    if key not in seen_chunks:
                        seen_chunks.add(key)
                        new_passages.append(p)
                result.evidence.extend(new_passages)
                result.trace.append({"event": "tool_call", "round": round_no, "tool": call["name"],
                                     "arguments": arguments, "results": len(passages), "new": len(new_passages)})
                messages.append({"role": "tool", "tool_name": call["name"],
                                 "content": _render_for_model(response.get("result"), passages)})

        # Passages from the documents themselves (not an inventory, table of contents or
        # topic overview). Chunk ids of real passages are integers.
        has_doc_passages = any(isinstance(p.get("chunk_id"), int) for p in result.evidence)
        if retrieval_expected and not has_doc_passages:
            reason = result.fallback_reason or (
                "Model made no valid tool call for a documentation question." if valid_calls == 0
                else "Tool calls returned no passages from the documents."
            )
            result.used_fallback = True
            result.fallback_reason = reason
            fallback = await _fallback_search(question, manifest)
            result.evidence.extend(fallback)
            result.trace.append({"event": "fallback_search", "reason": reason, "results": len(fallback)})

        return result
