"""Fixed-window rate limiting: Redis when reachable, per-process counters otherwise."""
from __future__ import annotations

import logging
import threading
import time

import redis
from fastapi import Request

from app.core.config import settings
from app.core.errors import RateLimited

logger = logging.getLogger("autopilot.ratelimit")
_lock = threading.Lock()
_local: dict[str, tuple[int, int]] = {}
_client: redis.Redis | None = None
_redis_retry_at = 0.0


def _redis() -> redis.Redis | None:
    global _client, _redis_retry_at
    if settings.environment == "test" or time.monotonic() < _redis_retry_at:
        return None
    if _client is None:
        _client = redis.Redis.from_url(settings.redis_url, socket_timeout=0.25, socket_connect_timeout=0.25)
    return _client


def _hit(key: str, window: int) -> int:
    global _redis_retry_at
    client = _redis()
    if client is not None:
        try:
            pipe = client.pipeline()
            pipe.incr(key)
            pipe.expire(key, window + 1)
            return int(pipe.execute()[0])
        except redis.RedisError:
            _redis_retry_at = time.monotonic() + 30
            logger.warning("rate_limit_redis_unavailable")
    with _lock:
        if len(_local) > 50_000:
            _local.clear()
        count = _local.get(key, (0, 0))[0] + 1
        _local[key] = (count, int(time.time()))
        return count


def reset_local_counters() -> None:
    with _lock:
        _local.clear()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce(scope: str, identity: str, limit: int, window: int = 60) -> None:
    bucket = int(time.time() // window)
    if _hit(f"rl:{scope}:{identity}:{bucket}", window) > limit:
        raise RateLimited(f"Too many requests. Try again in up to {window} seconds.", details={"retry_after": window})


def limit_auth(request: Request) -> None:
    enforce("auth", client_ip(request), settings.rate_limit_auth_per_minute)
