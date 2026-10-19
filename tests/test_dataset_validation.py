"""
tests/test_dataset_validation.py

Hard-fail dataset validation tests (R2.11).

Requires:
  - data/evaluation/candidates.jsonl exists
  - sentence-transformers installed (for semantic diversity check)

Run: pytest tests/test_dataset_validation.py -v
"""
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
DOCS_DIR  = REPO_ROOT / "data" / "documents"
MANIFEST  = DOCS_DIR / "MANIFEST.json"
EVAL_DIR  = REPO_ROOT / "data" / "evaluation"

CATEGORY_MINIMUMS = {
    "single_lookup":  48,
    "multi_hop":      24,
    "comparison":     16,
    "exact_term":     12,
    "unanswerable":   12,
    "followup":        8,
}
TOTAL_MINIMUM = 120
SEMANTIC_COSINE_MAX = 0.92
LEXICAL_DIVERSITY_MIN = 0.95
FOLLOWUP_DISTINCT_MIN = 8

# A row counts as verified only if BOTH conditions are met:
#   1. status == "verified"
#   2. verified_by is non-empty and does NOT match the banned-pattern
# This cannot be bypassed by scripts — it is enforced in every test and every script.
BANNED_VERIFIER_RE = re.compile(
    r"^(auto|bot|agent|script|ai|llm|system|test|antigravity)$",
    re.IGNORECASE,
)

STOPWORDS = {
    "the","a","an","is","are","was","were","be","been","being","have","has","had",
    "do","does","did","will","would","could","should","may","might","can","and",
    "or","but","in","on","at","to","for","of","from","with","by","as","this",
    "that","these","those","it","its","they","them","their","there","when",
    "where","how","what","which","who","more","also","each","all","not","if",
    "then","than","so","i","you","we","my","your","our","its","no","yes",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_manifest_paths() -> set[str]:
    if not MANIFEST.exists():
        return set()
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {e["path"] for e in data.get("files", [])}


def load_corpus_text() -> dict[str, str]:
    corpus: dict[str, str] = {}
    if not DOCS_DIR.exists():
        return corpus
    for f in DOCS_DIR.rglob("*.md"):
        rel = f.relative_to(DOCS_DIR).as_posix()
        corpus[rel] = f.read_text(encoding="utf-8", errors="replace")
    return corpus


def load_candidates() -> list[dict]:
    p = EVAL_DIR / "candidates.jsonl"
    if not p.exists():
        return []
    rows = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def verified(rows: list[dict]) -> list[dict]:
    """A row is verified only if status=verified AND verified_by is a non-empty,
    non-banned string. Scripts may never produce a row that passes this check."""
    result = []
    for r in rows:
        if r.get("status") != "verified":
            continue
        vby = (r.get("verified_by") or "").strip()
        if not vby or BANNED_VERIFIER_RE.match(vby):
            continue
        result.append(r)
    return result


def normalize_question(q: str) -> str:
    """Lowercase + strip digits for lexical diversity check."""
    q = q.lower()
    q = re.sub(r"\d+", "", q)
    q = re.sub(r"[^a-z ]", " ", q)
    q = re.sub(r" +", " ", q).strip()
    return q


def meaningful_terms(text: str) -> set[str]:
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]{2,}\b", text.lower())
    return {w for w in words if w not in STOPWORDS}


def lexical_overlap(q: str, passage: str) -> float:
    """Jaccard overlap between question terms and passage terms."""
    q_terms = meaningful_terms(q)
    p_terms = meaningful_terms(passage)
    if not q_terms or not p_terms:
        return 0.0
    intersection = q_terms & p_terms
    union = q_terms | p_terms
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def all_rows():
    return load_candidates()


@pytest.fixture(scope="module")
def verified_rows(all_rows):
    return verified(all_rows)


@pytest.fixture(scope="module")
def manifest_paths():
    return load_manifest_paths()


@pytest.fixture(scope="module")
def corpus_text():
    return load_corpus_text()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_candidates_file_exists():
    """candidates.jsonl must exist."""
    assert (EVAL_DIR / "candidates.jsonl").exists(), \
        "candidates.jsonl not found — run generate_candidates.py"


def test_verified_count(verified_rows):
    """Must have >= 120 verified rows."""
    assert len(verified_rows) >= TOTAL_MINIMUM, \
        f"Only {len(verified_rows)} verified rows; need >= {TOTAL_MINIMUM}"


@pytest.mark.parametrize("cat,minimum", CATEGORY_MINIMUMS.items())
def test_category_minimum(cat, minimum, verified_rows):
    """Each category must meet its minimum verified count."""
    cnt = sum(1 for r in verified_rows if r.get("category") == cat)
    assert cnt >= minimum, f"Category {cat!r}: {cnt} verified rows, need >= {minimum}"


def test_no_empty_questions(verified_rows):
    """All verified answerable rows must have non-empty question."""
    bad = [r["id"] for r in verified_rows
           if r.get("category") != "unanswerable" and not r.get("question", "").strip()]
    assert not bad, f"Verified rows with empty question: {bad}"


def test_no_empty_reference_answers_for_answerable(verified_rows):
    """All verified answerable rows must have non-empty reference_answer."""
    bad = [r["id"] for r in verified_rows
           if r.get("category") != "unanswerable" and not r.get("reference_answer", "").strip()]
    assert not bad, f"Verified answerable rows with empty reference_answer: {bad}"


def test_no_missing_expected_sources(verified_rows, manifest_paths):
    """All expected_sources must appear in MANIFEST.json."""
    bad: list[str] = []
    for r in verified_rows:
        if r.get("category") == "unanswerable":
            continue
        for src in (r.get("expected_sources") or []):
            if src not in manifest_paths:
                bad.append(f"{r['id']}: {src!r}")
    assert not bad, f"Missing expected_sources in manifest: {bad}"


def test_keyword_validity(verified_rows, corpus_text):
    """Every expected_chunk_keyword must appear in at least one gold document."""
    bad: list[str] = []
    for r in verified_rows:
        if r.get("category") == "unanswerable":
            continue
        sources = r.get("expected_sources") or []
        keywords = r.get("expected_chunk_keywords") or []
        for kw in keywords:
            found = any(
                re.search(re.escape(kw), corpus_text.get(src, ""), re.IGNORECASE)
                for src in sources
            )
            if not found:
                bad.append(f"{r['id']}: keyword {kw!r} not in {sources}")
    assert not bad, f"Keywords not found in gold documents: {bad[:10]}..."


def test_reference_answer_grounded(verified_rows, corpus_text):
    """Meaningful terms from reference_answer must appear in gold passage."""
    bad: list[str] = []
    for r in verified_rows:
        if r.get("category") in ("unanswerable", "followup"):
            continue
        answer = r.get("reference_answer", "")
        sources = r.get("expected_sources") or []
        if not answer or not sources:
            continue
        ans_terms = meaningful_terms(answer) - STOPWORDS
        if not ans_terms:
            continue
        all_gold_text = " ".join(corpus_text.get(s, "") for s in sources)
        # At least 50% of meaningful answer terms must appear in gold passages
        found_terms = {t for t in ans_terms if re.search(re.escape(t), all_gold_text, re.IGNORECASE)}
        coverage = len(found_terms) / len(ans_terms)
        if coverage < 0.50:
            bad.append(f"{r['id']}: only {coverage:.0%} of answer terms in gold ({found_terms})")
    assert not bad, f"Reference answers not grounded in gold passages: {bad[:5]}"


def test_unanswerable_key_terms_absent_from_corpus(verified_rows, corpus_text):
    """For unanswerable rows, absent_check_regex must not match any corpus file."""
    all_text = "\n".join(corpus_text.values())
    bad: list[str] = []
    for r in verified_rows:
        if r.get("category") != "unanswerable":
            continue
        regex = r.get("absent_check_regex", "")
        if not regex:
            continue
        if re.search(regex, all_text, re.IGNORECASE):
            bad.append(f"{r['id']}: term {r.get('absent_key_term')!r} IS found in corpus")
    assert not bad, f"Unanswerable key terms found in corpus: {bad}"


def test_lexical_diversity(verified_rows):
    """distinct_normalized_questions / total_verified >= 0.95"""
    qs = [normalize_question(r.get("question", "")) for r in verified_rows]
    total = len(qs)
    distinct = len(set(qs))
    ratio = distinct / total if total > 0 else 0.0
    assert ratio >= LEXICAL_DIVERSITY_MIN, \
        f"Lexical diversity {ratio:.3f} < {LEXICAL_DIVERSITY_MIN} ({distinct}/{total} distinct)"


def test_semantic_diversity(verified_rows):
    """No two verified questions may have cosine similarity > 0.92.
    If sentence-transformers is unavailable, this test FAILS (not skips)."""
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:
        pytest.fail(
            "sentence-transformers is required for semantic diversity check but is not installed. "
            "Install it: uv pip install -p .venv sentence-transformers"
        )

    questions = [r.get("question", "") for r in verified_rows]
    if len(questions) < 2:
        return

    model = SentenceTransformer("all-MiniLM-L6-v2")
    embeddings = model.encode(questions, batch_size=32, show_progress_bar=False, normalize_embeddings=True)
    # Cosine similarity = dot product (since normalized)
    sim_matrix = np.dot(embeddings, embeddings.T)

    violations: list[tuple[int, int, float]] = []
    n = len(questions)
    for i in range(n):
        for j in range(i + 1, n):
            sim = float(sim_matrix[i, j])
            if sim > SEMANTIC_COSINE_MAX:
                violations.append((i, j, sim))

    assert not violations, (
        f"Semantic diversity violations (cosine > {SEMANTIC_COSINE_MAX}): "
        f"{[(verified_rows[i]['id'], verified_rows[j]['id'], f'{s:.3f}') for i,j,s in violations[:5]]}"
    )


def test_followup_distinct_histories(verified_rows):
    """At least 8 genuinely distinct followup histories."""
    fu_rows = [r for r in verified_rows if r.get("category") == "followup"]
    # Two histories are distinct only if their JSON representation differs
    distinct_histories: set[str] = set()
    for r in fu_rows:
        hist = r.get("history")
        if hist:
            distinct_histories.add(json.dumps(hist, sort_keys=True))
    assert len(distinct_histories) >= FOLLOWUP_DISTINCT_MIN, \
        f"Only {len(distinct_histories)} distinct followup histories; need >= {FOLLOWUP_DISTINCT_MIN}"


def test_no_duplicate_questions(verified_rows):
    """No two verified questions may be identical."""
    qs = [r.get("question", "").strip().lower() for r in verified_rows]
    seen: set[str] = set()
    dups: list[str] = []
    for q in qs:
        if q in seen:
            dups.append(q[:60])
        seen.add(q)
    assert not dups, f"Duplicate questions found: {dups[:5]}"


def test_lexical_overlap_reported(verified_rows, corpus_text):
    """Compute and report mean question-to-gold-passage lexical overlap.
    Definition: Jaccard(meaningful_question_terms, meaningful_passage_terms).
    Only meaningful if the overlap is not trivially 0.0.
    This test always passes — it reports the statistic."""
    overlaps = []
    for r in verified_rows:
        q = r.get("question", "")
        passage = r.get("passage_text", "")
        if q and passage:
            overlaps.append(lexical_overlap(q, passage))
    if overlaps:
        mean_overlap = sum(overlaps) / len(overlaps)
        # Write to a results file for the Phase 0 report
        results_path = EVAL_DIR / "lexical_overlap.txt"
        results_path.write_text(
            f"mean_question_gold_passage_lexical_overlap={mean_overlap:.4f}\n"
            f"definition=Jaccard(meaningful_question_terms, meaningful_passage_terms)\n"
            f"n={len(overlaps)}\n",
            encoding="utf-8",
        )
        print(f"\n  Mean lexical overlap: {mean_overlap:.4f} (n={len(overlaps)})")
    # Always passes
    assert True


# ---------------------------------------------------------------------------
# Split tests (run after split_dataset.py)
# ---------------------------------------------------------------------------

def test_dev_and_held_out_exist():
    """dev.jsonl and held_out.jsonl must exist after split."""
    assert (EVAL_DIR / "dev.jsonl").exists(), "dev.jsonl not found — run split_dataset.py"
    assert (EVAL_DIR / "held_out.jsonl").exists(), "held_out.jsonl not found"


def test_held_out_hash_locked():
    """HELD_OUT_HASH.txt must match current SHA-256 of held_out.jsonl."""
    held_path = EVAL_DIR / "held_out.jsonl"
    hash_path = EVAL_DIR / "HELD_OUT_HASH.txt"
    if not held_path.exists() or not hash_path.exists():
        pytest.skip("Split files not yet created")
    actual = hashlib.sha256(held_path.read_bytes()).hexdigest()
    stored = hash_path.read_text(encoding="utf-8").strip()
    assert actual == stored, f"held_out.jsonl SHA mismatch: stored={stored[:16]}, actual={actual[:16]}"


def test_review_csv_covers_all_held_out():
    """review.csv must contain 100% of held_out rows."""
    held_path   = EVAL_DIR / "held_out.jsonl"
    review_path = EVAL_DIR / "review.csv"
    if not held_path.exists() or not review_path.exists():
        pytest.skip("Split files not yet created")

    held_ids: set[str] = set()
    with open(held_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                held_ids.add(json.loads(line)["id"])

    import csv
    review_ids: set[str] = set()
    with open(review_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("split") == "held_out":
                review_ids.add(row["id"])

    missing = held_ids - review_ids
    assert not missing, f"Held-out rows missing from review.csv: {missing}"


def test_review_csv_covers_at_least_30pct_dev():
    """review.csv must contain >= 30% of dev rows."""
    dev_path    = EVAL_DIR / "dev.jsonl"
    review_path = EVAL_DIR / "review.csv"
    if not dev_path.exists() or not review_path.exists():
        pytest.skip("Split files not yet created")

    dev_ids: set[str] = set()
    with open(dev_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                dev_ids.add(json.loads(line)["id"])

    import csv
    review_dev_ids: set[str] = set()
    with open(review_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("split") == "dev":
                review_dev_ids.add(row["id"])

    frac = len(review_dev_ids) / len(dev_ids) if dev_ids else 0.0
    assert frac >= 0.30, \
        f"review.csv covers only {frac:.1%} of dev rows; need >= 30%"
