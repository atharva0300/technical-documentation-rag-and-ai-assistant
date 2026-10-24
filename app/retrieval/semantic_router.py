"""
Embedding-similarity semantic router.

Replaces the fixed keyword/regex lists that used to decide chitchat vs.
out-of-scope vs. comparison vs. multi-hop vs. "exhaustive" (answer scattered
across many places) queries. A keyword list can only ever cover the specific
phrasings someone thought to write down; a cosine-similarity match against a
handful of example utterances per category generalizes across paraphrase
("every place X is mentioned" / "all occurrences of X" / "anywhere X comes
up" all land near the same region of embedding space without needing any of
those exact words to be enumerated anywhere).

This reuses the same embedding model already loaded for dense retrieval
(`app.embeddings.model`), so routing costs one extra embedding comparison per
query, not a generative LLM call — chitchat/out-of-scope queries are turned
away without ever reaching the LLM, which is what keeps this cheap enough to
run on every query.
"""
import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.embeddings.model import generate_embeddings_batch

logger = logging.getLogger(__name__)

# A handful of example utterances per category — not keywords, full example
# queries. Expand this list over time; it never needs to be exhaustive the
# way a keyword list would, because similarity generalizes to unseen phrasings
# of the same intent.
ROUTE_EXEMPLARS: Dict[str, List[str]] = {
    "chitchat": [
        "hi",
        "hello",
        "hey there",
        "good morning",
        "good evening",
        "who are you",
        "what can you do",
        "thanks, that's helpful",
        "how are you today",
    ],
    "out_of_scope": [
        "write me a poem",
        "tell me a joke",
        # Keep these topic-neutral: exemplars that mention in-scope topics (e.g. "a joke
        # about Docker") pull genuine questions like "what is docker?" toward a refusal.
        "tell me a joke about programmers",
        "write a limerick about my cat",
        "what's the weather today",
        "recommend a recipe for dinner",
        "who won the football game last night",
        "sing me a song",
        "recommend a good movie",
    ],
    "comparison": [
        "compare X and Y",
        "what's the difference between X and Y",
        "X vs Y, which is better",
        "how does X differ from Y",
        "pros and cons of X versus Y",
        # Longer, technically-worded phrasings of the same intent: real comparison
        # questions about a specific feature read like a normal technical question
        # first, with the comparison framing buried mid-sentence rather than leading
        # with "compare" or "vs" — these were previously losing to point_lookup/
        # exhaustive on cosine similarity because the short exemplars above don't
        # cover that register.
        "what distinguishes X from Y when it comes to this specific feature",
        "how do these two approaches differ in terms of behavior and configuration",
        "when should I use X instead of Y for this use case",
        "X and Y both solve this problem, how are they different under the hood",
        "at what point in the process do X and Y diverge in what they do",
    ],
    "multi_hop": [
        "explain X and also how it affects Y",
        "what is X and how does it relate to Y",
        "describe both X and Y and how they interact",
        "walk me through X, then explain how Y depends on it",
        # Same register gap as comparison above: real multi-hop questions about two
        # systems/components read as one flowing technical question, often without
        # the word "and" as an explicit separator between the two halves.
        "how does this feature in one system connect to the related feature in another",
        "what is the relationship between X here and Y over there",
        "if I want to accomplish this, how do X and Y need to work together",
        "how do changes in one component propagate to or get picked up by the other",
    ],
    "exhaustive": [
        "find every place X is mentioned in the documentation",
        "list all the services that depend on X",
        "what are all the exceptions to this rule",
        "show me every occurrence of X across the docs",
        "give me a complete list of everywhere X shows up",
        "are there any other places this is discussed",
    ],
    "relational": [
        "what depends on X",
        "what breaks if I change X",
        "what else is affected by X",
        "which components call X",
        "trace how X connects to Y",
    ],
    "point_lookup": [
        "what does this flag do",
        "what is the default value for this setting",
        "how do I configure X",
        "what does this error mean",
        "where is X defined",
    ],
}

CONFIDENCE_THRESHOLD = 0.45

# Categories that skip retrieval entirely need a much stricter bar: misrouting a real
# question there means the user gets a greeting or a refusal instead of an answer.
# Calibrated for bge-base-en-v1.5, whose cosine similarities sit higher across the board
# than MiniLM's: genuine questions ("what is spring ?", "what is the elvis operator?")
# score up to ~0.54 against chitchat/out_of_scope, while real greetings and off-topic
# requests score ~0.70+. Below this, the planner falls through to the LLM to decide.
NON_RETRIEVAL_CATEGORIES = {"chitchat", "out_of_scope"}
NON_RETRIEVAL_THRESHOLD = 0.65

_exemplar_embeddings: Optional[Dict[str, np.ndarray]] = None


def _get_exemplar_embeddings() -> Dict[str, np.ndarray]:
    global _exemplar_embeddings
    if _exemplar_embeddings is None:
        cache: Dict[str, np.ndarray] = {}
        for category, examples in ROUTE_EXEMPLARS.items():
            vectors = np.array(generate_embeddings_batch(examples))
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            cache[category] = vectors / norms
        _exemplar_embeddings = cache
    return _exemplar_embeddings


def _cosine_similarity_to_category(query_vec: np.ndarray, category_vecs: np.ndarray) -> float:
    sims = category_vecs @ query_vec
    return float(np.max(sims))


def route_query(query: str) -> Tuple[Optional[str], float]:
    """
    Classifies `query` against every category's exemplars and returns the
    best-matching (category, similarity_score). Returns (None, 0.0) if no
    category clears CONFIDENCE_THRESHOLD — the caller (the planner) should
    treat that as "ambiguous" and fall through to the LLM for a harder
    judgment, rather than guessing.
    """
    exemplars = _get_exemplar_embeddings()

    query_vec = np.array(generate_embeddings_batch([query])[0])
    norm = np.linalg.norm(query_vec)
    if norm > 0:
        query_vec = query_vec / norm

    best_category, best_score = None, -1.0
    for category, vecs in exemplars.items():
        score = _cosine_similarity_to_category(query_vec, vecs)
        if score > best_score:
            best_category, best_score = category, score

    if best_score < CONFIDENCE_THRESHOLD:
        return None, best_score

    if best_category in NON_RETRIEVAL_CATEGORIES and best_score < NON_RETRIEVAL_THRESHOLD:
        return None, best_score

    return best_category, best_score


def embed_query(query: str) -> np.ndarray:
    """Shared helper so callers (e.g. follow-up detection in memory.py) reuse
    the same normalized-embedding machinery instead of recomputing it ad hoc."""
    vec = np.array(generate_embeddings_batch([query])[0])
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec
