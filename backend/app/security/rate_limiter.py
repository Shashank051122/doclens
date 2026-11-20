import time
from collections import deque
from threading import Lock

from fastapi import HTTPException, Request

from app.config import settings


class SlidingWindowLimiter:
    def __init__(self):
        self._hits = {}
        self._lock = Lock()

    def check(self, key: str, limit: int, window_seconds: int):
        now = time.monotonic()

        with self._lock:
            hits = self._hits.setdefault(key, deque())

            while hits and now - hits[0] >= window_seconds:
                hits.popleft()

            if len(hits) >= limit:
                retry_after = int(window_seconds - (now - hits[0])) + 1
                return False, retry_after

            hits.append(now)

            if len(self._hits) > 2000:
                self._forget_idle(now, window_seconds)

            return True, 0

    def _forget_idle(self, now: float, window_seconds: int) -> None:
        idle = [
            key
            for key, hits in self._hits.items()
            if not hits or now - hits[-1] >= window_seconds
        ]
        for key in idle:
            del self._hits[key]


class ConcurrencyGate:
    def __init__(self, limit: int):
        self.limit = max(1, limit)
        self.active = 0

    def try_enter(self) -> bool:
        if self.active >= self.limit:
            return False
        self.active += 1
        return True

    def leave(self) -> None:
        self.active = max(0, self.active - 1)


minute_limiter = SlidingWindowLimiter()
read_limiter = SlidingWindowLimiter()
daily_limiter = SlidingWindowLimiter()


def client_ip(request: Request) -> str:
    hops = settings.TRUSTED_PROXY_HOPS

    if hops > 0:
        header = request.headers.get("x-forwarded-for", "")
        parts = [part.strip() for part in header.split(",") if part.strip()]
        if len(parts) >= hops:
            return parts[-hops][:64]

    if request.client:
        return request.client.host
    return "unknown"


def too_many(message: str, retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={"message": message},
        headers={"Retry-After": str(retry_after)},
    )


async def limit_uploads(request: Request) -> None:
    allowed, retry_after = minute_limiter.check(
        client_ip(request), settings.RATE_LIMIT_PER_MINUTE, 60
    )
    if not allowed:
        raise too_many(
            "Too many uploads from your connection. Please wait a moment and try again.",
            retry_after,
        )

    if settings.GLOBAL_DAILY_LIMIT > 0:
        allowed, retry_after = daily_limiter.check(
            "all-visitors", settings.GLOBAL_DAILY_LIMIT, 86400
        )
        if not allowed:
            raise too_many(
                "This free demo has reached its daily limit. Please try again later.",
                retry_after,
            )


async def limit_reads(request: Request) -> None:
    allowed, retry_after = read_limiter.check(
        client_ip(request), settings.READ_RATE_LIMIT_PER_MINUTE, 60
    )
    if not allowed:
        raise too_many("Too many requests. Please slow down.", retry_after)