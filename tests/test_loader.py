import os
import tempfile
from app.ingestion.loader import load_file, generate_document_id

def test_generate_document_id():
    id1 = generate_document_id("file1.md", "content1")
    id2 = generate_document_id("file1.md", "content1")
    id3 = generate_document_id("file2.md", "content1")
    id4 = generate_document_id("file1.md", "content2")
    
    assert id1 == id2
    assert id1 != id3
    assert id1 != id4

def test_load_file_not_found():
    try:
        load_file("nonexistent_file.md")
        assert False, "Should have raised FileNotFoundError"
    except FileNotFoundError:
        pass

def test_load_non_utf8_fallback():
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="wb", delete=False) as tmp:
        # Write bytes including invalid UTF-8 byte 0xe4
        tmp.write(b"Hello \xe4 World documentation text.")
        tmp_path = tmp.name

    try:
        doc = load_file(tmp_path)
        assert "Hello" in doc["content"]
        assert "World" in doc["content"]
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
