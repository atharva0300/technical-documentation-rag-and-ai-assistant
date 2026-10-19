"""
tests/test_no_autoverification.py

Structural safeguard: prevents scripts or agents from manufacturing verified rows.

RULES (permanent, cannot be bypassed by prompts or instructions):
  1. A row with status="verified" MUST have a non-empty verified_by that does NOT
     match the banned-pattern (auto|bot|agent|script|ai|llm|system|test|antigravity).
  2. A reference_answer may not be a copy-paste of the passage_text (>90% character
     overlap with a prefix of passage_text). This blocks the "paste the passage"
     shortcut used in the fabrication incident.

Run as part of the standard dataset gate:
  pytest tests/test_no_autoverification.py tests/test_dataset_validation.py -v
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
EVAL_DIR  = REPO_ROOT / "data" / "evaluation"

# Exact same pattern used in split_dataset.py and test_dataset_validation.py.
BANNED_VERIFIER_RE = re.compile(
    r"^(auto|bot|agent|script|ai|llm|system|test|antigravity)$",
    re.IGNORECASE,
)
PASSAGE_OVERLAP_THRESHOLD = 0.90


def load_all_rows() -> list[dict]:
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


def char_overlap_with_prefix(answer: str, passage: str) -> float:
    """
    Measure how much of `answer` is covered by a prefix of `passage`.
    Returns the fraction of answer characters that appear in passage[:len(answer)+50].
    A score > 0.90 indicates copy-paste.
    """
    if not answer or not passage:
        return 0.0
    prefix = passage[:len(answer) + 50]
    # Count matching characters (simple: how many chars in answer also appear in prefix)
    answer_chars = set(answer.lower())
    prefix_chars = set(prefix.lower())
    if not answer_chars:
        return 0.0
    shared = len(answer_chars & prefix_chars) / len(answer_chars)
    # Also check direct substring containment (catches exact copies)
    if answer.strip().lower() in passage.lower():
        return 1.0
    return shared


@pytest.fixture(scope="module")
def all_rows():
    return load_all_rows()


@pytest.fixture(scope="module")
def verified_rows(all_rows):
    """Rows that claim to be verified (status=verified), for testing their legitimacy."""
    return [r for r in all_rows if r.get("status") == "verified"]


# ---------------------------------------------------------------------------
# Test 1: Every verified row must have a non-empty, non-banned verified_by
# ---------------------------------------------------------------------------

def test_no_verified_without_human_verifier(verified_rows):
    """
    STRUCTURAL SAFEGUARD: Any row with status='verified' must have a verified_by
    field that is non-empty and does not match the banned-pattern. Scripts may
    never satisfy this check — only a human filling in their real name/initials can.
    """
    offenders = []
    for r in verified_rows:
        vby = (r.get("verified_by") or "").strip()
        if not vby:
            offenders.append((r.get("id"), "verified_by is empty"))
        elif BANNED_VERIFIER_RE.match(vby):
            offenders.append((r.get("id"), f"verified_by={vby!r} matches banned pattern"))

    assert not offenders, (
        f"STRUCTURAL SAFEGUARD FAILED: {len(offenders)} verified row(s) lack a legitimate "
        f"human verified_by. A script or agent set status='verified' without a real "
        f"reviewer name. Offending rows (id, reason):\n"
        + "\n".join(f"  {rid}: {reason}" for rid, reason in offenders[:20])
    )


# ---------------------------------------------------------------------------
# Test 2: reference_answer must not be a copy-paste of passage_text
# ---------------------------------------------------------------------------

def test_no_passage_copy_paste_in_answers(verified_rows):
    """
    STRUCTURAL SAFEGUARD: Blocks the fabrication technique of copying passage_text
    directly into reference_answer to defeat grounding checks (as seen in the
    et001/cp002 incident). reference_answer must be an original summary, not
    a transcript of the source passage.
    """
    offenders = []
    for r in verified_rows:
        answer  = (r.get("reference_answer") or "").strip()
        passage = (r.get("passage_text") or "").strip()
        if not answer or not passage:
            continue
        overlap = char_overlap_with_prefix(answer, passage)
        if overlap > PASSAGE_OVERLAP_THRESHOLD:
            offenders.append((r.get("id"), overlap))

    assert not offenders, (
        f"STRUCTURAL SAFEGUARD FAILED: {len(offenders)} verified row(s) have a "
        f"reference_answer that is a near-copy (>{PASSAGE_OVERLAP_THRESHOLD:.0%} overlap) "
        f"of its passage_text. This defeats grounding validation. "
        f"Reference answers must be original summaries.\n"
        "Offending rows (id, overlap):\n"
        + "\n".join(f"  {rid}: {overlap:.2%}" for rid, overlap in offenders[:20])
    )


# ---------------------------------------------------------------------------
# Test 3: No row at all (not just verified) may have a banned verified_by
# ---------------------------------------------------------------------------

def test_no_banned_verifier_anywhere(all_rows):
    """
    Even rows with status='needs_review' or 'drafted' must not have a banned
    verified_by. Scripts must always write verified_by="" (empty).
    """
    offenders = []
    for r in all_rows:
        vby = (r.get("verified_by") or "").strip()
        if vby and BANNED_VERIFIER_RE.match(vby):
            offenders.append((r.get("id"), vby))

    assert not offenders, (
        f"{len(offenders)} row(s) have a banned verified_by value set by a script. "
        f"Scripts must always write verified_by='' (empty string). Offenders:\n"
        + "\n".join(f"  {rid}: {repr(vby)}" for rid, vby in offenders[:20])
    )
