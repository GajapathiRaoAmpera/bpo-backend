import sys

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter

from app.core.audit import _client


def client_ip(request: Request) -> str:
    # First X-Forwarded-For entry (Cloud Run / Next.js proxy), else the socket peer.
    return _client(request)[0] or "unknown"


# shortcut: in-memory counters, correct only for a single instance; use a Redis storage_uri before scaling out.
# Off under pytest because the suite logs in hundreds of times from one client; tests/test_security.py turns it on.
limiter = Limiter(key_func=client_ip, enabled="pytest" not in sys.modules)


def rate_limited(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": "Too many attempts. Try again in a minute."}, status_code=429)
