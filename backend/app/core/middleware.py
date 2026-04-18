"""
Production middleware — security headers, request logging, error handling.
"""
import time
import uuid
import logging
import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.config import settings

logger = logging.getLogger("flowrex")


_CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "  # unsafe-inline for Next.js + Tailwind
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self' https: wss:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)

_PERMISSIONS_POLICY = (
    "geolocation=(), microphone=(), camera=(), payment=(), usb=(), "
    "accelerometer=(), gyroscope=(), magnetometer=(), interest-cohort=()"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = _PERMISSIONS_POLICY
        if not settings.DEBUG:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            response.headers["Content-Security-Policy"] = _CSP_POLICY
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log all requests with timing and request ID."""

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        start = time.time()

        response = await call_next(request)

        duration = time.time() - start
        if not request.url.path.startswith("/api/health"):
            logger.info(
                f"[{request_id}] {request.method} {request.url.path} "
                f"-> {response.status_code} ({duration:.3f}s)"
            )

        response.headers["X-Request-ID"] = request_id
        response.headers["X-API-Version"] = "1.0"
        return response


def setup_global_error_handler(app: FastAPI):
    """Global exception handler — never leak internals in production."""

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        if settings.DEBUG:
            # In dev, show full error
            return JSONResponse(
                status_code=500,
                content={"detail": str(exc), "type": type(exc).__name__},
            )
        else:
            # In production, hide internals
            logger.error(f"Unhandled error: {exc}\n{traceback.format_exc()}")
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error"},
            )


class _JSONFormatter(logging.Formatter):
    """JSON log format for production — machine-parseable, structured."""

    def format(self, record):
        import json as _json
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = self.formatException(record.exc_info)
        return _json.dumps(entry)


def setup_logging():
    """Configure structured logging. JSON in production, plain text in debug."""
    # LOG_LEVEL env var overrides the DEBUG-driven default.
    # Lets you enable verbose logs in production without flipping DEBUG
    # (which would also loosen security headers and auth fallbacks).
    override = (settings.LOG_LEVEL or "").strip().upper()
    if override in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        level = getattr(logging, override)
    else:
        level = logging.DEBUG if settings.DEBUG else logging.INFO

    if settings.DEBUG:
        fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        logging.basicConfig(level=level, format=fmt, datefmt="%Y-%m-%d %H:%M:%S")
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(_JSONFormatter())
        logging.basicConfig(level=level, handlers=[handler])

    # Quiet down noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
