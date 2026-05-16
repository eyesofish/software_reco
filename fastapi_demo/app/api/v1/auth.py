"""API key authentication dependencies for /api/v1/* routes.

Behavior:
- If `settings.API_KEY` is empty (default), `require_api_key` is a no-op (dev mode).
- If set, callers must send `X-API-Key: <key>` (constant-time compared).
- `require_admin_api_key` is independent: gates admin endpoints behind `ADMIN_API_KEY`.
  When `ADMIN_API_KEY` is empty, falls back to requiring `API_KEY` (still better than open).
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


def _safe_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = settings.API_KEY
    if not expected:
        return  # dev mode: auth disabled
    if not x_api_key or not _safe_eq(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


async def require_admin_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = settings.ADMIN_API_KEY or settings.API_KEY
    if not expected:
        return  # dev mode: auth disabled
    if not x_api_key or not _safe_eq(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid admin X-API-Key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
