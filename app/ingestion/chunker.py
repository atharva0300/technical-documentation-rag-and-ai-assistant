import re
import logging
from typing import List, Dict, Any, Optional
from app.config import settings
from app.embeddings.model import EMBEDDING_MODEL_NAME

logger = logging.getLogger(__name__)

_tokenizer = None


def get_model_tokenizer():
    """Lazily load the real embedding-model tokenizer so chunk sizes are measured
    the same way the embedding model (EMBEDDING_MODEL_NAME) will see them."""
    global _tokenizer
    if _tokenizer is None:
        try:
            from transformers import AutoTokenizer
            _tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL_NAME)
        except Exception as e:
            logger.debug(f"Transformers AutoTokenizer unavailable, using word-count fallback: {e}")
            _tokenizer = False
    return _tokenizer if _tokenizer is not False else None


class TokenAwareChunker:
    """
    Token-aware, structure-preserving chunker for technical documentation.
    Uses the real embedding model's own tokenizer to guarantee chunks fit within
    its sequence limit, instead of approximating size in words.
    """

    def __init__(self, max_tokens: Optional[int] = None, overlap_tokens: Optional[int] = None):
        self.max_tokens = max_tokens if max_tokens is not None else settings.MAX_CHUNK_SIZE
        self.overlap_tokens = overlap_tokens if overlap_tokens is not None else settings.CHUNK_OVERLAP
        self.tokenizer = get_model_tokenizer()

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self.tokenizer:
            try:
                return len(self.tokenizer.encode(text, add_special_tokens=True))
            except Exception:
                pass
        # Fallback when the real tokenizer can't be loaded: ~1 token per word for technical markdown.
        return max(1, len(text.split()))

    def chunk_text(self, text: str, source_name: str = "") -> List[Dict[str, Any]]:
        if not text or not text.strip():
            return []

        lines = text.splitlines(keepends=True)
        chunks: List[Dict[str, Any]] = []

        current_lines: List[str] = []
        current_tokens = 0
        current_section = "Overview"
        in_code_fence = False
        fence_char = ""

        for line in lines:
            header_match = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
            if header_match and not in_code_fence:
                current_section = header_match.group(2).strip()

            fence_match = re.match(r'^(```|~~~)', line.strip())
            if fence_match:
                if not in_code_fence:
                    in_code_fence = True
                    fence_char = fence_match.group(1)
                elif fence_match.group(1) == fence_char:
                    in_code_fence = False
                    fence_char = ""

            line_tokens = self._estimate_tokens(line)

            if current_tokens + line_tokens > self.max_tokens and current_lines and not in_code_fence:
                chunk_content = "".join(current_lines).strip()
                chunks.append({
                    "content": chunk_content,
                    "section": current_section,
                    "token_count": self._estimate_tokens(chunk_content),
                    "has_code": "```" in chunk_content or "~~~" in chunk_content
                })

                overlap_lines = []
                overlap_toks = 0
                for prev_line in reversed(current_lines):
                    pt_toks = self._estimate_tokens(prev_line)
                    if overlap_toks + pt_toks > self.overlap_tokens:
                        break
                    overlap_lines.insert(0, prev_line)
                    overlap_toks += pt_toks

                current_lines = overlap_lines
                current_tokens = overlap_toks

            current_lines.append(line)
            current_tokens += line_tokens

        if current_lines:
            chunk_content = "".join(current_lines).strip()
            if chunk_content:
                chunks.append({
                    "content": chunk_content,
                    "section": current_section,
                    "token_count": self._estimate_tokens(chunk_content),
                    "has_code": "```" in chunk_content or "~~~" in chunk_content
                })

        return chunks
