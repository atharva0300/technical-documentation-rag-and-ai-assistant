"""
Lightweight, corpus-agnostic entity extraction for the knowledge graph.

Reuses the same technical-token shapes `app.retrieval.bm25._tokenize` already
extracts (CLI flags, version strings, dotted/hyphenated identifiers) plus a
simple capitalized-phrase heuristic (consecutive Capitalized Words, e.g.
"PostgreSQL", "Kubernetes Service", "Reciprocal Rank Fusion") to approximate
named entities without an LLM call per chunk. This is the scoped-down
substitute for GraphRAG's LLM-based extraction — cheaper and bounded-cost at
ingestion time, at the expense of being less semantically precise. Nothing
here is tied to any particular technical domain's vocabulary.
"""
import re
from typing import List, Set

from app.retrieval.bm25 import _tokenize

_CAPITALIZED_PHRASE_RE = re.compile(r'\b(?:[A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*){0,3})\b')

# Generic stopword set for phrase starts -- not domain vocabulary, just the
# words that make capitalized-phrase detection noisy at sentence boundaries.
_COMMON_SENTENCE_STARTERS = {"The", "This", "That", "These", "Those", "A", "An", "It", "If", "When", "For"}


def extract_entities(text: str, max_entities: int = 25) -> List[str]:
    """
    Returns a deduplicated list of entity-like strings found in `text`:
    technical tokens (flags, versions, identifiers) and capitalized phrases
    (candidate proper nouns / named concepts). Order is first-seen, capped at
    `max_entities` to keep per-chunk graph fan-out bounded.
    """
    if not text:
        return []

    seen: Set[str] = set()
    entities: List[str] = []

    for token in _tokenize(text):
        # Only technical-shaped tokens (contain a digit, a flag dash, or a
        # dot/hyphen compound) count as entities -- plain lowercase words
        # from _tokenize are just regular vocabulary, not identifiers.
        if not re.search(r'[\d.\-]', token) and not token.startswith('--'):
            continue
        if token not in seen:
            seen.add(token)
            entities.append(token)

    for match in _CAPITALIZED_PHRASE_RE.finditer(text):
        phrase = match.group(0).strip()
        first_word = phrase.split()[0] if phrase else ""
        if len(phrase) < 3 or first_word in _COMMON_SENTENCE_STARTERS:
            continue
        key = phrase.lower()
        if key not in seen:
            seen.add(key)
            entities.append(phrase)

    return entities[:max_entities]
