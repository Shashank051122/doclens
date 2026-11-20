import asyncio
import json

from app.services.pipeline import PipelineError

KEEPALIVE_SECONDS = 15


def format_sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


async def stream_events(job):
    queue = asyncio.Queue()

    async def emit(event: str, payload: dict):
        await queue.put((event, payload))

    async def worker():
        try:
            await job(emit)
        except PipelineError as error:
            await emit(
                "failure",
                {
                    "stage": error.stage,
                    "message": error.message,
                    "status_code": error.status_code,
                    **error.detail,
                },
            )
        except Exception:
            await emit(
                "failure",
                {
                    "stage": "server",
                    "message": "Something went wrong on the server.",
                    "status_code": 500,
                },
            )
        finally:
            await queue.put(None)

    task = asyncio.create_task(worker())

    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue

            if item is None:
                break

            event, payload = item
            yield format_sse(event, payload)
    finally:
        if not task.done():
            task.cancel()