import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
HEADERS = {"X-API-Key": "dev-api-key"}

def test_api_v2_agent_query_unauthorized():
    response = client.post("/api/v2/agent/query", json={"query": "hi"})
    assert response.status_code == 401

def test_api_v2_agent_query_chitchat():
    response = client.post("/api/v2/agent/query", json={"query": "hi", "conversation_id": "test_conv_v2"}, headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["intent"] == "chitchat"
    assert "Hello" in data["answer"]

def test_api_v2_agent_stream_endpoint():
    response = client.post("/api/v2/agent/stream", json={"query": "hi", "conversation_id": "test_stream_conv"}, headers=HEADERS)
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    content = response.text
    assert "event: status" in content
    assert "event: done" in content

def test_api_v2_eval_job_queue():
    response = client.post("/api/v2/jobs/eval", headers=HEADERS)
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    job_id = data["job_id"]
    
    # Query status
    status_resp = client.get(f"/api/v2/jobs/{job_id}", headers=HEADERS)
    assert status_resp.status_code == 200
    job_data = status_resp.json()
    assert job_data["job_id"] == job_id
    assert job_data["status"] in ("pending", "running", "completed")
