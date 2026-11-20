from contextlib import asynccontextmanager
from pathlib import Path as FilePath

from fastapi import Depends, FastAPI, File, HTTPException, Path, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db.database import init_db, purge_old_documents
from app.db.repository import (
    delete_document,
    get_document,
    get_stats,
    list_documents,
    update_correction,
)
from app.security.body_limit import BodySizeLimitMiddleware
from app.security.rate_limiter import ConcurrencyGate, limit_reads, limit_uploads
from app.services.ai_providers import AllProvidersFailed, describe_providers, generate
from app.services.invoice_schema import InvoiceData
from app.services.output_validator import validate_invoice
from app.services.pipeline import PipelineError, run_pipeline
from app.services.sse import stream_events

DOCUMENT_ID_PATTERN = r"^[0-9a-f]{32}$"

FRONTEND_DIR = FilePath(__file__).resolve().parents[2] / "frontend"

PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "img-src 'self' data: blob:; "
        "style-src 'self'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}

BUSY_MESSAGE = "DocLens is busy reading other documents. Please try again in a few seconds."

busy_gate = ConcurrencyGate(settings.MAX_CONCURRENT_UPLOADS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    purge_old_documents()
    yield


app = FastAPI(
    title="DocLens",
    description="Reads documents like invoices and receipts and returns clean structured data.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    BodySizeLimitMiddleware, max_bytes=settings.MAX_FILE_SIZE_BYTES + 1024 * 1024
)


@app.get("/", include_in_schema=False)
def home():
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(
            status_code=404, detail={"message": "The frontend files were not found."}
        )
    return FileResponse(index_file, headers=PAGE_HEADERS)


@app.get("/api/health")
def health_check():
    return {"status": "ok", "app": "DocLens"}


@app.get("/api/config", dependencies=[Depends(limit_reads)])
def public_config():
    return {
        "max_file_size_mb": settings.MAX_FILE_SIZE_MB,
        "allowed_extensions": settings.ALLOWED_EXTENSIONS,
        "provider_order": settings.PROVIDER_ORDER,
        "data_retention_days": settings.DATA_RETENTION_DAYS,
        "providers_ready": {
            "gemini": bool(settings.GEMINI_API_KEY),
            "groq": bool(settings.GROQ_API_KEY),
            "openrouter": bool(settings.OPENROUTER_API_KEY),
        },
    }


@app.get("/api/providers", dependencies=[Depends(limit_reads)])
def providers_status():
    return {"order": settings.PROVIDER_ORDER, "providers": describe_providers()}


@app.post("/api/ai-ping", dependencies=[Depends(limit_uploads)])
async def ai_ping():
    try:
        result = await generate(
            system_prompt="You are a connection tester. Reply with JSON only.",
            user_text='Reply with exactly this JSON: {"status": "ok"}',
            images=[],
        )
    except AllProvidersFailed as error:
        raise HTTPException(
            status_code=503,
            detail={"message": error.message, "attempts": error.attempts},
        )

    return {
        "provider": result["provider"],
        "model": result["model"],
        "reply": result["text"][:200],
        "attempts": result["attempts"],
    }


@app.post("/api/upload", dependencies=[Depends(limit_uploads)])
async def upload_document(file: UploadFile = File(...)):
    content = await file.read(settings.MAX_FILE_SIZE_BYTES + 1)

    if not busy_gate.try_enter():
        raise HTTPException(
            status_code=503,
            detail={"message": BUSY_MESSAGE},
            headers={"Retry-After": "5"},
        )

    try:
        return await run_pipeline(file.filename or "", content)
    except PipelineError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"message": error.message, **error.detail},
        )
    finally:
        busy_gate.leave()


@app.post("/api/upload/stream", dependencies=[Depends(limit_uploads)])
async def upload_document_stream(file: UploadFile = File(...)):
    content = await file.read(settings.MAX_FILE_SIZE_BYTES + 1)
    filename = file.filename or ""

    async def job(emit):
        if not busy_gate.try_enter():
            await emit(
                "failure",
                {"stage": "server", "message": BUSY_MESSAGE, "status_code": 503},
            )
            return

        try:
            await run_pipeline(filename, content, emit)
        finally:
            busy_gate.leave()

    return StreamingResponse(
        stream_events(job),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/documents", dependencies=[Depends(limit_reads)])
async def documents_list(limit: int = Query(50, ge=1, le=100)):
    documents = await run_in_threadpool(list_documents, limit)
    return {"documents": documents}


@app.get("/api/documents/{document_id}", dependencies=[Depends(limit_reads)])
async def documents_get(document_id: str = Path(..., pattern=DOCUMENT_ID_PATTERN)):
    document = await run_in_threadpool(get_document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail={"message": "Document not found."})
    return document


@app.patch("/api/documents/{document_id}", dependencies=[Depends(limit_reads)])
async def documents_correct(
    corrected: InvoiceData,
    document_id: str = Path(..., pattern=DOCUMENT_ID_PATTERN),
):
    existing = await run_in_threadpool(get_document, document_id)
    if existing is None:
        raise HTTPException(status_code=404, detail={"message": "Document not found."})

    data = corrected.model_dump()
    validation = validate_invoice(data, existing["risk"])

    await run_in_threadpool(update_correction, document_id, data, validation)
    return await run_in_threadpool(get_document, document_id)


@app.delete("/api/documents/{document_id}", dependencies=[Depends(limit_reads)])
async def documents_delete(document_id: str = Path(..., pattern=DOCUMENT_ID_PATTERN)):
    deleted = await run_in_threadpool(delete_document, document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail={"message": "Document not found."})
    return {"deleted": True}


@app.get("/api/stats", dependencies=[Depends(limit_reads)])
async def stats():
    return await run_in_threadpool(get_stats)


if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")