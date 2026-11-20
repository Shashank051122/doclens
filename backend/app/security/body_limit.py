from starlette.responses import JSONResponse


class BodySizeLimitMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            length = headers.get(b"content-length")

            if length and length.isdigit() and int(length) > self.max_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={"detail": {"message": "That upload is too large."}},
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)