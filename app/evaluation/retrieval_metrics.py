"""
app/evaluation/retrieval_metrics.py

Retrieval evaluation metrics per Phase 0 R3.5.

All metrics operate on EXACT document paths (no substring matching).
Multi-gold document support for multi-hop and comparison questions.

Definitions (exact):
  hit@k       = 1 if ANY gold document appears in top-k retrieved, else 0
  doc_recall@k = fraction of gold documents found in top-k retrieved
  all_gold@k  = 1 if ALL gold documents appear in top-k retrieved, else 0
  MRR@k       = 1 / rank of the FIRST gold document in top-k, else 0
  NDCG@k      = DCG / IDCG where relevance = 1 for gold docs, 0 otherwise

For single-gold backward compat:
  compute_recall_at_k(retrieved, gold_str, k) -> hit@k (0.0 or 1.0)
  compute_mrr_at_k(retrieved, gold_str, k) -> MRR@k
  compute_ndcg_at_k(retrieved, gold_str, k) -> NDCG@k
"""
import math
from typing import Union


def _to_gold_set(gold: Union[str, list[str]]) -> list[str]:
    """Normalize gold to a list of exact path strings."""
    if isinstance(gold, str):
        return [gold] if gold else []
    return [g for g in gold if g]


# ---------------------------------------------------------------------------
# Multi-gold metrics (R3.5)
# ---------------------------------------------------------------------------

def hit_at_k(retrieved: list[str], gold: Union[str, list[str]], k: int) -> float:
    """1.0 if any gold document is in the top-k retrieved, else 0.0. Exact match."""
    gold_set = set(_to_gold_set(gold))
    if not gold_set:
        return 0.0
    return 1.0 if any(r in gold_set for r in retrieved[:k]) else 0.0


def doc_recall_at_k(retrieved: list[str], gold: Union[str, list[str]], k: int) -> float:
    """Fraction of gold documents found in top-k retrieved. Exact match."""
    golds = _to_gold_set(gold)
    if not golds:
        return 0.0
    retrieved_k = set(retrieved[:k])
    found = sum(1 for g in golds if g in retrieved_k)
    return found / len(golds)


def all_gold_at_k(retrieved: list[str], gold: Union[str, list[str]], k: int) -> float:
    """1.0 if ALL gold documents appear in top-k retrieved, else 0.0. Exact match."""
    golds = _to_gold_set(gold)
    if not golds:
        return 0.0
    retrieved_k = set(retrieved[:k])
    return 1.0 if all(g in retrieved_k for g in golds) else 0.0


def mrr_at_k(retrieved: list[str], gold: Union[str, list[str]], k: int) -> float:
    """1/rank of the first gold document found in top-k. Exact match. 0 if none."""
    gold_set = set(_to_gold_set(gold))
    if not gold_set:
        return 0.0
    for rank, r in enumerate(retrieved[:k], start=1):
        if r in gold_set:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: list[str], gold: Union[str, list[str]], k: int) -> float:
    """NDCG@k with binary relevance: 1 for gold docs, 0 otherwise. Exact match."""
    golds = _to_gold_set(gold)
    if not golds:
        return 0.0
    gold_set = set(golds)

    # DCG: sum of 1/log2(rank+1) for each gold doc found in top-k
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, r in enumerate(retrieved[:k], start=1)
        if r in gold_set
    )

    # IDCG: ideal DCG if all gold docs at top positions
    n_gold = min(len(golds), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, n_gold + 1))

    return dcg / idcg if idcg > 0 else 0.0


# ---------------------------------------------------------------------------
# Single-gold backward-compatible wrappers (used by legacy eval scripts)
# NO substring matching — must be exact paths.
# ---------------------------------------------------------------------------

def compute_recall_at_k(
    retrieved_sources: list[str],
    expected_source: Union[str, list[str]],
    k: int,
) -> float:
    """hit@k: 1.0 if any gold found in top-k. Exact match only."""
    return hit_at_k(retrieved_sources, expected_source, k)


def compute_mrr_at_k(
    retrieved_sources: list[str],
    expected_source: Union[str, list[str]],
    k: int,
) -> float:
    """MRR@k. Exact match only."""
    return mrr_at_k(retrieved_sources, expected_source, k)


def compute_ndcg_at_k(
    retrieved_sources: list[str],
    expected_source: Union[str, list[str]],
    k: int,
) -> float:
    """NDCG@k with binary relevance. Exact match only."""
    return ndcg_at_k(retrieved_sources, expected_source, k)
