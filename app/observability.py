"""Structured diagnostics. Never pass request data or exception messages to emit()."""
from __future__ import annotations

import json
import logging
import time
import traceback
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
logger = logging.getLogger("stayzy.api")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def emit(event: str, *, level: int = logging.INFO, **fields: object) -> None:
    logger.log(level, json.dumps({**fields, "event": event, "request_id": request_id.get(), "correlation_id": correlation_id.get()}))


def failure_fields(error: Exception) -> dict[str, object]:
    # Exception text, SQL, bound parameters and locals can contain credentials.
    chain = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < 6:
        seen.add(id(current))
        chain.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    frames = traceback.extract_tb(error.__traceback__)
    return {"error_types": chain, "frames": [
        {"file": f.filename.rsplit("/", 1)[-1], "line": f.lineno, "function": f.name}
        for f in frames[-8:]
    ]}


def resolve_correlation_id(headers: list[tuple[bytes, bytes]]) -> str:
    values = [value for key, value in headers if key.lower() == b"xcorrelationid"]
    if len(values) == 1 and len(values[0]) == 36:
        try:
            value = values[0].decode("ascii")
            identifier = str(uuid.UUID(value))
            if identifier == value.lower():
                return identifier
        except (ValueError, UnicodeDecodeError):
            pass
    return str(uuid.uuid4())


class RequestDiagnostics:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        identifier = uuid.uuid4().hex
        correlation = resolve_correlation_id(scope.get("headers", []))
        correlation_token = correlation_id.set(correlation)
        token = request_id.set(identifier)
        scope.setdefault("state", {})["request_id"] = identifier
        scope["state"]["correlation_id"] = correlation
        start = time.monotonic()
        status = 500
        started = False
        # Do not log raw paths, query strings, headers, IPs or request bodies.
        method = scope["method"] if scope["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "OTHER"
        emit("request.started", method=method)

        async def tracked_send(message):
            nonlocal status, started
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                message = {**message, "headers": [
                    (k, v) for k, v in message.get("headers", []) if k.lower() not in {b"x-request-id", b"xcorrelationid"}
                ] + [(b"x-request-id", identifier.encode()), (b"xcorrelationid", correlation.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except Exception as error:
            emit("request.exception", level=logging.ERROR, **failure_fields(error))
            if started:
                # Avoid a second response and never let Uvicorn print raw exceptions.
                status = 500
            else:
                await JSONResponse(status_code=500, content={"detail": {
                    "code": "internal_error",
                    "message": "Stayzy encountered a server error. Please try again later.",
                    "request_id": identifier,
                    "correlation_id": correlation,
                }})(scope, receive, tracked_send)
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            emit("request.completed", level=logging.ERROR if status >= 500 else logging.INFO,
                 method=method, route=route, status=status,
                 duration_ms=round((time.monotonic() - start) * 1000, 1))
            request_id.reset(token)
            correlation_id.reset(correlation_token)


def install_diagnostics(app: FastAPI) -> None:
    app.add_middleware(RequestDiagnostics)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        detail = error.detail
        if not isinstance(detail, dict) or not {"code", "message"} <= detail.keys():
            detail = {"code": "http_error", "message": "Stayzy could not complete the request."}
        emit("request.rejected", status=error.status_code, code=detail["code"])
        return JSONResponse(status_code=error.status_code, headers=error.headers,
                            content={"detail": {**detail, "request_id": request_id.get(), "correlation_id": correlation_id.get()}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Pydantic's default response includes submitted input, including tokens.
        emit("request.validation_failed", status=422, issue_count=len(error.errors()))
        return JSONResponse(status_code=422, content={"detail": {
            "code": "invalid_request", "message": "Please check the submitted information and try again.",
            "request_id": request_id.get(), "correlation_id": correlation_id.get(),
        }})
