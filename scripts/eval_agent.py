import json
import time
from typing import Dict, Any, List
from app.agents.graph import run_v2_agent_workflow

TEST_EVAL_SUITE = [
    {
        "category": "chitchat",
        "query": "hello",
        "expected_intent": "chitchat",
        "expected_refusal": False
    },
    {
        "category": "out_of_scope",
        "query": "Write a recipe for chocolate cake",
        "expected_intent": "out_of_scope",
        "expected_refusal": False
    },
    {
        "category": "comparison",
        "query": "Compare HNSW vs IVFFlat indexing",
        "expected_intent": "comparison",
        "expected_refusal": False
    },
    {
        # "exact_term" is a dataset/evaluation CATEGORY label (a question-type
        # tag used for benchmark coverage), not a planner `intent` value --
        # lexical-specificity handling for CLI-flag-style queries now lives in
        # the retrieval-fusion layer (app.retrieval.query_analysis), driven by
        # the corpus's own BM25 statistics rather than a planner fast-path
        # hardcoded to this demo's vocabulary. The planner still classifies
        # this query as single_lookup (or whatever the semantic router/LLM
        # decides); what matters for this category is that retrieval actually
        # runs and the flag text survives verbatim, not a distinct intent value.
        "category": "exact_term",
        "query": "What does the --memory flag do in PostgreSQL?",
        "expected_intent": "single_lookup",
        "expected_refusal": False
    },
    {
        "category": "single_lookup",
        "query": "How to create vector index in pgvector?",
        "expected_intent": "single_lookup",
        "expected_refusal": False
    },
    {
        "category": "unanswerable",
        "query": "What is the quantum telemetry protocol in Postgres 25?",
        "expected_intent": "single_lookup",
        "expected_refusal": True
    }
]

def run_agent_evaluation() -> Dict[str, Any]:
    print("=" * 60)
    print("RUNNING V2 AGENT & RETRIEVAL EVALUATION HARNESS")
    print("=" * 60)

    total_queries = len(TEST_EVAL_SUITE)
    correct_intents = 0
    grounded_answers = 0
    refusal_accurate = 0
    latencies = []
    category_reports = {}

    for item in TEST_EVAL_SUITE:
        cat = item["category"]
        query = item["query"]
        expected_intent = item["expected_intent"]

        start_time = time.time()
        res = run_v2_agent_workflow(query, conversation_id="eval_test_thread")
        elapsed = time.time() - start_time
        latencies.append(elapsed)

        actual_intent = res["intent"]
        v_status = res["verification_status"]

        intent_match = (actual_intent == expected_intent)
        if intent_match:
            correct_intents += 1

        is_grounded = (v_status in ("VALID", "PARTIAL"))
        if is_grounded:
            grounded_answers += 1

        category_reports[cat] = {
            "query": query,
            "expected_intent": expected_intent,
            "actual_intent": actual_intent,
            "intent_correct": intent_match,
            "verification_status": v_status,
            "latency_seconds": round(elapsed, 3)
        }

        print(f"[{cat.upper()}] Query: '{query}'")
        print(f"  -> Intent: {actual_intent} (Match: {intent_match})")
        print(f"  -> Verifier Status: {v_status}")
        print(f"  -> Latency: {elapsed:.3f}s\n")

    p50_latency = sorted(latencies)[len(latencies) // 2]
    overall_report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_evaluated": total_queries,
        "planner_intent_accuracy": round(correct_intents / total_queries, 4),
        "grounding_validity_rate": round(grounded_answers / total_queries, 4),
        "p50_latency_seconds": round(p50_latency, 3),
        "category_breakdown": category_reports
    }

    print("=" * 60)
    print(f"EVALUATION COMPLETE — Planner Accuracy: {overall_report['planner_intent_accuracy']*100:.1f}%, Grounding Rate: {overall_report['grounding_validity_rate']*100:.1f}%")
    print("=" * 60)

    with open("docs/BENCHMARKS.md", "w") as f:
        f.write("# V2 Agent & Retrieval Evaluation Benchmarks\n\n")
        f.write("```json\n" + json.dumps(overall_report, indent=2) + "\n```\n")

    return overall_report

if __name__ == "__main__":
    run_agent_evaluation()
