"""Reject request bodies over the configured size, including chunked ones without a length.

The request-ID middleware already refuses an oversized ``Content-Length`` up front; this ASGI
wrapper also counts bytes as they stream in, so a chunked body cannot bypass the limit.
"""

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


async def _too_large(scope: Scope, send: Send) -> None:
    body = json.dumps(
        {
            "type": "https://codegenome.dev/problems/request_too_large",
            "title": "Request too large",
            "status": 413,
            "detail": "Request body exceeds the configured byte limit.",
            "instance": scope.get("path", ""),
            "code": "REQUEST_TOO_LARGE",
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BodyLimitMiddleware:
    """Bodies with a Content-Length are checked by the request-ID middleware. A body without
    one (chunked) is read here up to the limit first: too large gets a 413, otherwise the
    buffered body is replayed to the application unchanged."""

    def __init__(self, app: ASGIApp, limit: Callable[[], int]) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = dict(scope.get("headers") or [])
        if scope["type"] != "http" or b"content-length" in headers:
            await self.app(scope, receive, send)
            return
        maximum = self.limit()
        buffered: list[Message] = []
        received = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break
            received += len(message.get("body", b""))
            if received > maximum:
                await _too_large(scope, send)
                return
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            return buffered.pop(0) if buffered else await receive()

        await self.app(scope, replay, send)
