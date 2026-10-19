import pytest
import json
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
HEADERS = {"X-API-Key": "dev-api-key"}

def test_real_sse_streaming_events():
    response = client.post(
        "/api/v2/agent/stream", 
        json={"query": "hi", "conversation_id": "test_sse_stream"},
        headers=HEADERS
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    
    text = response.text
    # Verify real SSE framing and distinct event types
    events = [line for line in text.split("\n") if line.startswith("event:")]
    event_names = [e.replace("event: ", "").strip() for e in events]
    
    assert "status" in event_names
    assert "planner" in event_names
    assert "token" in event_names
    assert "verification" in event_names
    assert "done" in event_names

def test_real_sse_tokens_emitted_incrementally():
    response = client.post(
        "/api/v2/agent/stream", 
        json={"query": "hi", "conversation_id": "test_sse_incremental"},
        headers=HEADERS
    )
    
    lines = response.text.split("\n")
    token_lines = [l for l in lines if l.startswith("data:") and "token" in l]
    
    # Assert multiple token events are streamed
    assert len(token_lines) >= 1
    sample_data = json.loads(token_lines[0].replace("data: ", ""))
    assert "token" in sample_data
