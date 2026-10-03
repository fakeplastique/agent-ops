import json

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.errors import error_envelope

MAX_BODY_SIZE = 1 * 1024 * 1024  # 1 MB default


class PayloadTooLargeException(HTTPException):
    """An HTTPException so the app's own error handler renders the 413 envelope mid-stream."""

    def __init__(self, max_bytes: int):
        super().__init__(413, f"Request body exceeds maximum size of {max_bytes} bytes")


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_SIZE):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                length = int(content_length.decode("ascii"))
                if length > self.max_bytes:
                    await self._send_413(send, length)
                    return
            except (ValueError, UnicodeDecodeError):
                pass

        total_bytes = 0

        async def limited_receive():
            nonlocal total_bytes
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                total_bytes += len(chunk)
                if total_bytes > self.max_bytes:
                    raise PayloadTooLargeException(self.max_bytes)
            return message

        await self.app(scope, limited_receive, send)

    async def _send_413(self, send: Send, received: int) -> None:
        body = json.dumps(
            error_envelope(
                code="payload_too_large",
                message=f"Request body exceeds maximum size of {self.max_bytes} bytes",
                details={"max_bytes": self.max_bytes, "received_bytes": received},
            )
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
