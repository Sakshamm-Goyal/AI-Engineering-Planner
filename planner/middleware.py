"""Bound request bodies before multipart parsing; add conservative response headers."""
import json
import uuid


class RequestGuards:
    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid.uuid4().hex[:12]

        async def safe_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
                            (b"x-frame-options", b"DENY"), (b"x-request-id", request_id.encode()),
                            (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")]
                if scope["path"].startswith("/api/"):
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        async def reject(status: int, code: str, message: str):
            body = json.dumps({"error": {"code": code, "message": message}}).encode()
            await safe_send({"type": "http.response.start", "status": status,
                             "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await safe_send({"type": "http.response.body", "body": body})

        if scope["method"] in ("POST", "PUT", "PATCH"):
            # Reject cross-origin browser submissions, including simple multipart
            # forms. This is not authentication: the app is local/single-user.
            headers = dict(scope["headers"])
            origin = headers.get(b"origin", b"").decode("latin-1")
            if origin:
                from urllib.parse import urlsplit
                parsed = urlsplit(origin)
                host = headers.get(b"host", b"").decode("latin-1")
                if parsed.netloc != host or parsed.scheme != scope.get("scheme", "http"):
                    return await reject(403, "cross_origin", "Cross-origin submissions are not allowed.")
            length = headers.get(b"content-length")
            if length:
                try:
                    claimed = int(length)
                    if claimed < 0:
                        raise ValueError
                except ValueError:
                    return await reject(400, "content_length", "Invalid Content-Length header.")
                if claimed > self.max_bytes:
                    return await reject(413, "request_limit", "The request exceeds the configured upload limit.")
            # Pre-buffer at a strict cap, so multipart parsers cannot spool an
            # unbounded file before the endpoint runs. No persistence is introduced.
            chunks, total = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                total += len(chunk)
                if total > self.max_bytes:
                    return await reject(413, "request_limit", "The request exceeds the configured upload limit.")
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            body = b"".join(chunks)
            sent = False

            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()
            await self.app(scope, replay, safe_send)
        else:
            await self.app(scope, receive, safe_send)
