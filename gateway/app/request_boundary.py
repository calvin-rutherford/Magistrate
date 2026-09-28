"""ASGI byte/concurrency limits apply before multipart and JSON parsing.

Content-Length is only an early rejection hint. Counting receive bytes also
covers chunked and dishonestly framed requests. Proxy byte/time/rate limits
remain required: this is a per-process boundary, not a distributed limiter.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import time

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app import telemetry

DEFAULT_BODY_LIMIT = 1_000_000
UPLOAD_BODY_LIMIT = 50 * 1024 * 1024 + 1_100_000
VOICE_BODY_LIMIT = 25 * 1024 * 1024 + 1_000_000
AVATAR_BODY_LIMIT = 5 * 1024 * 1024 + 64_000


class RequestBoundary:
    def __init__(self, app, *, max_inflight: int | None = None, body_timeout: float = 30):
        self.app = app
        self.max_inflight = max_inflight or int(os.getenv('MAGISTRATE_MAX_INFLIGHT_REQUESTS', '32'))
        if not 1 <= self.max_inflight <= 256:
            raise RuntimeError('MAGISTRATE_MAX_INFLIGHT_REQUESTS must be between 1 and 256.')
        self.body_timeout = body_timeout
        self.inflight = 0

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        correlation = secrets.token_hex(16)  # Do not trust a supplied request/trace ID.
        context = telemetry.request_id.set(correlation)
        start = time.monotonic()
        status = 500
        failure = None
        started = False
        counted = False
        path = scope.get('path', '')
        limit = {
            '/api/v1/uploads': UPLOAD_BODY_LIMIT,
            '/api/v1/voice/transcribe': VOICE_BODY_LIMIT,
            '/api/v1/account/avatar': AVATAR_BODY_LIMIT,
        }.get(path, DEFAULT_BODY_LIMIT)
        total = 0
        body_deadline = start + self.body_timeout

        async def bounded_receive():
            nonlocal total, failure
            try:
                message = await asyncio.wait_for(receive(), max(0.001, body_deadline - time.monotonic()))
            except asyncio.TimeoutError:
                failure = 408
                raise HTTPException(408, 'Request body timed out.') from None
            if message['type'] == 'http.request':
                total += len(message.get('body', b''))
                if total > limit:
                    failure = 413
                    raise HTTPException(413, 'Request body is too large.')
            return message

        async def secured_send(message):
            nonlocal status, started
            if message['type'] == 'http.response.start':
                started = True
                status = message['status']
                headers = [(k, v) for k, v in message.get('headers', []) if k.lower() not in {
                    b'x-request-id', b'x-content-type-options', b'referrer-policy',
                }]
                headers.extend([(b'x-request-id', correlation.encode()),
                                (b'x-content-type-options', b'nosniff'),
                                (b'referrer-policy', b'no-referrer')])
                if path.startswith(('/api/', '/internal/', '/uploads/')):
                    headers = [(k, v) for k, v in headers if k.lower() != b'cache-control']
                    headers.append((b'cache-control', b'no-store'))
                if path.startswith('/uploads/'):
                    headers.append((b'content-security-policy', b"default-src 'none'; sandbox"))
                message = {**message, 'headers': headers}
            await send(message)

        async def reject(code, detail):
            await JSONResponse({'detail': detail}, status_code=code)(scope, receive, secured_send)

        try:
            lengths = [v for k, v in scope.get('headers', []) if k.lower() == b'content-length']
            if len(lengths) > 1 or (lengths and (not lengths[0].isdigit())):
                return await reject(400, 'Invalid request size.')
            if lengths and (len(lengths[0]) > 12 or int(lengths[0]) > limit):
                return await reject(413, 'Request body is too large.')
            # No waiting queue: overload must not exhaust worker memory. Single
            # event-loop mutation has no await between the check and increment.
            if self.inflight >= self.max_inflight:
                return await reject(503, 'Gateway request capacity is exhausted.')
            self.inflight += 1
            counted = True
            try:
                await self.app(scope, bounded_receive, secured_send)
            except Exception:
                if started:
                    raise
                await reject(failure or 500, 'Request body rejected.' if failure else 'Internal server error.')
        finally:
            if counted:
                self.inflight -= 1
            route = getattr(scope.get('route'), 'path', 'unmatched')
            outcome = 'rejected' if status in {400, 401, 403, 408, 413, 429} else 'error' if status >= 500 else 'ok'
            telemetry.record('http', outcome=outcome, duration=time.monotonic() - start,
                             route=route, status=status)
            telemetry.request_id.reset(context)
