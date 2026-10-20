"""
GraphRAG-lite "global search": map-reduce over pre-built community summaries,
for queries whose answer is scattered across many places in the corpus
("find every place X is mentioned") rather than contained in one chunk.

This is the actual fix for that failure mode -- not a bigger `top_k`, not a
cleverer fusion formula over chunk-level rankings. Those still only ever see
the chunks that rank highest for one query embedding; a query whose answer is
spread across 33 of 100 pages has no guarantee any top-k set covers all 33.
Community summaries are built once at ingestion time specifically to be
corpus-aware, so map-reduce over them can answer "what, across the whole
corpus, is true about X" in a way chunk-ranking structurally cannot.

Scoped down from the reference GraphRAG implementation: instead of mapping
over every community (unbounded LLM call volume), this ranks communities by
embedding similarity to the query first and maps over only the top-N --
documented plainly as a cost/completeness trade-off, not hidden.
"""
import logging
from typing import Dict, Any, List

import numpy as np
from sqlalchemy.orm import Session

from app.database.models import CommunitySummary
from app.core.llm_gateway import llm_gateway, OllamaUnavailableError
from app.retrieval.semantic_router import embed_query

logger = logging.getLogger(__name__)

MAX_COMMUNITIES_TO_MAP = 5


def _rank_communities_by_relevance(query: str, communities: List[CommunitySummary]) -> List[CommunitySummary]:
    query_vec = embed_query(query)
    scored = []
    for community in communities:
        try:
            summary_vec = embed_query(community.summary_text)
            score = float(np.dot(query_vec, summary_vec))
        except Exception:
            score = 0.0
        scored.append((score, community))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored]


def _map_partial_answer(query: str, community: CommunitySummary) -> str:
    try:
        return llm_gateway.generate(
            prompt=(
                f"Question: {query}\n\n"
                f"Community summary (community #{community.community_id}, {community.chunk_count} chunks):\n"
                f"{community.summary_text}\n\n"
                "If this community summary is relevant to the question, extract the relevant facts. "
                "If it is not relevant at all, respond with exactly: NOT_RELEVANT."
            ),
            system="You extract relevant facts from one community's summary to help answer a broader question. Be concise.",
        )
    except OllamaUnavailableError:
        return community.summary_text


def answer_global(db: Session, query: str) -> Dict[str, Any]:
    """
    Map-reduce global search. Returns {"answer", "communities_used", "citations"}.
    Falls back to an explicit "no knowledge graph built yet" signal (rather
    than silently returning nothing) if `rebuild_knowledge_graph` has never
    run for this corpus -- callers (graph.py's global_search_node) should
    fall back to ordinary retrieval in that case.
    """
    communities = db.query(CommunitySummary).all()
    if not communities:
        return {"answer": None, "communities_used": 0, "citations": [], "graph_available": False}

    ranked = _rank_communities_by_relevance(query, communities)[:MAX_COMMUNITIES_TO_MAP]

    partial_answers = []
    for community in ranked:
        partial = _map_partial_answer(query, community)
        if partial and "NOT_RELEVANT" not in partial.upper():
            partial_answers.append((community, partial))

    if not partial_answers:
        return {"answer": None, "communities_used": len(ranked), "citations": [], "graph_available": True}

    combined = "\n\n".join(f"[Community #{c.community_id}] {text}" for c, text in partial_answers)

    try:
        final_answer = llm_gateway.generate(
            prompt=(
                f"Question: {query}\n\n"
                f"Partial answers gathered from {len(partial_answers)} relevant document communities:\n\n{combined}\n\n"
                "Synthesize these into one comprehensive answer that covers everything relevant found "
                "across all the communities above, citing community numbers like [Community #n]."
            ),
            system="You synthesize partial findings gathered from across an entire document corpus into one coherent, comprehensive answer.",
        )
    except OllamaUnavailableError:
        final_answer = combined

    return {
        "answer": final_answer,
        "communities_used": len(partial_answers),
        "citations": [
            {"citation_id": idx + 1, "source": f"community:{c.community_id}", "content": text}
            for idx, (c, text) in enumerate(partial_answers)
        ],
        "graph_available": True,
    }
