from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    file_path: str = Field(..., description="Path to the local markdown or text file to ingest")


class SyncRequest(BaseModel):
    directory_path: str = Field("data/documents", description="Directory path containing documentation to synchronize")
