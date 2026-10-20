# GraphRAG-lite: entity/community graph construction + local/global search.
#
# Scoped-down implementation of the pattern Microsoft Research published as
# GraphRAG, built for the same problem this project's interview review
# identified: plain top-k chunk retrieval has no notion of "coverage" across
# a whole corpus, so a query whose answer is scattered across many places
# can't be answered by widening top_k or tuning fusion weights alone.
#
# Scoping differences from the reference implementation, stated plainly
# rather than glossed over:
#   - Entity extraction is regex/heuristic-based (reusing the same
#     technical-token shapes BM25 already extracts, plus capitalized-phrase
#     detection), not LLM-based, to keep ingestion cost bounded and avoid
#     requiring a live LLM during ingestion/tests.
#   - Community detection uses networkx's Louvain implementation (a close,
#     widely-used sibling of GraphRAG's Leiden), not Leiden itself.
#   - Global search caps map-reduce to the top-N communities most relevant
#     to the query (via embedding similarity to each community summary)
#     rather than mapping over every community, to bound LLM call volume.
