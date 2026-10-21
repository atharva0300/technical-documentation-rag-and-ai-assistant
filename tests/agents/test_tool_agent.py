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
    return [{"chunk_id": f"{prefix}-{i}", "title": "Spring", "section": f"17.{i} Section",
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
