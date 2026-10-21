import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from app.config import settings
from app.embeddings.model import EMBEDDING_MODEL_NAME
from app.ingestion.code_detection import detect_code

logger = logging.getLogger(__name__)

_tokenizer = None

_PAGE_MARKER_REGEX = re.compile(r'^##\s+Page\s+(\d+)\s*$', re.MULTILINE)
_MIN_CHUNK_CHARS = 20


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
        # (section, page) in effect at each line of current_lines, so a chunk is labelled
        # by where it actually starts rather than by whatever heading the next line opens.
        current_states: List[Tuple[Optional[str], Optional[int]]] = []
        current_tokens = 0
        current_section = None
        current_page = None
        in_code_fence = False
        fence_char = ""

        for line in lines:
            page_match = _PAGE_MARKER_REGEX.match(line.strip())
            header_match = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
            if page_match and not in_code_fence:
                # "## Page N" markers come from PDF extraction (app/ingestion/loader.py):
                # they say where a chunk is, not what it's about, so they set the page and
                # leave the current section heading alone.
                current_page = int(page_match.group(1))
            elif header_match and not in_code_fence:
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
                self._append_chunk(chunks, current_lines, current_states)

                overlap_lines = []
                overlap_states = []
                overlap_toks = 0
                for prev_line, prev_state in zip(reversed(current_lines), reversed(current_states)):
                    pt_toks = self._estimate_tokens(prev_line)
                    if overlap_toks + pt_toks > self.overlap_tokens:
                        break
                    overlap_lines.insert(0, prev_line)
                    overlap_states.insert(0, prev_state)
                    overlap_toks += pt_toks

                current_lines = overlap_lines
                current_states = overlap_states
                current_tokens = overlap_toks

            current_lines.append(line)
            current_states.append((current_section, current_page))
            current_tokens += line_tokens

        if current_lines:
            self._append_chunk(chunks, current_lines, current_states)

        return chunks

    def _append_chunk(
        self,
        chunks: List[Dict[str, Any]],
        lines: List[str],
        states: List[Tuple[Optional[str], Optional[int]]],
    ) -> None:
        chunk_content = "".join(lines).strip()
        # Skip chunks with no real text, e.g. pages left empty once table-of-contents
        # lines are dropped (only page markers / headings remain).
        body = _PAGE_MARKER_REGEX.sub("", chunk_content)
        if len(re.sub(r'[#\s]', "", body)) < _MIN_CHUNK_CHARS:
            return
        # Label by the first section heading the chunk itself opens, if any; otherwise by
        # the section it continues from.
        section, page = states[0]
        for line_section, _ in states:
            if line_section != states[0][0]:
                section = line_section
                break
        has_code, code_languages = detect_code(chunk_content)
        chunks.append({
            "content": chunk_content,
            "section": _section_label(section, page),
            "page": page,
            "token_count": self._estimate_tokens(chunk_content),
            "has_code": has_code,
            "code_languages": code_languages,
        })


def _section_label(section: Optional[str], page: Optional[int]) -> str:
    """Human-readable location for citations: "7.14 Registering a LoadTimeWeaver · p. 170"
    for PDFs, the markdown heading for markdown docs."""
    if page is None:
        return section or "Overview"
    return f"{section} · p. {page}" if section else f"Page {page}"
