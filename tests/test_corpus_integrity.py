"""
tests/test_corpus_integrity.py

Hard-fail corpus integrity tests (R1.8).

Run:  pytest tests/test_corpus_integrity.py -v
Also: python scripts/fetch_corpus.py --check
"""
import hashlib
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DOCS_DIR  = REPO_ROOT / "data" / "documents"
MANIFEST  = DOCS_DIR / "MANIFEST.json"


def _load_manifest() -> tuple[dict, list[dict]]:
    assert MANIFEST.exists(), "MANIFEST.json missing — run fetch_corpus.py"
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return data, data.get("files", [])


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# --------------------------------------------------------------------------- #

def test_manifest_exists():
    """MANIFEST.json must exist."""
    assert MANIFEST.exists(), "MANIFEST.json not found — run fetch_corpus.py"


def test_manifest_has_files():
    """MANIFEST.json must have a non-empty 'files' list."""
    _, entries = _load_manifest()
    assert len(entries) >= 1, "MANIFEST.json 'files' list is empty"


def test_minimum_document_count():
    """Corpus must have at least 40 documents."""
    _, entries = _load_manifest()
    assert len(entries) >= 40, f"Only {len(entries)} documents; need >= 40"


def test_no_duplicate_content_hashes():
    """No two documents may have the same SHA-256 (exact duplicates)."""
    _, entries = _load_manifest()
    hashes = [e["sha256"] for e in entries]
    seen: set[str] = set()
    dups: list[str] = []
    for h in hashes:
        if h in seen:
            dups.append(h[:16])
        seen.add(h)
    assert not dups, f"Duplicate content hashes found: {dups}"


@pytest.mark.parametrize("field", [
    "path", "source_url", "commit_sha", "license", "sha256", "word_count", "product", "fetched_at"
])
def test_all_required_fields_present(field: str):
    """Every manifest entry must have all required non-empty fields."""
    _, entries = _load_manifest()
    missing = [e.get("path", "?") for e in entries if not e.get(field)]
    assert not missing, f"Field '{field}' missing/empty in entries: {missing}"


def test_commit_sha_exactly_40_hex():
    """Every commit_sha must be exactly 40 lowercase hex characters."""
    _, entries = _load_manifest()
    bad = [
        (e["path"], e.get("commit_sha", ""))
        for e in entries
        if not re.fullmatch(r"[0-9a-f]{40}", e.get("commit_sha", ""))
    ]
    assert not bad, f"Invalid commit_sha entries: {bad}"


def test_source_url_contains_commit_sha():
    """source_url must contain the full commit SHA (i.e., is pinned)."""
    _, entries = _load_manifest()
    bad = [
        (e["path"], e["commit_sha"][:8])
        for e in entries
        if e.get("commit_sha", "") not in e.get("source_url", "")
    ]
    assert not bad, f"source_url not pinned to commit SHA: {bad}"


def test_all_manifest_files_exist_on_disk():
    """Every path in MANIFEST.json must exist on disk."""
    _, entries = _load_manifest()
    missing = [
        e["path"] for e in entries
        if not (DOCS_DIR / e["path"]).exists()
    ]
    assert not missing, f"Manifest files missing on disk: {missing}"


def test_no_disk_files_missing_from_manifest():
    """Every .md file in DOCS_DIR (except README.md) must appear in MANIFEST.json."""
    _, entries = _load_manifest()
    manifest_paths = {e["path"] for e in entries}
    orphans = []
    for f in DOCS_DIR.rglob("*.md"):
        rel = f.relative_to(DOCS_DIR).as_posix()
        if f.name == "README.md" and f.parent == DOCS_DIR:
            continue
        if rel not in manifest_paths:
            orphans.append(rel)
    assert not orphans, f"Files on disk not in manifest: {orphans}"


def test_no_empty_files():
    """No corpus file may be empty."""
    _, entries = _load_manifest()
    empty = [
        e["path"] for e in entries
        if not (DOCS_DIR / e["path"]).read_bytes()
    ]
    assert not empty, f"Empty files: {empty}"


def test_word_count_minimum():
    """Every document must have >= 300 words."""
    _, entries = _load_manifest()
    below = [
        (e["path"], e["word_count"])
        for e in entries
        if e.get("word_count", 0) < 300
    ]
    assert not below, f"Documents below 300 words: {below}"


def test_sha256_matches_disk():
    """Stored SHA-256 must match the actual file content."""
    _, entries = _load_manifest()
    mismatches = []
    for e in entries:
        p = DOCS_DIR / e["path"]
        if not p.exists():
            continue
        actual = _sha256(p.read_bytes())
        if actual != e.get("sha256", ""):
            mismatches.append(e["path"])
    assert not mismatches, f"SHA-256 mismatches: {mismatches}"


def test_no_stale_numbered_duplicates():
    """No files matching *_1.md or *_2.md may remain."""
    bad = list(DOCS_DIR.rglob("*_[0-9].md"))
    assert not bad, f"Stale numbered duplicate files: {[str(f) for f in bad]}"


def test_no_link_list_or_offtopic_files():
    """Specific known off-topic files must not exist."""
    bad_patterns = [
        "**/awesome-postgres*",
        "**/additional/**",
    ]
    found = []
    for pat in bad_patterns:
        found.extend(DOCS_DIR.glob(pat))
    assert not found, f"Off-topic/link-list files found: {[str(f) for f in found]}"


def test_manifest_word_counts_match_actual():
    """Manifest word_count must match actual word count (within 5% tolerance for
    conversion differences). Hard fail if ANY is off by more than 10%."""
    _, entries = _load_manifest()
    bad = []
    for e in entries:
        p = DOCS_DIR / e["path"]
        if not p.exists():
            continue
        actual_wc = len(p.read_text(encoding="utf-8", errors="replace").split())
        stored_wc = e.get("word_count", 0)
        if stored_wc == 0 or abs(actual_wc - stored_wc) / max(stored_wc, 1) > 0.10:
            bad.append((e["path"], stored_wc, actual_wc))
    assert not bad, f"word_count mismatches (>10%): {bad}"


def test_corpus_covers_required_products():
    """Corpus must have at least 2 documents for each required product."""
    _, entries = _load_manifest()
    required = {"kubernetes", "docker", "fastapi", "sqlalchemy", "postgresql"}
    by_product: dict[str, int] = {}
    for e in entries:
        by_product[e["product"]] = by_product.get(e["product"], 0) + 1
    for prod in required:
        cnt = by_product.get(prod, 0)
        assert cnt >= 2, f"Product {prod!r} has only {cnt} documents (need >= 2)"
