# V2 Agent & Retrieval Evaluation Benchmarks

```json
{
  "timestamp": "2026-09-22 17:32:42",
  "total_evaluated": 6,
  "planner_intent_accuracy": 1.0,
  "grounding_validity_rate": 1.0,
  "p50_latency_seconds": 2.722,
  "category_breakdown": {
    "chitchat": {
      "query": "hello",
      "expected_intent": "chitchat",
      "actual_intent": "chitchat",
      "intent_correct": true,
      "verification_status": "VALID",
      "latency_seconds": 0.055
    },
    "out_of_scope": {
      "query": "Write a recipe for chocolate cake",
      "expected_intent": "out_of_scope",
      "actual_intent": "out_of_scope",
      "intent_correct": true,
      "verification_status": "VALID",
      "latency_seconds": 0.007
    },
    "comparison": {
      "query": "Compare HNSW vs IVFFlat indexing",
      "expected_intent": "comparison",
      "actual_intent": "comparison",
      "intent_correct": true,
      "verification_status": "PARTIAL",
      "latency_seconds": 18.329
    },
    "exact_term": {
      "query": "What does the --memory flag do in PostgreSQL?",
      "expected_intent": "exact_term",
      "actual_intent": "exact_term",
      "intent_correct": true,
      "verification_status": "PARTIAL",
      "latency_seconds": 2.627
    },
    "single_lookup": {
      "query": "How to create vector index in pgvector?",
      "expected_intent": "single_lookup",
      "actual_intent": "single_lookup",
      "intent_correct": true,
      "verification_status": "PARTIAL",
      "latency_seconds": 2.722
    },
    "unanswerable": {
      "query": "What is the quantum telemetry protocol in Postgres 25?",
      "expected_intent": "single_lookup",
      "actual_intent": "single_lookup",
      "intent_correct": true,
      "verification_status": "PARTIAL",
      "latency_seconds": 2.912
    }
  }
}
```

---

> ℹ️ Retrieval uses a single fixed strategy (`hybrid_rerank`: dense + BM25 fused with RRF,
> cross-encoder reranked) — there is no score-threshold-based refinement loop to calibrate.

