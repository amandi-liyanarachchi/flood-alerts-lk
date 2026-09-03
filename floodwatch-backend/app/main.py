"""FastAPI application.

Two things in this file matter more than the rest:

1. THE EXCEPTION HANDLERS. Every non-2xx response in the system goes through
   them, so the error envelope is guaranteed and no stack trace can reach a
   member of the public.

2. THE 500 HANDLER RETURNS 500. An unhandled exception on /locations must stay a
   5xx so the client queues the ping and retries. Converting it to a 4xx to look
   tidier would permanently destroy location data (brief 5.3).
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pathlib import Path

from . import errors, scheduler
from .config import settings
from .db import init_db
from .routers import account, admin, alerts, auth, devices, feedback, locations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("floodwatch")

STATIC_DIR = Path(__file__).parent / "static"


def _lan_address() -> str:
    """Best guess at this machine's address on the local network.

    Opens a UDP socket towards a public address and reads back which local
    interface the OS chose. Nothing is sent, so it works with no internet -- it
    just asks the routing table a question. Printed at startup so you do not
    have to go hunting for `ipconfig` five minutes before a presentation.
    """
    import socket

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except Exception:  # noqa: BLE001
        return "unavailable"


def _banner(port: int = 8000) -> None:
    lan = _lan_address()
    lines = [
        "",
        "  Flood Alerts LK backend is up.",
        "",
        f"  Admin dashboard   http://localhost:{port}",
        f"  API docs          http://localhost:{port}/docs",
        "",
        "  Point the mobile app's base URL at:",
        f"    Android emulator   http://10.0.2.2:{port}/api/v1",
        f"    Phone over USB     http://localhost:{port}/api/v1   (after: adb reverse tcp:{port} tcp:{port})",
        f"    Phone over wifi    http://{lan}:{port}/api/v1",
        "",
        f"  Database   {settings.database_url}",
        f"  Push       {'enabled' if settings.firebase_credentials_file else 'disabled (alerts arrive by polling)'}",
        f"  Ingestion  {'on' if settings.ingest_enabled else 'off (use the dashboard buttons)'}",
        "",
    ]
    print("\n".join(lines), flush=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    scheduler.start()
    log.info("Flood Alerts LK backend ready (environment=%s)", settings.environment)
    _banner(int(os.environ.get("PORT", "8000")))
    yield
    scheduler.stop()


app = FastAPI(
    title="Flood Alerts LK — backend",
    version="1.0.0",
    description=(
        "Smart Flood Early Warning System for Sri Lanka. The mobile client is "
        "built against the /api/v1 contract; do not change it without changing "
        "and re-verifying the client."
    ),
    lifespan=lifespan,
)

# The mobile app does not need CORS. The admin dashboard is served from this
# same origin, so this is only here for local tooling.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.environment == "development" else [],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Error handling -- the single envelope, every time
# ---------------------------------------------------------------------------


@app.exception_handler(errors.ApiError)
async def _api_error(request: Request, exc: errors.ApiError) -> JSONResponse:
    return await errors.api_error_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's default 422 body would not match the contract, and 422 is a 4xx
    the client treats as permanent -- which is correct here, since a malformed
    payload never becomes valid. We return 400 with a message safe to display."""
    detail = exc.errors()[0] if exc.errors() else {}
    message = str(detail.get("msg", "")).replace("Value error, ", "").strip()
    field = ".".join(str(p) for p in detail.get("loc", ()) if p not in ("body", "query"))

    if not message or message.lower().startswith(("field required", "input should")):
        message = (
            f"Please check the {field} field and try again." if field
            else "Some of the information sent was not valid. Please check and try again."
        )

    return errors.error_response(400, errors.VALIDATION_FAILED, message)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {401: errors.UNAUTHORIZED, 403: errors.FORBIDDEN, 404: errors.NOT_FOUND}.get(
        exc.status_code, errors.VALIDATION_FAILED
    )
    message = exc.detail if isinstance(exc.detail, str) else "That request could not be completed."
    if exc.status_code == 404:
        message = "That resource was not found."
    if exc.status_code == 405:
        message = "That request could not be completed."
    return errors.error_response(exc.status_code, code, message)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    # Log everything, leak nothing. And keep it a 500: on /locations that is
    # what makes the client hold on to the ping and retry.
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return errors.error_response(
        500,
        errors.INTERNAL_ERROR,
        "Something went wrong on our side. Please try again in a moment.",
    )


@app.middleware("http")
async def _timing(request: Request, call_next):
    """The client's timeout is 20 seconds; anything slower is a network failure
    to it. Log slow requests so that shows up in the logs before it shows up as
    mysteriously missing data."""
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - started
    if elapsed > 5.0:
        log.warning("SLOW %.1fs %s %s", elapsed, request.method, request.url.path)
    response.headers["X-Response-Time-Ms"] = str(int(elapsed * 1000))
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

API = "/api/v1"

app.include_router(auth.router, prefix=API)
app.include_router(locations.router, prefix=API)
app.include_router(feedback.router, prefix=API)
app.include_router(alerts.router, prefix=API)
app.include_router(devices.router, prefix=API)
app.include_router(account.router, prefix=API)
app.include_router(admin.router, prefix=API)


@app.get("/health", tags=["ops"])
def health() -> dict:
    return {"status": "ok", "environment": settings.environment}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
