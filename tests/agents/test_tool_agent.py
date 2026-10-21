import json
from unittest.mock import AsyncMock

import pytest

from app.agents import tool_agent
from app.agents.tool_agent import ToolCallingAgent
from app.config import settings
from app.core.llm_gateway import llm_gateway, OllamaUnavailableError

SEARCH_TOOL = {
    "name": "search_documentation",
    "description": "Search the indexed documentation.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
        "required": ["query"],
    },
}


def _passages(prefix, n=2):
    # Integer chunk ids, like passages from the documents themselves.
    return [{"chunk_id": abs(hash((prefix, i))) % 10**6, "title": "Spring", "section": f"17.{i} Section",
             "document_id": "doc1", "content": f"{prefix} passage {i}"} for i in range(n)]


class ScriptedModel:
    """Stands in for llm_gateway.achat_with_tools: returns the scripted replies in
    order and records how it was called."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def __call__(self, messages, tools, temperature=None, seed=None, num_predict=None, model=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools,
                           "temperature": temperature, "seed": seed})
        if not self.replies:
            return {"content": "Done.", "tool_calls": []}
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _call(name, **arguments):
    return {"name": name, "arguments": arguments}


@pytest.fixture
def fake_mcp(monkeypatch):
    calls = []

    async def call_tool(name, arguments):
        calls.append((name, dict(arguments)))
        return {"status": "success", "result": json.dumps(_passages(arguments.get("query", name)))}

    monkeypatch.setattr(tool_agent.mcp_client, "list_tools_async", AsyncMock(return_value=[SEARCH_TOOL]))
    monkeypatch.setattr(tool_agent.mcp_client, "call_tool_async", call_tool)
    return calls


@pytest.fixture
def fallback(monkeypatch):
    mock = AsyncMock(return_value=_passages("fallback"))
    monkeypatch.setattr(tool_agent, "_fallback_search", mock)
    return mock


@pytest.mark.asyncio
async def test_model_calls_tool_with_its_own_reformulated_query(monkeypatch, fake_mcp, fallback):
    model = ScriptedModel([
        {"content": "", "tool_calls": [_call("search_documentation", query="Spring transaction management example code", top_k=1)]},
        {"content": "That covers it.", "tool_calls": []},
    ])
    monkeypatch.setattr(tool_agent.llm_gateway, "achat_with_tools", model)

    result = await ToolCallingAgent.gather_evidence("give me example code for transaction management", retrieval_expected=True)

    # The model's own query is executed, with its top_k clamped to the allowed range.
    assert fake_mcp == [("search_documentation", {"query": "Spring transaction management example code",
                                                  "top_k": settings.AGENT_MIN_TOP_K})]
    assert len(result.evidence) == 2
    assert not result.used_fallback
    fallback.assert_not_called()
    # The tool result was fed back to the model as a tool message before its next decision.
    second_turn = model.calls[1]["messages"]
    assert second_turn[-1]["role"] == "tool"
    assert "transaction management example code passage 0" in second_turn[-1]["content"]


@pytest.mark.asyncio
async def test_tool_selection_runs_at_temperature_zero(monkeypatch, fake_mcp, fallback):
    model = ScriptedModel([{"content": "No search needed.", "tool_calls": []}])
    monkeypatch.setattr(tool_agent.llm_gateway, "achat_with_tools", model)

    await ToolCallingAgent.gather_evidence("hi", retrieval_expected=False)

    assert model.calls[0]["temperature"] == 0
    assert model.calls[0]["seed"] == settings.AGENT_SEED
    tool_names = [t["function"]["name"] for t in model.calls[0]["tools"]]
    assert tool_names == ["search_documentation"]


@pytest.mark.asyncio
async def test_model_decides_not_to_call_a_tool(monkeypatch, fake_mcp, fallback):
    monkeypatch.setattr(tool_agent.llm_gateway, "achat_with_tools",
                        ScriptedModel([{"content": "This doesn't need the docs.", "tool_calls": []}]))

    result = await ToolCallingAgent.gather_evidence("thanks, that helps", retrieval_expected=False)

    assert fake_mcp == []
    assert result.evidence == []
    assert not result.used_fallback
    fallback.assert_not_called()


@pytest.mark.asyncio
async def test_two_tool_calls_in_one_turn_then_another_round(monkeypatch, fake_mcp, fallback):
    model = ScriptedModel([
        {"content": "", "tool_calls": [_call("search_documentation", query="BeanFactory"),
                                       _call("search_documentation", query="MessageSource")]},
        {"content": "", "tool_calls": [_call("search_documentation", query="ApplicationContext vs BeanFactory")]},
        {"content": "Enough to compare them.", "tool_calls": []},
    ])
    monkeypatch.setattr(tool_agent.llm_gateway, "achat_with_tools", model)

    result = await ToolCallingAgent.gather_evidence("difference between BeanFactory and MessageSource", retrieval_expected=True)

    assert [args["query"] for _, args in fake_mcp] == ["BeanFactory", "MessageSource", "ApplicationContext vs BeanFactory"]
    assert len(result.evidence) == 6
    assert len(model.calls) == 3


@pytest.mark.asyncio
async def test_rounds_are_capped(monkeypatch, fake_mcp, fallback):
    endless = [{"content": "", "tool_calls": [_call("search_documentation", query=f"query {i}")]} for i in range(10)]
    model = ScriptedModel(endless)
    monkeypatch.setattr(tool_agent.llm_gateway, "achat_with_tools", model)

    await ToolCallingAgent.gather_evidence("anything", retrieval_expected=True)

    assert len(model.calls) == settings.AGENT_MAX_TOOL_ROUNDS
    assert len(fake_mcp) == settings.AGENT_MAX_TOOL_ROUNDS


@pytest.mark.asyncio
async def test_llm_budget_is_enforced_for_tool_calling_turns(monkeypatch):
    llm_gateway.reset_call_count()
    llm_gateway.llm_call_count = settings.MAX_LLM_CALLS
    chat = AsyncMock()
    monkeypatch.setattr(llm_gateway.async_client, "chat", chat)
    try:
        from app.core.llm_gateway import LLMBudgetExhaustedError
        with pytest.raises(LLMBudgetExhaustedError):
            await llm_gateway.achat_with_tools([{"role": "user", "content": "q"}], [])
        chat.assert_not_called()
    finally:
        llm_gateway.reset_call_count()


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected_and_model_can_correct(monkeypatch, fake_mcp, fallback):
    model = ScriptedModel([
        {"content": "", "tool_calls": [_call("delete_everything", target="all")]},
        {"content": "", "tool_calls": [_call("search_documentation", query="DataBinder")]},
        {"content": "Done.", "tool_calls": []},
    ])
    monkeypatch.setattr(tool_agent.llm_gateway, "achat_with_tools", model)

    result = await ToolCallingAgent.gather_evidence("how to configure a DataBinder", retrieval_expected=True)

    assert fake_mcp == [("search_documentation", {"query": "DataBinder"})]
    assert "Unknown tool 'delete_everything'" in model.calls[1]["messages"][-1]["content"]
    assert any(step["event"] == "invalid_call" for step in result.trace)
    fallback.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_arguments_are_rejected_against_the_mcp_schema(monkeypatch, fake_mcp, fallback):
    model = ScriptedModel([
        {"content": "", "tool_calls": [_call("search_documentation", top_k=5)]},  # missing required query
        {"content": "Done.", "tool_calls": []},
    ])
    monkeypatch.setattr(tool_agent.llm_gateway, "achat_with_tools", model)

    result = await ToolCallingAgent.gather_evidence("how to configure a DataBinder", retrieval_expected=True)

    assert fake_mcp == []
    assert "'query' is a required property" in model.calls[1]["messages"][-1]["content"]
    # Only invalid calls were made for a question that needs the docs: deterministic fallback.
    assert result.used_fallback
    fallback.assert_awaited_once()
    assert result.evidence == _passages("fallback")


@pytest.mark.asyncio
async def test_model_unavailable_falls_back_to_plain_search(monkeypatch, fake_mcp, fallback):
    monkeypatch.setattr(tool_agent.llm_gateway, "achat_with_tools",
                        ScriptedModel([OllamaUnavailableError("connection refused")]))

    result = await ToolCallingAgent.gather_evidence("what is the BeanFactory", retrieval_expected=True)

    assert result.used_fallback
    assert "Model unavailable" in result.fallback_reason
    fallback.assert_awaited_once_with("what is the BeanFactory", [SEARCH_TOOL])


@pytest.mark.asyncio
async def test_no_fallback_when_router_was_not_confident(monkeypatch, fake_mcp, fallback):
    """If the router wasn't sure the question needs the docs, the model's decision not
    to search stands."""
    monkeypatch.setattr(tool_agent.llm_gateway, "achat_with_tools",
                        ScriptedModel([{"content": "No search needed.", "tool_calls": []}]))

    result = await ToolCallingAgent.gather_evidence("ok cool", retrieval_expected=False)

    assert not result.used_fallback
    fallback.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_passes_tools_and_sampling_options_to_ollama(monkeypatch):
    llm_gateway.reset_call_count()
    chat = AsyncMock(return_value={"message": {"content": "", "tool_calls": [
        {"function": {"name": "search_documentation", "arguments": {"query": "x"}}}]}})
    monkeypatch.setattr(llm_gateway.async_client, "chat", chat)
    tools = [{"type": "function", "function": {"name": "search_documentation", "parameters": {}}}]

    reply = await llm_gateway.achat_with_tools([{"role": "user", "content": "q"}], tools,
                                               temperature=0, seed=7, num_predict=50)

    kwargs = chat.call_args.kwargs
    assert kwargs["tools"] == tools
    assert kwargs["options"]["temperature"] == 0
    assert kwargs["options"]["seed"] == 7
    assert kwargs["options"]["num_predict"] == 50
    assert reply["tool_calls"] == [{"name": "search_documentation", "arguments": {"query": "x"}}]
    llm_gateway.reset_call_count()


@pytest.mark.asyncio
async def test_unknown_arguments_are_rejected_so_the_model_rewrites_the_call(monkeypatch, fake_mcp, fallback):
    model = ScriptedModel([
        {"content": "", "tool_calls": [_call("search_documentation", object="DataBinder", query="configuration")]},
        {"content": "", "tool_calls": [_call("search_documentation", query="configure a DataBinder")]},
        {"content": "Done.", "tool_calls": []},
    ])
    monkeypatch.setattr(tool_agent.llm_gateway, "achat_with_tools", model)

    await ToolCallingAgent.gather_evidence("how to configure a DataBinder", retrieval_expected=True)

    assert fake_mcp == [("search_documentation", {"query": "configure a DataBinder"})]
    assert "unknown argument(s) ['object']" in model.calls[1]["messages"][-1]["content"]


def test_tool_schemas_are_inlined_for_the_model():
    from app.mcp.client import to_ollama_tools
    manifest = [{"name": "search_documentation", "description": "d", "input_schema": {
        "type": "object",
        "$defs": {"Filters": {"type": "object", "properties": {"section": {"type": "string"}}}},
        "properties": {"query": {"type": "string"},
                       "filters": {"anyOf": [{"$ref": "#/$defs/Filters"}, {"type": "null"}]}},
    }}]
    params = to_ollama_tools(manifest)[0]["function"]["parameters"]
    assert "$defs" not in params
    assert params["properties"]["filters"]["anyOf"][0]["properties"] == {"section": {"type": "string"}}



@pytest.mark.asyncio
async def test_navigation_only_results_get_a_plain_search_added(monkeypatch, fallback):
    """If the loop ends with only navigation data (an inventory, a table of contents) for a
    question that needs the documents, a plain search is added so the answer has passages."""
    inventory = {"passages": [{"chunk_id": "inventory", "section": "Document inventory", "content": "1 document"}]}
    monkeypatch.setattr(tool_agent.mcp_client, "list_tools_async", AsyncMock(return_value=[SEARCH_TOOL]))

    async def call_tool(name, arguments):
        return {"status": "success", "result": json.dumps(inventory)}

    monkeypatch.setattr(tool_agent.mcp_client, "call_tool_async", call_tool)
    monkeypatch.setattr(tool_agent.llm_gateway, "achat_with_tools", ScriptedModel([
        {"content": "", "tool_calls": [_call("search_documentation", query="LoadTimeWeaver")]},
        {"content": "Done.", "tool_calls": []},
    ]))

    result = await ToolCallingAgent.gather_evidence("explain Registering a LoadTimeWeaver", retrieval_expected=True)

    assert result.used_fallback
    assert result.fallback_reason == "Tool calls returned no passages from the documents."
    assert result.evidence[0]["chunk_id"] == "inventory"
    assert result.evidence[1:] == _passages("fallback")


@pytest.mark.asyncio
async def test_calls_in_one_round_run_concurrently(monkeypatch, fallback):
    import asyncio
    import time
    monkeypatch.setattr(tool_agent.mcp_client, "list_tools_async", AsyncMock(return_value=[SEARCH_TOOL]))

    async def slow_call(name, arguments):
        await asyncio.sleep(0.3)
        return {"status": "success", "result": json.dumps(_passages(arguments["query"]))}

    monkeypatch.setattr(tool_agent.mcp_client, "call_tool_async", slow_call)
    monkeypatch.setattr(tool_agent.llm_gateway, "achat_with_tools", ScriptedModel([
        {"content": "", "tool_calls": [_call("search_documentation", query="BeanFactory"),
                                       _call("search_documentation", query="MessageSource")]},
        {"content": "Done.", "tool_calls": []},
    ]))
    tool_agent.mcp_client.reset_tool_call_count()

    started = time.monotonic()
    result = await ToolCallingAgent.gather_evidence("BeanFactory vs MessageSource", retrieval_expected=True)

    assert time.monotonic() - started < 0.55  # two 0.3s calls overlapped
    assert [s["arguments"]["query"] for s in result.trace if s["event"] == "tool_call"] == ["BeanFactory", "MessageSource"]
    assert tool_agent.mcp_client.total_tool_calls == 2
    tool_agent.mcp_client.reset_tool_call_count()


@pytest.mark.asyncio
async def test_tool_budget_holds_across_concurrent_calls(monkeypatch, fake_mcp, fallback):
    tool_agent.mcp_client.reset_tool_call_count()
    tool_agent.mcp_client.total_tool_calls = settings.MAX_TOOL_CALLS - 1
    model = ScriptedModel([
        {"content": "", "tool_calls": [_call("search_documentation", query="first"),
                                       _call("search_documentation", query="second")]},
        {"content": "Done.", "tool_calls": []},
    ])
    monkeypatch.setattr(tool_agent.llm_gateway, "achat_with_tools", model)

    await ToolCallingAgent.gather_evidence("anything", retrieval_expected=True)

    assert [args["query"] for _, args in fake_mcp] == ["first"]
    assert tool_agent.mcp_client.total_tool_calls == settings.MAX_TOOL_CALLS
    assert "budget exhausted" in model.calls[1]["messages"][-1]["content"]
    tool_agent.mcp_client.reset_tool_call_count()
