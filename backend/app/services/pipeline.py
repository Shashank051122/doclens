import time
from typing import Optional

from fastapi.concurrency import run_in_threadpool

from app.db.repository import save_document
from app.security.file_validator import FileRejected, validate_upload
from app.services.ai_providers import AllProvidersFailed
from app.services.document_reader import DocumentUnreadable, read_document
from app.services.extractor import ExtractionFailed, ask_ai
from app.services.output_validator import validate_invoice
from app.services.prompt_builder import build_prompt

TITLES = {
    "security": "Security gate",
    "reader": "Read document",
    "prompt": "Safe prompt",
    "ai": "AI call",
    "validate": "Validate output",
    "save": "Save result",
}


class PipelineError(Exception):
    def __init__(
        self, stage: str, message: str, status_code: int, detail: Optional[dict] = None
    ):
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.status_code = status_code
        self.detail = detail or {}


async def run_pipeline(filename: str, content: bytes, emit=None) -> dict:
    pipeline_start = time.perf_counter()

    async def send(event: str, payload: dict):
        if emit is not None:
            await emit(event, payload)

    def since(began: float) -> int:
        return int((time.perf_counter() - began) * 1000)

    async def running(stage: str):
        await send(
            "stage", {"stage": stage, "status": "running", "title": TITLES[stage]}
        )

    async def finish(stage: str, began: float, status: str, detail: str, **extra):
        await send(
            "stage",
            {
                "stage": stage,
                "status": status,
                "title": TITLES[stage],
                "detail": detail,
                "elapsed_ms": since(began),
                **extra,
            },
        )

    began = time.perf_counter()
    await running("security")
    try:
        gate = validate_upload(filename, content)
    except FileRejected as error:
        await finish("security", began, "failed", error.message, checks=error.checks)
        raise PipelineError(
            "security", error.message, error.status_code, {"checks": error.checks}
        )
    await finish(
        "security", began, "done", f"{len(gate['checks'])} checks passed", checks=gate["checks"]
    )

    began = time.perf_counter()
    await running("reader")
    try:
        reading = await run_in_threadpool(read_document, content, gate["detected_type"])
    except DocumentUnreadable as error:
        await finish("reader", began, "failed", error.message)
        raise PipelineError("reader", error.message, 422)
    await finish(
        "reader",
        began,
        "done",
        f"Mode: {reading['mode']}, read {reading['pages_read']} of {reading['total_pages']} page(s)",
        mode=reading["mode"],
        notes=reading["notes"],
    )

    began = time.perf_counter()
    await running("prompt")
    prompt = build_prompt(reading)
    prompt_status = "done" if prompt["risk"] == "none" else "attention"
    await finish(
        "prompt",
        began,
        prompt_status,
        f"Injection risk: {prompt['risk']}",
        risk=prompt["risk"],
        findings=prompt["findings"],
        notes=prompt["notes"],
    )

    began = time.perf_counter()
    await running("ai")

    async def report_attempt(attempt: dict):
        await send("attempt", attempt)

    try:
        answer = await ask_ai(prompt, on_attempt=report_attempt)
    except AllProvidersFailed as error:
        await finish("ai", began, "failed", error.message, attempts=error.attempts)
        raise PipelineError("ai", error.message, 503, {"attempts": error.attempts})
    except ExtractionFailed as error:
        await finish(
            "ai", began, "failed", error.message, attempts=error.attempts, notes=error.notes
        )
        raise PipelineError(
            "ai", error.message, 502, {"attempts": error.attempts, "notes": error.notes}
        )
    await finish(
        "ai",
        began,
        "done",
        f"Answered by {answer['provider']} ({answer['model']})",
        provider=answer["provider"],
        model=answer["model"],
        notes=answer["notes"],
    )

    began = time.perf_counter()
    await running("validate")
    validation = validate_invoice(answer["data"], prompt["risk"])
    validate_status = "done" if validation["status"] == "ok" else "attention"
    await finish(
        "validate", began, validate_status, validation["summary"], validation=validation
    )

    duration_ms = since(pipeline_start)

    record = {
        "filename": gate["safe_filename"],
        "file_type": gate["detected_type"],
        "size_bytes": gate["size_bytes"],
        "mode": reading["mode"],
        "pages_read": reading["pages_read"],
        "provider": answer["provider"],
        "model": answer["model"],
        "duration_ms": duration_ms,
        "status": validation["status"],
        "risk": prompt["risk"],
        "data": answer["data"],
        "validation": validation,
        "trace": {
            "security": gate["checks"],
            "reader": reading["notes"],
            "safety": prompt["notes"],
            "findings": [finding["name"] for finding in prompt["findings"]],
            "ai_attempts": answer["attempts"],
            "notes": answer["notes"],
        },
    }

    began = time.perf_counter()
    await running("save")
    try:
        document_id = await run_in_threadpool(save_document, record)
        await finish("save", began, "done", "Result saved", id=document_id)
    except Exception:
        document_id = None
        await finish(
            "save", began, "attention", "Could not save. The result is still shown below."
        )

    result = {
        "status": "extracted",
        "id": document_id,
        "saved": document_id is not None,
        "duration_ms": duration_ms,
        "security": gate,
        "reader": {
            "mode": reading["mode"],
            "total_pages": reading["total_pages"],
            "pages_read": reading["pages_read"],
            "text_length": len(reading["text"]),
            "image_count": len(reading["images"]),
            "notes": reading["notes"],
        },
        "safety": {
            "risk": prompt["risk"],
            "findings": prompt["findings"],
            "notes": prompt["notes"],
        },
        "ai": {
            "provider": answer["provider"],
            "model": answer["model"],
            "attempts": answer["attempts"],
            "answers_used": answer["answers_used"],
        },
        "data": answer["data"],
        "validation": validation,
        "notes": answer["notes"],
    }

    await send("result", result)
    return result