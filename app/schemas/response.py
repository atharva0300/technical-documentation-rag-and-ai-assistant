from pydantic import BaseModel
from typing import List, Optional, Union


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


class DocumentResponse(BaseModel):
    document_id: Union[str, int]
    title: Optional[str]
    source: Optional[str]
    content_hash: Optional[str] = None
    chunks_count: int
