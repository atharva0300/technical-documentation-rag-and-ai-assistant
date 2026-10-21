"""
GraphRAG-lite "global search": ranks the pre-built community summaries against a query,
for questions whose answer is scattered across many places in the corpus ("every place X
is mentioned") rather than contained in one chunk.

Chunk ranking only ever sees the passages that score highest for one query embedding; a
topic spread across dozens of pages has no guarantee any top-k set covers it. Community
summaries are built once (app/graph/community_builder.py) specifically to be corpus-aware,
so returning the most relevant ones gives the answerer whole-corpus coverage.

Exposed to the model as the `survey_topic` MCP tool (mcp_server/tools/graph_tools.py).
Synthesis is left to the answerer, so this makes no LLM calls: the reference GraphRAG
map-reduce would cost one LLM call per community on every query.
"""
from typing import List

import numpy as np

from app.database.models import CommunitySummary
from app.retrieval.semantic_router import embed_query


def rank_communities_by_relevance(query: str, communities: List[CommunitySummary]) -> List[CommunitySummary]:
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
