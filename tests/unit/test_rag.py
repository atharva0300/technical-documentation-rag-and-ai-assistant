import pytest
from app.retrieval.bm25 import _tokenize
from app.ingestion.chunker import TokenAwareChunker

def test_technical_tokenizer_preserves_flags_and_versions():
    tokens = _tokenize("Run kubectl with --memory 512Mi for version v1.2.3 and restartPolicy")
    assert "--memory" in tokens
    assert "v1.2.3" in tokens
    assert "kubectl" in tokens

def test_token_aware_chunker_code_fence():
    chunker = TokenAwareChunker(max_tokens=50, overlap_tokens=10)
    code_doc = """# Introduction

Here is some text.

```python
def calculate_matrix():
    return [1, 2, 3]
```

More text after code.
"""
    chunks = chunker.chunk_text(code_doc)
    assert len(chunks) >= 1
    # Check that code block was preserved
    has_code_chunks = [c for c in chunks if "def calculate_matrix" in c["content"]]
    assert len(has_code_chunks) == 1
    assert "```python" in has_code_chunks[0]["content"]
    assert "```" in has_code_chunks[0]["content"]
