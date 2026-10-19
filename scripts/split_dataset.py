#!/usr/bin/env python3
"""
Deterministic stratified 60/40 split of verified dataset rows.

Rules:
- Only rows with status == "verified" participate.
- Split is 60% dev / 40% held_out.
- Stratified by category.
- Deterministic: SEED=42.
- Requires 0 category to drop out entirely (each category must appear in both splits where N>=2).
- Writes dev.jsonl, held_out.jsonl, HELD_OUT_HASH.txt.
- Generates review.csv with 100% of held_out + >= 30% of dev.
- Hard-fails if verified count < 120.
- Hard-fails if any split has 0 rows from a category that has >= 2 verified rows.
"""

import csv
import hashlib
import json
import random
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
EVAL_DIR  = REPO_ROOT / "data" / "evaluation"
SEED = 42

CATEGORY_MINIMUMS = {
    "single_lookup":  48,
    "multi_hop":      24,
    "comparison":     16,
    "exact_term":     12,
    "unanswerable":   12,
    "followup":        8,
}
TOTAL_MINIMUM = 120

# Banned auto-verifier names — verified_by must NOT match this pattern
_BANNED_VERIFIER_RE = re.compile(
    r"^(auto|bot|agent|script|ai|llm|system|test|antigravity)$",
    re.IGNORECASE,
)


def _is_human_verified(row: dict) -> bool:
    """A row counts as verified only if status==verified AND verified_by is a
    non-empty string that does not match the banned-pattern."""
    if row.get("status") != "verified":
        return False
    vby = (row.get("verified_by") or "").strip()
    if not vby:
        return False
    if _BANNED_VERIFIER_RE.match(vby):
        return False
    return True


def load_verified(candidates_path: Path) -> list[dict]:
    rows = []
    skipped_no_verifier = 0
    with open(candidates_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("status") == "verified":
                if _is_human_verified(row):
                    rows.append(row)
                else:
                    skipped_no_verifier += 1
    if skipped_no_verifier:
        print(
            f"WARNING: {skipped_no_verifier} row(s) have status='verified' but missing or "
            f"banned verified_by — they are NOT counted as verified. "
            f"A human must fill in their real name/initials in verified_by."
        )
    return rows


def stratified_split(
    rows: list[dict], dev_frac: float = 0.60, rng: random.Random = None
) -> tuple[list[dict], list[dict]]:
    """Split rows into (dev, held_out) stratified by category."""
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)

    dev, held_out = [], []
    for cat, cat_rows in by_cat.items():
        rng.shuffle(cat_rows)
        n_dev = max(1, round(len(cat_rows) * dev_frac))
        if len(cat_rows) == 1:
            dev.extend(cat_rows)
        else:
            dev.extend(cat_rows[:n_dev])
            held_out.extend(cat_rows[n_dev:])

    return dev, held_out


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_jsonl(rows: list[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_review_csv(dev: list[dict], held_out: list[dict], path: Path, dev_sample_frac: float = 0.30) -> None:
    rng = random.Random(SEED + 1)
    n_dev_sample = max(1, round(len(dev) * dev_sample_frac))
    dev_sample = rng.sample(dev, n_dev_sample)

    fieldnames = ["id", "category", "question", "expected_sources", "reference_answer", "split", "verdict"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in held_out:
            writer.writerow({
                "id":               r["id"],
                "category":         r["category"],
                "question":         r.get("question", ""),
                "expected_sources": "; ".join(r.get("expected_sources") or []),
                "reference_answer": r.get("reference_answer", ""),
                "split":            "held_out",
                "verdict":          "needs_review",
            })
        for r in dev_sample:
            writer.writerow({
                "id":               r["id"],
                "category":         r["category"],
                "question":         r.get("question", ""),
                "expected_sources": "; ".join(r.get("expected_sources") or []),
                "reference_answer": r.get("reference_answer", ""),
                "split":            "dev",
                "verdict":          "needs_review",
            })


def main() -> None:
    rng = random.Random(SEED)

    candidates_path = EVAL_DIR / "candidates.jsonl"
    if not candidates_path.exists():
        print(f"FATAL: {candidates_path} not found. Run generate_candidates.py first.")
        sys.exit(1)

    verified = load_verified(candidates_path)
    total = len(verified)
    print(f"Verified rows: {total}")

    # Count by category
    by_cat: dict[str, int] = {}
    for r in verified:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    for cat, cnt in sorted(by_cat.items()):
        minimum = CATEGORY_MINIMUMS.get(cat, 0)
        status = "OK" if cnt >= minimum else f"FAIL (need >= {minimum})"
        print(f"  {cat}: {cnt}  {status}")

    # Hard fails
    errors = []
    if total < TOTAL_MINIMUM:
        errors.append(f"Verified count {total} < {TOTAL_MINIMUM}")
    for cat, minimum in CATEGORY_MINIMUMS.items():
        cnt = by_cat.get(cat, 0)
        if cnt < minimum:
            errors.append(f"Category {cat}: {cnt} < {minimum}")

    if errors:
        print("\nFATAL split gate failures:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    # Delete old split files
    for fname in ["dev.jsonl", "held_out.jsonl", "HELD_OUT_HASH.txt", "review.csv"]:
        p = EVAL_DIR / fname
        if p.exists():
            p.unlink()
            print(f"Deleted old {fname}")

    dev, held_out = stratified_split(verified, dev_frac=0.60, rng=rng)

    # Verify ratio
    actual_dev_frac = len(dev) / (len(dev) + len(held_out))
    if not (0.50 <= actual_dev_frac <= 0.70):
        print(f"FATAL: Dev fraction {actual_dev_frac:.2f} is outside [0.50, 0.70]")
        sys.exit(1)

    # Verify category representation in both splits
    dev_cats  = {r["category"] for r in dev}
    hold_cats = {r["category"] for r in held_out}
    for cat, cnt in by_cat.items():
        if cnt >= 2:
            if cat not in dev_cats:
                print(f"FATAL: Category {cat!r} has {cnt} verified rows but none in dev split")
                sys.exit(1)
            if cat not in hold_cats:
                print(f"FATAL: Category {cat!r} has {cnt} verified rows but none in held_out split")
                sys.exit(1)

    dev_path     = EVAL_DIR / "dev.jsonl"
    held_path    = EVAL_DIR / "held_out.jsonl"
    hash_path    = EVAL_DIR / "HELD_OUT_HASH.txt"
    review_path  = EVAL_DIR / "review.csv"

    write_jsonl(dev, dev_path)
    write_jsonl(held_out, held_path)

    held_sha = sha256_file(held_path)
    hash_path.write_text(held_sha + "\n", encoding="utf-8")

    write_review_csv(dev, held_out, review_path)

    print(f"\nSplit complete:")
    print(f"  dev:      {len(dev)} rows → {dev_path}")
    print(f"  held_out: {len(held_out)} rows → {held_path}")
    print(f"  held_out SHA-256: {held_sha}")
    print(f"  review.csv: {review_path}")


if __name__ == "__main__":
    main()
