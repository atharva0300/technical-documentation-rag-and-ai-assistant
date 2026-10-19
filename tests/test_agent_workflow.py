"""
Smoke test for the V2 multi-agent LangGraph workflow.

The V1 agent graph has been archived to app/archive/agent_v1/.
This test exercises the live V2 pipeline through run_v2_agent_workflow.
"""
from unittest.mock import patch
from app.agents.graph import run_v2_agent_workflow
from app.agents.state import V2AgentState


def test_v2_agent_graph_execution_returns_required_keys():
    """run_v2_agent_workflow must return answer, sources, intent, verification_status, and step_count."""
    dummy_chunks = [
        {
            "chunk_id": 1,
            "title": "Kubernetes Pods",
            "section": "Pod Phase",
            "source": "kubernetes/pod-lifecycle.md",
            "content": "Possible phases include Pending, Running, Succeeded, Failed, Unknown.",
            "similarity": 0.95
        }
    ]

    mock_mcp_response = {
        "status": "success",
        "result": dummy_chunks
    }

    with patch("app.agents.retriever.mcp_client.call_tool", return_value=mock_mcp_response):
        result = run_v2_agent_workflow(
            query="What are the possible phases of a Kubernetes pod?",
            conversation_id="test-smoke"
        )

    assert "answer" in result, "result must contain 'answer'"
    assert "sources" in result, "result must contain 'sources'"
    assert "intent" in result, "result must contain 'intent'"
    assert "verification_status" in result, "result must contain 'verification_status'"
    assert "step_count" in result, "result must contain 'step_count'"
    assert result["step_count"] > 0, "step_count must be > 0 after workflow execution"
