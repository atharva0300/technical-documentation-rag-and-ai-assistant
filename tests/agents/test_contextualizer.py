from unittest.mock import AsyncMock

import pytest

from app.agents import graph
from app.agents.memory import MemoryLoader, QueryContextualizer, ContextualizedQuery
from app.core import llm_gateway as gateway_module
from app.core.llm_gateway import OllamaUnavailableError

HISTORY = [
    {"role": "user", "content": "what is transaction management ?"},
    {"role": "assistant", "content": "Transaction management in Spring provides a consistent abstraction "
                                     "over JTA, JDBC and Hibernate transactions. " + "More detail. " * 80},
]


@pytest.fixture
def model(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(gateway_module.llm_gateway, "agenerate", mock)
    return mock


@pytest.mark.asyncio
async def test_pronoun_follow_up_is_rewritten_by_the_model(model):
    model.return_value = "Give me example code for Spring transaction management"

    result = await QueryContextualizer.contextualize("give me an example code for it", HISTORY)

    assert result == ContextualizedQuery(text="Give me example code for Spring transaction management", method="llm")
    prompt = model.call_args.args[0]
    assert "User: what is transaction management ?" in prompt
    assert "Latest question: give me an example code for it" in prompt
    # Earlier answers are trimmed; the rewrite only needs what was being discussed.
    assert "More detail. " * 40 not in prompt
    assert model.call_args.kwargs["options"]["temperature"] == 0


@pytest.mark.asyncio
async def test_standalone_question_comes_back_unchanged(model):
    model.return_value = "How do I configure a DataBinder?"
    result = await QueryContextualizer.contextualize("How do I configure a DataBinder?", HISTORY)
    assert result.text == "How do I configure a DataBinder?"
    assert result.method == "llm"


@pytest.mark.asyncio
async def test_no_history_means_no_llm_call(model):
    result = await QueryContextualizer.contextualize("what is spring ?", [])
    assert result == ContextualizedQuery(text="what is spring ?", method="none")
    model.assert_not_called()


@pytest.mark.asyncio
async def test_model_unavailable_falls_back_to_the_heuristic(model):
    model.side_effect = OllamaUnavailableError("connection refused")

    result = await QueryContextualizer.contextualize("give me an example code for it", HISTORY)

    assert result.method == "heuristic"
    assert result.text == "give me an example code for it (Context: regarding what is transaction management ?)"


@pytest.mark.asyncio
async def test_an_answer_instead_of_a_question_is_rejected(model):
    model.return_value = "Here is an example:\n" + "@Transactional public void transfer() { ... } " * 30

    result = await QueryContextualizer.contextualize("give me an example code for it", HISTORY)

    assert result.method == "heuristic"
    assert "Unusable rewrite" in result.detail


@pytest.mark.asyncio
async def test_label_prefixes_and_quotes_are_stripped(model):
    model.return_value = 'Standalone question: "Give me example code for Spring transaction management"'
    result = await QueryContextualizer.contextualize("give me an example code for it", HISTORY)
    assert result.text == "Give me example code for Spring transaction management"


def test_heuristic_handles_seven_word_pronoun_follow_up():
    """The old heuristic only fired at <= 6 words, which missed this exact question."""
    rewritten = MemoryLoader.rewrite_query("give me an example code for it", HISTORY)
    assert "transaction management" in rewritten


@pytest.mark.asyncio
async def test_memory_node_uses_standalone_question_and_ignores_the_saved_current_message(monkeypatch):
    saved = HISTORY + [{"role": "user", "content": "give me an example code for it"}]
    monkeypatch.setattr(graph.MemoryLoader, "load_history", staticmethod(lambda conversation_id: saved))
    contextualize = AsyncMock(return_value=ContextualizedQuery(
        text="Give me example code for Spring transaction management", method="llm"))
    monkeypatch.setattr(graph.QueryContextualizer, "contextualize", contextualize)
    monkeypatch.setattr(graph.mcp_client, "list_tools_async", AsyncMock(return_value=[]))

    out = await graph.load_memory_node({"conversation_id": "c1", "query": "give me an example code for it",
                                        "history": [], "step_count": 0})

    # The streaming endpoint had already saved the current question; it isn't history.
    assert contextualize.call_args.args[1] == HISTORY
    assert out["rewritten_query"] == "Give me example code for Spring transaction management"
    assert out["trace_events"][0]["method"] == "llm"


@pytest.mark.asyncio
async def test_answerer_receives_the_standalone_question(monkeypatch):
    seen = {}

    def fake_generate_answer(query, evidence):
        seen["query"] = query
        return "answer [1]"

    monkeypatch.setattr(graph.AnswererAgent, "generate_answer", staticmethod(fake_generate_answer))
    await graph.answer_node({"query": "give me an example code for it",
                             "rewritten_query": "Give me example code for Spring transaction management",
                             "intent": "single_lookup", "citations": [{"content": "x"}], "retry_count": 0,
                             "step_count": 0, "stream_handler": None})
    assert seen["query"] == "Give me example code for Spring transaction management"


@pytest.mark.asyncio
async def test_rewrite_that_replaces_the_question_is_rejected(model):
    """Seen with qwen2.5:3b: a new, self-contained question was "rewritten" into the
    previous one. The user's own question must be kept."""
    model.return_value = "what documentation do you have?"
    history = [{"role": "user", "content": "what documentation do you have?"},
               {"role": "assistant", "content": "I have the Spring Framework Documentation indexed."}]

    result = await QueryContextualizer.contextualize("explain Registering a LoadTimeWeaver", history)

    assert result.method == "heuristic"
    assert "LoadTimeWeaver" in result.text
    assert "dropped the question's key terms" in result.detail
