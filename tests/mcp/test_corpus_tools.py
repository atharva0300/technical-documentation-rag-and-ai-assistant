import json
import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.models import Base, Chunk, Document
from app.ingestion.code_detection import detect_code
from app.ingestion import pipeline
from app.ingestion.chunker import TokenAwareChunker
from app.ingestion.pipeline import ingest_document
from mcp_server.tools import graph_tools, navigation

DOC_TEXT = """# 7.13 Environment abstraction

The Environment is an interface integrated in the container that models profiles and properties.

# 7.14 Registering a LoadTimeWeaver

The LoadTimeWeaver is used by Spring to dynamically transform classes as they are loaded into the JVM.
To enable load-time weaving add the @EnableLoadTimeWeaving annotation to one of your configuration classes:

```java
@Configuration
@EnableLoadTimeWeaving
public class AppConfig {
}
```

# 9.8 Spring Validation

DataBinder binds request parameters to a target object and runs validators on it.
"""


@pytest.fixture
def corpus(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(navigation, "SessionLocal", Session)
    monkeypatch.setattr(graph_tools, "SessionLocal", Session)
    # Small chunks so sections span several chunks and some headings start mid-chunk,
    # as they do with real documents.
    monkeypatch.setattr(pipeline, "_chunker", TokenAwareChunker(max_tokens=40, overlap_tokens=0))

    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as tmp:
        tmp.write(DOC_TEXT)
        path = tmp.name
    db = Session()
    try:
        ingest_document(db, path)
        doc = db.query(Document).first()
        yield {"session": Session, "document_id": doc.document_id, "title": doc.title}
    finally:
        db.close()
        os.remove(path)


def test_detect_code_finds_unfenced_pdf_code_but_not_inline_mentions():
    pdf_code = ("To enable load-time weaving add the @EnableLoadTimeWeaving to one of your @Configuration\n"
                "classes:\n@Configuration\n@EnableLoadTimeWeaving\npublic class AppConfig {\n}\n"
                "<beans>\n <context:load-time-weaver/>\n</beans>")
    prose = ("To enable load-time weaving add the @EnableLoadTimeWeaving to one of your @Configuration "
             "classes. Any bean may implement LoadTimeWeaverAware; see the <beans> element docs.")
    assert detect_code(pdf_code) == (True, ["java", "xml"])
    assert detect_code(prose) == (False, [])
    assert detect_code("```python\nprint('x')\n```") == (True, ["python"])


def test_list_documents(corpus):
    result = json.loads(navigation.tool_list_documents())
    assert [d["document_id"] for d in result["documents"]] == [corpus["document_id"]]
    assert corpus["document_id"] in result["passages"][0]["content"]


def test_table_of_contents_lists_headings_and_filters(corpus):
    result = json.loads(navigation.tool_get_table_of_contents(corpus["document_id"]))
    headings = [e["heading"] for e in result["entries"]]
    assert headings == ["7.13 Environment abstraction", "7.14 Registering a LoadTimeWeaver", "9.8 Spring Validation"]

    narrowed = json.loads(navigation.tool_get_table_of_contents(corpus["document_id"], under="7.14"))
    assert [e["heading"] for e in narrowed["entries"]] == ["7.14 Registering a LoadTimeWeaver"]
    assert "7.14 Registering a LoadTimeWeaver" in narrowed["passages"][0]["content"]


def test_table_of_contents_unknown_document(corpus):
    assert "error" in json.loads(navigation.tool_get_table_of_contents("nope"))


def test_get_section_by_heading_returns_the_whole_section(corpus):
    result = json.loads(navigation.tool_get_section(corpus["document_id"], section="7.14 Registering a LoadTimeWeaver"))
    text = "\n".join(p["content"] for p in result["passages"])
    # Exactly the section: from its heading to where the next one starts, even though
    # its tail sits in a chunk labelled with the next section.
    assert text.startswith("# 7.14 Registering a LoadTimeWeaver")
    assert "@EnableLoadTimeWeaving" in text and "public class AppConfig" in text
    assert "Environment abstraction" not in text
    assert "DataBinder" not in text and "9.8 Spring Validation" not in text


def test_get_section_by_chunk_returns_neighbours_in_reading_order(corpus):
    db = corpus["session"]()
    chunk = db.query(Chunk).filter(Chunk.section.like("7.14%")).first()
    db.close()
    result = json.loads(navigation.tool_get_section(corpus["document_id"], chunk_id=chunk.id, window=1))
    indexes = [p["chunk_index"] for p in result["passages"]]
    assert indexes == sorted(indexes) and chunk.chunk_index in indexes


def test_get_section_needs_a_target(corpus):
    assert "error" in json.loads(navigation.tool_get_section(corpus["document_id"]))


def test_ingestion_records_code_languages(corpus):
    db = corpus["session"]()
    metas = [json.loads(c.metadata_json) for c in db.query(Chunk).all()]
    db.close()
    assert any(m["has_code"] and m["code_languages"] == ["java"] for m in metas)


def test_survey_topic_without_a_knowledge_graph(corpus):
    result = json.loads(graph_tools.tool_survey_topic("validation"))
    assert result["passages"] == []
    assert "search_documentation" in result["error"]
