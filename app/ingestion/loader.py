import os
import hashlib
import logging
from pypdf import PdfReader

logger = logging.getLogger(__name__)

def compute_sha256(content: str) -> str:
    """Compute SHA-256 hash of document content."""
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()

def generate_document_id(source: str, content: str) -> str:
    """Generate a stable document ID based on source path and content hash."""
    hasher = hashlib.sha256()
    hasher.update(source.encode("utf-8", errors="replace"))
    hasher.update(content.encode("utf-8", errors="replace"))
    return hasher.hexdigest()[:16]

def _read_pdf_file(file_path: str) -> str:
    """Extract text from PDF page by page."""
    try:
        reader = PdfReader(file_path)
        pages_text = []
        for idx, page in enumerate(reader.pages, start=1):
            text = page.extract_text()
            if text and text.strip():
                pages_text.append(f"## Page {idx}\n{text.strip()}")
        return "\n\n".join(pages_text)
    except Exception as e:
        logger.error(f"Error extracting PDF text from {file_path}: {e}")
        raise ValueError(f"Could not extract text from PDF document: {e}")

def load_file(file_path: str) -> dict:
    """
    Load a file (Markdown, TXT, PDF, etc.) and return content, SHA-256 hash, and extracted title.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    
    filename = os.path.basename(file_path)
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".pdf":
        content = _read_pdf_file(file_path)
    else:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

    if not content or not content.strip():
        raise ValueError(f"File {filename} is empty or no text could be extracted.")

    title = os.path.splitext(filename)[0].replace("-", " ").replace("_", " ").title()
    content_hash = compute_sha256(content)
    doc_id = generate_document_id(file_path, content)

    return {
        "document_id": doc_id,
        "title": title,
        "source": os.path.abspath(file_path),
        "content": content,
        "content_hash": content_hash
    }
