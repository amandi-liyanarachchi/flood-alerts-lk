"""The single error envelope every non-2xx response uses (brief sec 3).

    {"error": {"code": "...", "message": "..."}}

`message` is shown directly to a member of the public, so it must never carry a
stack trace, SQL, or an internal identifier. The catch-all handler in main.py
logs the real exception and returns a fixed, safe sentence.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

# Codes the Flutter client matches on by name.
INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
NIC_ALREADY_REGISTERED = "NIC_ALREADY_REGISTERED"
VALIDATION_FAILED = "VALIDATION_FAILED"
UNAUTHORIZED = "UNAUTHORIZED"
# Server-side codes. The client does not branch on these; it branches on status.
SERVICE_BUSY = "SERVICE_BUSY"
NOT_FOUND = "NOT_FOUND"
FORBIDDEN = "FORBIDDEN"
INTERNAL_ERROR = "INTERNAL_ERROR"


class ApiError(Exception):
    """Raise this instead of HTTPException so the envelope is never bypassed."""

    def __init__(self, status_code: int, code: str, message: str, headers: dict | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}
        super().__init__(message)


def error_response(status_code: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.headers)


# --- convenience constructors ------------------------------------------

def unauthorized(message: str = "Your session has expired. Please sign in again.") -> ApiError:
    """401 -- ONLY for a genuinely invalid or expired token.

    On any 401 outside /auth/*, the client tears down the session AND WIPES its
    queue of up to 50 pending location pings (brief sec 5.2). Never use this for
    a transient problem.
    """
    return ApiError(401, UNAUTHORIZED, message)


def invalid_credentials() -> ApiError:
    return ApiError(401, INVALID_CREDENTIALS, "NIC or password is incorrect")


def validation_failed(message: str) -> ApiError:
    """400 -- permanent, the payload will never become valid. The client drops it."""
    return ApiError(400, VALIDATION_FAILED, message)


def service_busy(retry_after: int = 60) -> ApiError:
    """503 -- transient. The client queues and retries.

    Deliberately NOT 429: 429 is a 4xx and the client drops the ping forever,
    destroying location data during exactly the conditions this system exists
    for (brief sec 5.3).
    """
    return ApiError(
        503,
        SERVICE_BUSY,
        "The service is busy right now. Your data will be sent again shortly.",
        headers={"Retry-After": str(retry_after)},
    )
