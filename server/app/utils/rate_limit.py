from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from redis import Redis
from redis.exceptions import RedisError

from app.config import settings

UPLOAD_PREPARE_RATE_LIMIT = 10
UPLOAD_COMPLETE_RATE_LIMIT = 20
BULK_UPLOAD_PREPARE_RATE_LIMIT = 5
BULK_UPLOAD_COMPLETE_RATE_LIMIT = 10
QUERY_RATE_LIMIT = 100
RATE_LIMIT_WINDOW_SECONDS = 60

_redis_client: Redis | None = None


def _redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def enforce_workspace_rate_limit(
    *,
    workspace_id: uuid.UUID,
    operation: str,
    limit: int,
    window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
) -> None:
    key = f"rate_limit:{operation}:{workspace_id}"
    try:
        count = int(_redis().incr(key))
        if count == 1:
            _redis().expire(key, window_seconds)
    except RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Rate limiter unavailable: {exc}",
        ) from exc

    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for {operation}",
        )
