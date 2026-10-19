import os
import tempfile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database.models import Base, Document, Chunk
from app.ingestion.loader import load_file, compute_sha256
from app.ingestion.pipeline import ingest_document, sync_directory

TEST_DATABASE_URL = "sqlite:///:memory:"

def setup_test_db():
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()

def test_compute_sha256():
    c1 = "Hello World"
    c2 = "Hello World"
    c3 = "Hello World!"
    assert compute_sha256(c1) == compute_sha256(c2)
    assert compute_sha256(c1) != compute_sha256(c3)

def test_idempotent_ingestion():
    db = setup_test_db()
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w+", delete=False) as tmp:
        tmp.write("# Test Doc\n\nThis is test content.")
        tmp_path = tmp.name

    try:
        # First Ingestion
        res1 = ingest_document(db, tmp_path)
        assert res1["status"] == "success"
        assert res1["chunks_created"] == 1

        # Second Ingestion (same content)
        res2 = ingest_document(db, tmp_path)
        assert res2["status"] == "skipped"

        # Update Content
        with open(tmp_path, "w") as f:
            f.write("# Test Doc Updated\n\nNew updated content for test.")

        # Third Ingestion (modified content)
        res3 = ingest_document(db, tmp_path)
        assert res3["status"] == "updated"

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
