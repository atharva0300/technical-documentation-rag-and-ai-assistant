"""
Full-corpus benchmark harness for the live, running agent stack.

Unlike scripts/eval_agent.py (6 hardcoded smoke-test queries), this runs against the
real labeled dataset at data/evaluation/candidates.jsonl (139 questions across 6
categories: single_lookup, multi_hop, comparison, exact_term, unanswerable, followup)
through the actual HTTP API (/api/v2/agent/query) -- the same entrypoint the frontend
uses, backed by the real Ollama model, Postgres/pgvector and MCP server. No mocking.

Because each query takes 1-3 minutes on this CPU-only host, by default this draws a
deterministic stratified sample (proportional to category sizes) rather than running
all 139. Pass --full to run the entire dataset instead.

Usage:
    python3 scripts/eval_agent_full.py --sample 40 --seed 42 --out docs/eval_raw_results.json
    python3 scripts/eval_agent_full.py --full --out docs/eval_raw_results.json
"""
import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = REPO_ROOT / "data" / "evaluation" / "candidates.jsonl"

API_BASE = "http://localhost:8000"
API_KEY = "dev-api-key"
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
REQUEST_TIMEOUT = 600  # seconds, generous ceiling for a single CPU-bound agent turn

# The planner only ever emits these five intents (app/agents/planner.py). The eval
# dataset's category labels are a different taxonomy (question-type tags for benchmark
# coverage), so "exact_term" and "followup" are not themselves planner intents. This is
# the same mapping scripts/eval_agent.py already used for exact_term; applied here
# consistently and extended to followup, which (once the question is contextualized to
# a standalone question) is just whatever ordinary intent that standalone question gets.
CATEGORY_TO_EXPECTED_INTENT = {
    "single_lookup": "single_lookup",
    "multi_hop": "multi_hop",
    "comparison": "comparison",
    "exact_term": "single_lookup",
    "unanswerable": "single_lookup",
    "followup": "single_lookup",
}


def load_dataset():
    items = []
    with open(DATASET_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def stratified_sample(items, sample_size, seed):
    rng = random.Random(seed)
    by_cat = defaultdict(list)
    for it in items:
        by_cat[it["category"]].append(it)

    total = len(items)
    allocation = {}
    remainders = []
    allocated_sum = 0
    for cat, bucket in by_cat.items():
        exact = sample_size * len(bucket) / total
        base = int(exact)
        allocation[cat] = base
        allocated_sum += base
        remainders.append((exact - base, cat))

    # Distribute leftover slots (from integer truncation) to the categories with the
    # largest fractional remainder, so every category keeps at least proportional share.
    remainders.sort(reverse=True)
    leftover = sample_size - allocated_sum
    i = 0
    while leftover > 0 and i < len(remainders):
        allocation[remainders[i][1]] += 1
        leftover -= 1
        i += 1

    sample = []
    for cat, bucket in by_cat.items():
        n = min(allocation.get(cat, 0), len(bucket))
        sample.extend(rng.sample(bucket, n))
    rng.shuffle(sample)
    return sample


def post_query(query, conversation_id=None):
    payload = {"query": query}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    t0 = time.monotonic()
    resp = requests.post(
        f"{API_BASE}/api/v2/agent/query",
        headers=HEADERS,
        data=json.dumps(payload),
        timeout=REQUEST_TIMEOUT,
    )
    elapsed = time.monotonic() - t0
    resp.raise_for_status()
    return resp.json(), elapsed


def normalize_source(path):
    """expected_sources in the dataset are repo-relative (e.g. 'kubernetes/job.md');
    the live API returns the absolute in-container path (e.g.
    '/app/data/documents/kubernetes/job.md'). Compare on the trailing relative part."""
    return path.replace("\\", "/").rstrip("/").split("data/documents/")[-1]


def sources_overlap(returned_sources, expected_sources, expected_keywords):
    returned_paths = {normalize_source(s.get("source", "")) for s in returned_sources}
    expected_paths = {normalize_source(p) for p in (expected_sources or [])}
    path_hit = bool(returned_paths & expected_paths) if expected_paths else None

    kw_hit = None
    if expected_keywords:
        blob = " ".join(s.get("content", "").lower() for s in returned_sources)
        hits = sum(1 for kw in expected_keywords if kw.lower() in blob)
        kw_hit = hits / len(expected_keywords) >= 0.5

    if path_hit is None and kw_hit is None:
        return None
    if path_hit is None:
        return kw_hit
    if kw_hit is None:
        return path_hit
    return path_hit or kw_hit


def run_item(item):
    category = item["category"]
    question = item["question"]
    expected_intent = CATEGORY_TO_EXPECTED_INTENT[category]
    history = item.get("history") or []

    record = {
        "id": item["id"],
        "category": category,
        "question": question,
        "expected_intent": expected_intent,
    }

    try:
        if category == "followup" and history:
            # Build genuine conversation state: actually ask the first real question
            # through the live agent (not a scripted canned answer) so the follow-up
            # exercises the real MemoryLoader / query-contextualization path, then ask
            # the dataset's actual follow-up in the same conversation.
            first_question = history[0]["content"]
            first_res, first_elapsed = post_query(first_question)
            conv_id = first_res["conversation_id"]
            res, elapsed = post_query(question, conversation_id=conv_id)
            record["setup_latency_seconds"] = round(first_elapsed, 3)
        else:
            res, elapsed = post_query(question)

        record["actual_intent"] = res.get("intent")
        record["verification_status"] = res.get("verification_status")
        record["latency_seconds"] = round(elapsed, 3)
        record["answer_preview"] = (res.get("answer") or "")[:300]
        record["num_sources"] = len(res.get("sources") or [])

        if category == "unanswerable":
            record["refused_correctly"] = record["verification_status"] == "REFUSE"
        else:
            overlap = sources_overlap(
                res.get("sources") or [],
                item.get("expected_sources"),
                item.get("expected_chunk_keywords"),
            )
            record["source_overlap"] = overlap

        record["intent_correct"] = record["actual_intent"] == expected_intent
        record["grounded"] = record["verification_status"] in ("VALID", "PARTIAL")
        record["error"] = None
    except Exception as e:
        record["error"] = str(e)
        record["latency_seconds"] = None
        record["actual_intent"] = None
        record["verification_status"] = None
        record["intent_correct"] = False
        record["grounded"] = False

    return record


def percentile(values, pct):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * pct
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def summarize(records):
    evaluated = [r for r in records if r["error"] is None]
    latencies = [r["latency_seconds"] for r in evaluated if r["latency_seconds"] is not None]

    intent_scored = [r for r in evaluated]
    intent_correct = sum(1 for r in intent_scored if r["intent_correct"])

    grounded_count = sum(1 for r in evaluated if r["grounded"])

    unanswerable = [r for r in evaluated if r["category"] == "unanswerable"]
    refusal_correct = sum(1 for r in unanswerable if r.get("refused_correctly"))

    answerable = [r for r in evaluated if r["category"] != "unanswerable"]
    with_overlap_signal = [r for r in answerable if r.get("source_overlap") is not None]
    overlap_hits = sum(1 for r in with_overlap_signal if r["source_overlap"])

    by_category = defaultdict(lambda: {"n": 0, "intent_correct": 0, "grounded": 0})
    for r in evaluated:
        c = by_category[r["category"]]
        c["n"] += 1
        c["intent_correct"] += int(r["intent_correct"])
        c["grounded"] += int(r["grounded"])

    summary = {
        "total_items": len(records),
        "evaluated_items": len(evaluated),
        "errored_items": len(records) - len(evaluated),
        "planner_intent_accuracy": round(intent_correct / len(evaluated), 4) if evaluated else None,
        "grounding_validity_rate": round(grounded_count / len(evaluated), 4) if evaluated else None,
        "unanswerable_refusal_accuracy": round(refusal_correct / len(unanswerable), 4) if unanswerable else None,
        "answerable_source_overlap_rate": round(overlap_hits / len(with_overlap_signal), 4) if with_overlap_signal else None,
        "p50_latency_seconds": round(percentile(latencies, 0.50), 3) if latencies else None,
        "p95_latency_seconds": round(percentile(latencies, 0.95), 3) if latencies else None,
        "mean_latency_seconds": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "max_latency_seconds": round(max(latencies), 3) if latencies else None,
        "category_breakdown": {
            cat: {
                "n": v["n"],
                "intent_accuracy": round(v["intent_correct"] / v["n"], 4) if v["n"] else None,
                "grounding_validity_rate": round(v["grounded"] / v["n"], 4) if v["n"] else None,
            }
            for cat, v in by_category.items()
        },
    }
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=40, help="Stratified sample size (ignored with --full)")
    parser.add_argument("--full", action="store_true", help="Run the entire 139-question dataset")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=str, default=str(REPO_ROOT / "docs" / "eval_raw_results.json"))
    args = parser.parse_args()

    all_items = load_dataset()
    if args.full:
        items = all_items
        method = "full"
    else:
        items = stratified_sample(all_items, args.sample, args.seed)
        method = f"stratified_sample(n={len(items)}, seed={args.seed})"

    print(f"Dataset: {len(all_items)} total questions. Running: {method}")
    cat_counts = defaultdict(int)
    for it in items:
        cat_counts[it["category"]] += 1
    print("Category composition of this run:", dict(cat_counts))

    records = []
    start = time.monotonic()
    for i, item in enumerate(items, 1):
        print(f"[{i}/{len(items)}] ({item['category']}) {item['question'][:80]!r} ...", flush=True)
        rec = run_item(item)
        records.append(rec)
        status = rec.get("error") or f"intent={rec['actual_intent']} verif={rec['verification_status']} lat={rec['latency_seconds']}s"
        print(f"    -> {status}", flush=True)
        # Persist incrementally so a crash/timeout doesn't lose completed work.
        with open(args.out, "w") as f:
            json.dump({"method": method, "records": records}, f, indent=2)

    total_elapsed = time.monotonic() - start
    summary = summarize(records)
    summary["method"] = method
    summary["dataset_total_questions"] = len(all_items)
    summary["total_wallclock_seconds"] = round(total_elapsed, 1)

    out_path = Path(args.out)
    with open(out_path, "w") as f:
        json.dump({"method": method, "summary": summary, "records": records}, f, indent=2)

    print("\n" + "=" * 60)
    print(json.dumps(summary, indent=2))
    print("=" * 60)
    print(f"Raw results + summary written to {out_path}")


if __name__ == "__main__":
    main()
