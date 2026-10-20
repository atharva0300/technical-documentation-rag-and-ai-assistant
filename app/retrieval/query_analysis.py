"""
Corpus-derived lexical-specificity signal.

Replaces the old `exact_term` fast-path, which hardcoded `kubectl `/`docker `
prefix checks into the planner — vocabulary tied to one demo corpus that
silently stops meaning anything the moment a user uploads documentation about
something else entirely, since the project's premise is "upload any
technical documentation."

"Is this an exact-term/lexical-style query" isn't a semantic category to
classify (so it doesn't belong in the semantic router either) — it's a
statistical property of the query *relative to whatever corpus is currently
loaded*. BM25's IDF component already measures exactly this: how rare/
distinctive a term is in the current corpus. A query containing a rare,
exact-matching term is a lexical-style query for THIS corpus, whatever that
corpus happens to be about — no hardcoded vocabulary required, and the
signal moves automatically with whatever gets uploaded.

This is the standard mechanism behind query-adaptive sparse/dense weighting
in production hybrid search (e.g. hybrid "alpha" tuning in systems like
Weaviate/Azure AI Search): use the query's own lexical-rarity profile against
the corpus to decide how much to trust lexical (BM25) vs. semantic (dense)
retrieval for that specific query, rather than a fixed 50/50 blend or a
hardcoded intent bucket.
"""
import logging
from typing import Optional
from sqlalchemy.orm import Session

from app.retrieval.bm25 import get_bm25_index, _tokenize

logger = logging.getLogger(__name__)

# Below this IDF value a term is common enough in the corpus that matching it
# exactly isn't a meaningful lexical signal (every corpus has some "common"
# technical-shaped tokens, e.g. a frequently repeated flag name).
IDF_RARE_THRESHOLD = 2.0


def compute_lexical_specificity(db: Session, query: str) -> float:
    """
    Returns a 0.0-1.0 score: how strongly this query is a lexical/exact-match
    -style query against the corpus currently loaded in `db`, derived purely
    from that corpus's own BM25 statistics (IDF). 0.0 means "no rare, exact-
    matching terms found" (a purely semantic query, or a query about content
    that isn't in this corpus); higher means the query contains one or more
    terms that are both syntactically technical-shaped (per the same
    tokenizer BM25 already uses) AND rare/distinctive in this specific
    corpus, which is the generalizing analogue of "contains a CLI flag like
    --memory" without ever naming a flag, a command, or a vocabulary list.
    """
    bm25, chunks = get_bm25_index(db)
    if not bm25 or not chunks:
        return 0.0

    query_tokens = _tokenize(query)
    if not query_tokens:
        return 0.0

    idf_map = getattr(bm25, "idf", {}) or {}
    if not idf_map:
        return 0.0

    max_idf = max(idf_map.values()) if idf_map else 1.0
    if max_idf <= 0:
        return 0.0

    best_score = 0.0
    for token in query_tokens:
        idf = idf_map.get(token)
        if idf is None:
            continue
        if idf < IDF_RARE_THRESHOLD:
            continue
        normalized = min(1.0, idf / max_idf)
        best_score = max(best_score, normalized)

    return best_score


def resolve_hybrid_weights(lexical_specificity: float) -> Optional[dict]:
    """
    Converts the lexical-specificity score into RRF per-source weights for
    `reciprocal_rank_fusion`. Returns None for the default/unweighted (equal)
    case so callers can skip passing weights entirely when there's no signal.
    """
    if lexical_specificity <= 0.0:
        return None
    # Linearly scale BM25's weight up (and dense's down) with how lexically
    # distinctive the query is, capped so dense search is never fully zeroed
    # out — a rare term match is strong evidence, not certainty.
    bm25_weight = 1.0 + lexical_specificity * 1.5
    dense_weight = max(0.4, 1.0 - lexical_specificity * 0.6)
    return {"bm25_weight": bm25_weight, "dense_weight": dense_weight}
