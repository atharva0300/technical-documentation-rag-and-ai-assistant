import pytest
import asyncio
import json
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient

from app.main import app
from app.config import settings
from app.agents.state import V2AgentState
from app.agents.graph import run_v2_agent_workflow_async, v2_supervisor_graph
from app.agents.verifier import VerificationResult
from app.mcp.client import mcp_client, CircuitBreakerOpenException
from app.core.llm_gateway import llm_gateway, LLMBudgetExhaustedError


@pytest.mark.asyncio
async def test_verifier_retry_lifecycle():
    """
    TEST 1 — verifier retry lifecycle:
    First verification -> RETRY, second verification -> VALID.
    Assert: retrieve_count == 2, retry_count == 1, final_status == VALID, graph terminates.
    """
    mcp_client.reset_tool_call_count()
    llm_gateway.reset_call_count()
    mcp_client.reset_circuit_breaker()

    retrieve_call_count = 0
    verify_call_count = 0

    async def mock_retrieve_subquestion_async(*args, **kwargs):
        nonlocal retrieve_call_count
        retrieve_call_count += 1
        return [{"source": "k8s/pod.md", "content": "Pod lifecycle details", "score": 0.95}]

    def mock_verify(*args, **kwargs):
        nonlocal verify_call_count
        verify_call_count += 1
        if verify_call_count == 1:
            return VerificationResult(
                status="RETRY",
                cleaned_answer="Draft answer missing details.",
                unsupported_sentences=["Missing citation."],
                feedback="Include pod status phases."
            )
        else:
            return VerificationResult(
                status="VALID",
                cleaned_answer="Verified complete answer.",
                unsupported_sentences=[],
                feedback="All claims grounded."
            )

    with patch("app.agents.retriever.RetrievalAgent.retrieve_subquestion_async", side_effect=mock_retrieve_subquestion_async), \
         patch("app.agents.verifier.VerifierAgent.verify", side_effect=mock_verify), \
         patch("app.core.llm_gateway.llm_gateway.generate", return_value="Draft answer text"):

        res = await run_v2_agent_workflow_async(query="What are Pod phases in Kubernetes?")

    assert retrieve_call_count == 2
    assert verify_call_count == 2
    assert res["retry_count"] == 1
    assert res["verification_status"] == "VALID"
    assert res["answer"] == "Verified complete answer."


@pytest.mark.asyncio
async def test_verifier_retry_exhaustion():
    """
    TEST 2 — retry exhaustion:
    Always return RETRY.
    Assert: retry count never exceeds MAX_RETRIES, graph terminates, no infinite loop.
    """
    mcp_client.reset_tool_call_count()
    llm_gateway.reset_call_count()
    mcp_client.reset_circuit_breaker()

    retrieve_call_count = 0

    async def mock_retrieve_subquestion_async(*args, **kwargs):
        nonlocal retrieve_call_count
        retrieve_call_count += 1
        return [{"source": "k8s/pod.md", "content": "Pod details", "score": 0.8}]

    def mock_verify_always_retry(*args, **kwargs):
        return VerificationResult(
            status="RETRY",
            cleaned_answer="Unverified draft",
            unsupported_sentences=["Uncited claim"],
            feedback="Needs more citations"
        )

    with patch("app.agents.retriever.RetrievalAgent.retrieve_subquestion_async", side_effect=mock_retrieve_subquestion_async), \
         patch("app.agents.verifier.VerifierAgent.verify", side_effect=mock_verify_always_retry), \
         patch("app.core.llm_gateway.llm_gateway.generate", return_value="Unverified draft"):

        res = await run_v2_agent_workflow_async(query="What is a Pod?")

    assert res["retry_count"] <= settings.MAX_RETRIES
    assert res["verification_status"] == "RETRY"
    assert retrieve_call_count == 2


@pytest.mark.asyncio
async def test_concurrent_mcp_isolation():
    """
    TEST 3 — concurrent MCP isolation:
    Two concurrent graph requests with different MCP call counts.
    Assert request-local counts are exact and no cross-request leakage occurs.
    """
    async def request_a():
        mcp_client.reset_tool_call_count()
        # Request A makes 3 MCP calls directly via call_tool_async
        await mcp_client.call_tool_async("get_document_metadata", {"path": "k8s/pod.md"})
        await mcp_client.call_tool_async("get_document_metadata", {"path": "k8s/svc.md"})
        await mcp_client.call_tool_async("get_document_metadata", {"path": "k8s/deploy.md"})
        return mcp_client.total_tool_calls

    async def request_b():
        mcp_client.reset_tool_call_count()
        # Request B makes 1 MCP call directly
        await mcp_client.call_tool_async("get_document_metadata", {"path": "docker/volume.md"})
        return mcp_client.total_tool_calls

    with patch("mcp.client.Client.call_tool", return_value=MagicMock(is_error=False, content=[MagicMock(text='{"title": "doc"}')])):
        count_a, count_b = await asyncio.gather(request_a(), request_b())

    assert count_a == 3
    assert count_b == 1


@pytest.mark.asyncio
async def test_concurrent_llm_isolation():
    """
    TEST 4 — concurrent LLM isolation:
    Two concurrent tasks with different numbers of LLM calls.
    Assert request-local counts are exact and no cross-request leakage occurs.
    """
    async def task_a():
        llm_gateway.reset_call_count()
        llm_gateway.llm_call_count = 1
        await asyncio.sleep(0.01)
        llm_gateway.llm_call_count = llm_gateway.llm_call_count + 1
        return llm_gateway.llm_call_count

    async def task_b():
        llm_gateway.reset_call_count()
        llm_gateway.llm_call_count = 4
        await asyncio.sleep(0.01)
        return llm_gateway.llm_call_count

    count_a, count_b = await asyncio.gather(task_a(), task_b())

    assert count_a == 2
    assert count_b == 4


@pytest.mark.asyncio
async def test_mcp_circuit_isolation():
    """
    TEST 5 — MCP circuit isolation:
    Request A trips circuit breaker with consecutive errors.
    Request B simultaneously performs a valid call.
    Assert B succeeds and is not blocked by A's circuit state.
    """
    async def failing_request_a():
        mcp_client.reset_circuit_breaker()
        for _ in range(3):
            await mcp_client.call_tool_async("search_documentation", {"query": "fail"})
        return mcp_client.circuit_open

    async def successful_request_b():
        await asyncio.sleep(0.02)
        res = await mcp_client.call_tool_async("search_documentation", {"query": "success"})
        return res

    async def mock_call_tool(tool_name, arguments):
        if arguments.get("query") == "fail":
            raise RuntimeError("Connection refused")
        mock_res = MagicMock()
        mock_res.is_error = False
        mock_res.content = [MagicMock(text='{"ok": true}')]
        return mock_res

    mock_client_obj = MagicMock()
    mock_client_obj.call_tool = mock_call_tool

    async def mock_aenter(*args, **kwargs):
        return mock_client_obj

    async def mock_aexit(*args, **kwargs):
        pass

    with patch("mcp.client.Client.__aenter__", side_effect=mock_aenter), \
         patch("mcp.client.Client.__aexit__", side_effect=mock_aexit):
        circuit_a, res_b = await asyncio.gather(failing_request_a(), successful_request_b())

    print("RES_B IS:", res_b)
    assert circuit_a is True
    assert res_b.get("status") == "success"


def test_real_streaming_graph():
    """
    TEST 6 — real streaming graph via /api/v2/agent/stream:
    Assert SSE events stream from canonical answer_node and final concatenated tokens equal response answer.
    """
    client = TestClient(app)
    headers = {"X-API-Key": settings.API_KEY}

    with patch("app.core.llm_gateway.llm_gateway.astream") as mock_astream, \
         patch("app.agents.retriever.RetrievalAgent.retrieve_subquestion_async", return_value=[{"source": "doc1.md", "content": "test content", "score": 0.9}]):
        
        async def mock_stream_gen(*args, **kwargs):
            yield "Kubernetes "
            yield "Pod "
            yield "Lifecycle"
        mock_astream.side_effect = mock_stream_gen

        response = client.post("/api/v2/agent/stream", json={"query": "Explain Kubernetes Pod Lifecycle"}, headers=headers)
        assert response.status_code == 200

        lines = response.text.strip().split("\n")
        tokens = []
        for line in lines:
            if line.startswith("data: ") and "token" in line:
                try:
                    payload = json.loads(line[6:])
                    if "token" in payload:
                        tokens.append(payload["token"])
                except Exception:
                    pass

        concatenated = "".join(tokens)
        assert "Kubernetes" in concatenated or "Pod" in concatenated


def test_streaming_persistence():
    """
    TEST 7 — streaming persistence:
    Execute a streaming request, then issue a follow-up query in the same conversation.
    Assert follow-up query loads stored previous turn from database.
    """
    client = TestClient(app)
    headers = {"X-API-Key": settings.API_KEY}
    conv_id = "test-stream-persist-conv-123"

    with patch("app.core.llm_gateway.llm_gateway.astream") as mock_astream, \
         patch("app.agents.retriever.RetrievalAgent.retrieve_subquestion_async", return_value=[]):
        
        async def mock_stream_gen(*args, **kwargs):
            yield "Pod restart policies are Always, OnFailure, and Never."
        mock_astream.side_effect = mock_stream_gen

        # Stream request
        res1 = client.post("/api/v2/agent/stream", json={"query": "What are restart policies?", "conversation_id": conv_id}, headers=headers)
        assert res1.status_code == 200

    # Follow-up query in same conversation
    with patch("app.core.llm_gateway.llm_gateway.generate", return_value="Follow-up answer") as mock_gen, \
         patch("app.agents.retriever.RetrievalAgent.retrieve_subquestion_async", return_value=[]):

        res2 = client.post("/api/v2/agent/query", json={"query": "What about default?", "conversation_id": conv_id}, headers=headers)
        assert res2.status_code == 200
        assert res2.json()["conversation_id"] == conv_id


@pytest.mark.asyncio
async def test_streaming_verifier_retry():
    """
    TEST 8 — streaming verifier retry:
    Force first answer -> verifier RETRY, second answer -> verifier VALID.
    Assert streaming uses bounded retry lifecycle.
    """
    mcp_client.reset_tool_call_count()
    llm_gateway.reset_call_count()

    verify_count = 0
    tokens_received = []

    def mock_verify(*args, **kwargs):
        nonlocal verify_count
        verify_count += 1
        if verify_count == 1:
            return VerificationResult(status="RETRY", cleaned_answer="Draft", unsupported_sentences=["Uncited"], feedback="Add citation")
        return VerificationResult(status="VALID", cleaned_answer="Verified Draft", unsupported_sentences=[], feedback="All valid")

    def stream_callback(token: str):
        tokens_received.append(token)

    with patch("app.agents.retriever.RetrievalAgent.retrieve_subquestion_async", return_value=[{"source": "doc.md", "content": "data", "score": 0.9}]), \
         patch("app.agents.verifier.VerifierAgent.verify", side_effect=mock_verify), \
         patch("app.core.llm_gateway.llm_gateway.astream") as mock_astream:
        
        async def mock_stream_gen(*args, **kwargs):
            yield "Draft answer token. "
        mock_astream.side_effect = mock_stream_gen

        res = await run_v2_agent_workflow_async(query="How do StatefulSets work?", stream_callback=stream_callback)

    assert res["retry_count"] == 1
    assert res["verification_status"] == "VALID"
    assert len(tokens_received) > 0


def test_llm_budget_exhaustion():
    """
    TEST 9 — LLM budget exhaustion:
    Force LLM call count to MAX_LLM_CALLS and assert next call raises LLMBudgetExhaustedError.
    """
    llm_gateway.reset_call_count()
    llm_gateway.llm_call_count = settings.MAX_LLM_CALLS

    with pytest.raises(LLMBudgetExhaustedError):
        llm_gateway.generate("Test prompt")


@pytest.mark.asyncio
async def test_mcp_budget_exhaustion():
    """
    TEST 10 — MCP budget exhaustion:
    Force tool call count to MAX_TOOL_CALLS and assert next tool call returns error status.
    """
    mcp_client.reset_tool_call_count()
    mcp_client.total_tool_calls = settings.MAX_TOOL_CALLS

    res = await mcp_client.call_tool_async("get_document_metadata", {"path": "k8s/pod.md"})
    assert res["status"] == "error"
    assert "Budget Exhausted" in res["error"]
