from app.ingestion.chunker import TokenAwareChunker


def test_chunker_preserves_heading_section():
    text = "# Section 1\nContent under section 1."
    chunks = TokenAwareChunker(max_tokens=50, overlap_tokens=0).chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0]["section"] == "Section 1"
    assert "Content under section 1." in chunks[0]["content"]


def test_chunker_splits_on_token_budget():
    text = "word1 word2 word3 word4 word5\nword6 word7 word8 word9 word10\n"
    chunks = TokenAwareChunker(max_tokens=6, overlap_tokens=2).chunk_text(text)
    assert len(chunks) >= 2


def test_chunker_preserves_code_block():
    text = "# Section\nIntro text\n```python\ndef hello():\n    print('world')\n```\nOutro text"
    chunks = TokenAwareChunker(max_tokens=50, overlap_tokens=0).chunk_text(text)
    joined = " ".join(c["content"] for c in chunks)
    assert "def hello():" in joined
    assert any(c["has_code"] for c in chunks)


def test_chunker_empty_input():
    assert TokenAwareChunker().chunk_text("") == []
