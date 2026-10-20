from pydantic import BaseModel
from typing import List, Optional, Union, Dict, Any


class IngestResponse(BaseModel):
    status: str
    message: str
    document_id: Optional[Union[str, int]] = None
    content_hash: Optional[str] = None
    chunks_created: Optional[int] = 0


class SyncResponse(BaseModel):
    status: str
    ingested_count: int
    updated_count: int
    skipped_count: int
    deleted_count: int
    deleted_sources: List[str]
    knowledge_graph: Optional[Dict[str, Any]] = None


class GraphRebuildResponse(BaseModel):
    status: str
    communities: Optional[int] = None
    chunks_processed: Optional[int] = None
    edges: Optional[int] = None
    reason: Optional[str] = None


class DocumentResponse(BaseModel):
    document_id: Union[str, int]
    title: Optional[str]
    source: Optional[str]
    content_hash: Optional[str] = None
    chunks_count: int
