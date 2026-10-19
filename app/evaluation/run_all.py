import os
import json
import time
import numpy as np
from typing import Dict, Any, List
from app.database.connection import SessionLocal
from app.ingestion.pipeline import sync_directory
from app.retrieval.search import perform_search
from app.evaluation.retrieval_metrics import compute_recall_at_k, compute_mrr_at_k, compute_ndcg_at_k

QUESTIONS_FILE = "data/evaluation/questions.json"
OUTPUT_FILE = "data/evaluation/benchmark_results.json"
DOCS_DIR = "data/documents"

def run_evaluation() -> Dict[str, Any]:
    """Runs end-to-end evaluation suite across retrieval strategies."""
    db = SessionLocal()
    
    # 1. Sync sample documents to DB first
    if os.path.exists(DOCS_DIR):
        sync_directory(db, DOCS_DIR)

    # 2. Load benchmark questions
    if not os.path.exists(QUESTIONS_FILE):
        raise FileNotFoundError(f"Questions file not found: {QUESTIONS_FILE}")
        
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        questions = json.load(f)

    strategies = ["dense", "bm25", "hybrid", "hybrid_rerank"]
    results = {}

    for strat in strategies:
        recall3_list = []
        recall5_list = []
        recall10_list = []
        mrr5_list = []
        mrr10_list = []
        ndcg5_list = []
        ndcg10_list = []
        latencies = []

        for q in questions:
            q_text = q["question"]
            exp_source = q.get("expected_source")

            t0 = time.perf_counter()
            retrieved = perform_search(db, query=q_text, top_k=10, strategy=strat)
            lat = (time.perf_counter() - t0) * 1000.0 # ms
            latencies.append(lat)

            retrieved_sources = [r["source"] for r in retrieved if r.get("source")]

            # Evaluate metrics if query has expected_source
            if exp_source:
                recall3_list.append(compute_recall_at_k(retrieved_sources, exp_source, 3))
                recall5_list.append(compute_recall_at_k(retrieved_sources, exp_source, 5))
                recall10_list.append(compute_recall_at_k(retrieved_sources, exp_source, 10))
                
                mrr5_list.append(compute_mrr_at_k(retrieved_sources, exp_source, 5))
                mrr10_list.append(compute_mrr_at_k(retrieved_sources, exp_source, 10))

                ndcg5_list.append(compute_ndcg_at_k(retrieved_sources, exp_source, 5))
                ndcg10_list.append(compute_ndcg_at_k(retrieved_sources, exp_source, 10))

        results[strat] = {
            "Recall@3": round(float(np.mean(recall3_list)) if recall3_list else 0.0, 4),
            "Recall@5": round(float(np.mean(recall5_list)) if recall5_list else 0.0, 4),
            "Recall@10": round(float(np.mean(recall10_list)) if recall10_list else 0.0, 4),
            "MRR@5": round(float(np.mean(mrr5_list)) if mrr5_list else 0.0, 4),
            "MRR@10": round(float(np.mean(mrr10_list)) if mrr10_list else 0.0, 4),
            "NDCG@5": round(float(np.mean(ndcg5_list)) if ndcg5_list else 0.0, 4),
            "NDCG@10": round(float(np.mean(ndcg10_list)) if ndcg10_list else 0.0, 4),
            "median_latency_ms": round(float(np.median(latencies)), 2),
            "p95_latency_ms": round(float(np.percentile(latencies, 95)), 2)
        }

    db.close()

    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_questions": len(questions),
        "results": results
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print("\n=======================================================")
    print("           BENCHMARK EVALUATION SUMMARY                ")
    print("=======================================================")
    print(f"{'Strategy':<20} | {'Recall@5':<10} | {'MRR@5':<10} | {'NDCG@5':<10} | {'p95 Latency (ms)':<15}")
    print("-" * 75)
    for strat, m in results.items():
        print(f"{strat:<20} | {m['Recall@5']:<10.4f} | {m['MRR@5']:<10.4f} | {m['NDCG@5']:<10.4f} | {m['p95_latency_ms']:<15.2f}")
    print("=======================================================\n")

    return output

if __name__ == "__main__":
    run_evaluation()
