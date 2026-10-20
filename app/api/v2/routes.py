import os
import json
import uuid
import asyncio
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Header, Query, UploadFile, File, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import get_db
from app.database.models import Document, Conversation, Message
from app.ingestion.pipeline import ingest_document, sync_directory
from app.agents.graph import run_v2_agent_workflow_async
from app.graph.community_builder import rebuild_knowledge_graph
from app.schemas.query import IngestRequest, SyncRequest
from app.schemas.response import IngestResponse, SyncResponse, DocumentResponse, GraphRebuildResponse

router = APIRouter(prefix="/api/v2", tags=["v2-agent"])

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "data", "documents", "uploads")


def verify_api_key(x_api_key: Optional[str] = Header(None)):
    expected = getattr(settings, "API_KEY", "dev-api-key")
    if not x_api_key or x_api_key.strip() != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    return x_api_key


class AgentQueryRequest(BaseModel):
    query: str
    conversation_id: Optional[str] = None
    filters: Optional[Dict[str, Any]] = None


@router.get("/health")
def health_check():
    return {"status": "ok", "service": "Engineering AI Assistant API", "version": "2.0.0"}


@router.post("/agent/query", dependencies=[Depends(verify_api_key)])
async def api_v2_agent_query(request: AgentQueryRequest, db: Session = Depends(get_db)):
    """
    V2 multi-agent workflow query endpoint (JSON response, no streaming).
    """
    if not request.query or not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    conv_id = request.conversation_id
    if not conv_id or not conv_id.strip() or conv_id == "default":
        conv_id = str(uuid.uuid4())

    try:
        # Awaited directly: this handler already runs on FastAPI's event loop, so there is
        # no reason to go through the synchronous run_v2_agent_workflow() wrapper, which used
        # to spin up a second thread + a nested asyncio.run() just to call back into the same
        # async workflow (see graph.py's run_v2_agent_workflow docstring).
        res = await run_v2_agent_workflow_async(
            query=request.query,
            conversation_id=conv_id,
            filters=request.filters
        )

        # Persist conversation & message history in DB
        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        if not conv:
            conv = Conversation(id=conv_id, title=request.query[:50])
            db.add(conv)
            db.commit()

        user_msg = Message(
            conversation_id=conv_id,
            role="user",
            content=request.query
        )
        asst_msg = Message(
            conversation_id=conv_id,
            role="assistant",
            content=res["answer"],
            sources_json=json.dumps(res.get("sources", [])),
            agent_trace_json=json.dumps(res.get("trace_events", []))
        )
        db.add_all([user_msg, asst_msg])
        db.commit()

        return {
            "status": "success",
            "conversation_id": conv_id,
            "answer": res["answer"],
            "sources": res.get("sources", []),
            "intent": res.get("intent", "single_lookup"),
            "verification_status": res.get("verification_status", "VALID"),
            "trace_events": res.get("trace_events", [])
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent workflow error: {str(e)}")


@router.post("/agent/stream", dependencies=[Depends(verify_api_key)])
async def api_v2_agent_stream(request: AgentQueryRequest, db: Session = Depends(get_db)):
    """
    Real Server-Sent Events (SSE) token streaming endpoint executing through the
    LangGraph multi-agent workflow, with full conversation persistence.

    Streaming contract: `token` events carry the FIRST attempt's answer as it's generated —
    the common case, since the verifier's PARTIAL status already salvages most answers
    without a retry. If (and only if) the verifier rejects that first attempt and the graph
    regenerates, the regenerated attempt is buffered server-side (not streamed token-by-token)
    and delivered as a single `answer_replaced` event once it's verified. A client that
    receives `answer_replaced` MUST discard whatever it rendered from `token` events so far
    and show `answer_replaced.answer` as the message's full text instead — this is the fix for
    the retry-duplicates-streamed-tokens bug the debloat audit flagged (see graph.py's
    answer_node for the generation side of this).
    """
    if not request.query or not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    conv_id = request.conversation_id
    if not conv_id or not conv_id.strip() or conv_id == "default":
        conv_id = str(uuid.uuid4())

    token_queue = asyncio.Queue()

    def stream_callback(token: str):
        token_queue.put_nowait(token)

    async def sse_event_generator():
        try:
            # 1. Resolve/create Conversation and persist User Message
            conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
            if not conv:
                conv = Conversation(id=conv_id, title=request.query[:50])
                db.add(conv)
                db.commit()

            user_msg = Message(
                conversation_id=conv_id,
                role="user",
                content=request.query
            )
            db.add(user_msg)
            db.commit()

            yield f"event: status\ndata: {json.dumps({'status': 'planning'})}\n\n"

            # 2. Execute the supervisor graph workflow asynchronously
            workflow_task = asyncio.create_task(
                run_v2_agent_workflow_async(
                    query=request.query,
                    conversation_id=conv_id,
                    filters=request.filters,
                    stream_callback=stream_callback
                )
            )

            while not workflow_task.done() or not token_queue.empty():
                try:
                    token = await asyncio.wait_for(token_queue.get(), timeout=0.05)
                    yield f"event: token\ndata: {json.dumps({'token': token})}\n\n"
                    token_queue.task_done()
                except asyncio.TimeoutError:
                    pass

            res = await workflow_task

            # 3. Persist Assistant Message upon successful completion
            asst_msg = Message(
                conversation_id=conv_id,
                role="assistant",
                content=res.get("answer", ""),
                sources_json=json.dumps(res.get("sources", [])),
                agent_trace_json=json.dumps(res.get("trace_events", []))
            )
            db.add(asst_msg)
            db.commit()

            # If a retry happened, the token events above only carried the FIRST (rejected)
            # attempt. Replace them with the final, verified answer in one shot.
            if res.get("retry_count", 0) > 0:
                yield f"event: answer_replaced\ndata: {json.dumps({'answer': res.get('answer', ''), 'sources': res.get('sources', [])})}\n\n"

            yield f"event: planner\ndata: {json.dumps({'intent': res.get('intent', 'single_lookup')})}\n\n"
            yield f"event: sources\ndata: {json.dumps({'sources': res.get('sources', [])})}\n\n"
            yield f"event: verification\ndata: {json.dumps({'verification_status': res.get('verification_status', 'VALID'), 'verification_feedback': res.get('verification_feedback')})}\n\n"
            yield f"event: done\ndata: {json.dumps({'status': 'completed', 'conversation_id': conv_id, 'retry_count': res.get('retry_count', 0)})}\n\n"

        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(sse_event_generator(), media_type="text/event-stream")


@router.get("/documents", response_model=List[DocumentResponse], dependencies=[Depends(verify_api_key)])
def get_documents(db: Session = Depends(get_db)):
    docs = db.query(Document).all()
    return [
        DocumentResponse(
            document_id=doc.document_id,
            title=doc.title,
            source=doc.source,
            content_hash=doc.content_hash,
            chunks_count=len(doc.chunks)
        )
        for doc in docs
    ]


@router.post("/documents/ingest", response_model=IngestResponse, dependencies=[Depends(verify_api_key)])
def api_ingest(request: IngestRequest, db: Session = Depends(get_db)):
    try:
        result = ingest_document(db, request.file_path)
        return IngestResponse(**result)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/documents/upload", response_model=IngestResponse, dependencies=[Depends(verify_api_key)])
async def api_upload_document(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Upload and ingest a markdown/text documentation file.

    NOTE: an earlier version of this endpoint had no API-key dependency at all, unlike every
    sibling write endpoint — an unauthenticated arbitrary-file-write/ingest path (flagged as a
    critical mistake in the debloat audit). It now requires the same key as everything else.
    """
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe_filename = os.path.basename(file.filename)
    file_path = os.path.join(UPLOAD_DIR, safe_filename)

    try:
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        result = ingest_document(db, file_path)
        return IngestResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload and ingest document: {str(e)}")


@router.post("/documents/sync", response_model=SyncResponse, dependencies=[Depends(verify_api_key)])
def api_sync(request: SyncRequest, db: Session = Depends(get_db)):
    try:
        result = sync_directory(db, request.directory_path)
        return SyncResponse(**result)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/documents/graph/rebuild", response_model=GraphRebuildResponse, dependencies=[Depends(verify_api_key)])
def api_rebuild_graph(db: Session = Depends(get_db)):
    """
    Manually rebuilds the GraphRAG-lite entity/community graph (see
    app/graph/community_builder.py) without waiting for the next
    `/documents/sync` call. Community detection is corpus-level, so this
    reprocesses every indexed chunk, not just recently uploaded ones.
    """
    try:
        result = rebuild_knowledge_graph(db)
        return GraphRebuildResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Knowledge graph rebuild failed: {str(e)}")


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(verify_api_key)])
async def api_delete_document(document_id: str, db: Session = Depends(get_db)):
    """
    Cascade delete a document and its indexed chunks.
    """
    doc = db.query(Document).filter(Document.document_id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found.")

    db.delete(doc)
    db.commit()
    return None
