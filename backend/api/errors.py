"""Error taxonomy shared by REST and WebSocket transports.

Clients switch on ``code`` (stable), display ``message`` (mutable). Codes are
also used inside WS ``error`` frames — one vocabulary, two transports.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ErrorCode:
    # generic
    INTERNAL = "INTERNAL"
    VALIDATION = "VALIDATION"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    CONFLICT = "CONFLICT"
    # auth
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    AUTH_NOT_IMPLEMENTED = "AUTH_NOT_IMPLEMENTED"
    # ws protocol
    PROTOCOL_VERSION = "PROTOCOL_VERSION_UNSUPPORTED"
    PROTOCOL_FRAME = "PROTOCOL_FRAME_INVALID"
    SESSION_STATE = "SESSION_STATE_INVALID"
    # ai layer
    NO_PROVIDER = "NO_PROVIDER"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    CAPABILITY_NOT_IMPLEMENTED = "CAPABILITY_NOT_IMPLEMENTED"
    # persistence
    DB_NOT_CONFIGURED = "DB_NOT_CONFIGURED"
    # medical safety
    VALIDATION_WARNING = "VALIDATION_WARNING"


class ApiError(Exception):
    """Raise from routes/services; the app's exception handler renders it."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def error_body(code: str, message: str, details: Any = None) -> dict:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                ErrorCode.VALIDATION,
                "request validation failed",
                # field loc + type only; never echo submitted values
                # (they may contain patient data)
                [{"loc": list(err.get("loc", [])), "type": err.get("type")} for err in exc.errors()],
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log type only — exception messages can embed request data.
        request.app.state.metrics.incr("errors_unhandled")
        return JSONResponse(
            status_code=500, content=error_body(ErrorCode.INTERNAL, "internal server error")
        )


def ws_error_frame(code: str, message: str, *, recoverable: bool = True, details: Any = None) -> dict:
    frame: dict[str, Any] = {"v": 1, "type": "error", "code": code, "message": message, "recoverable": recoverable}
    if details is not None:
        frame["details"] = details
    return frame
