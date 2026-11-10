"""HTTP helper for external APIs: timeouts, backoff, Retry-After, and honest failure classification."""
from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

from app.core.errors import ExternalServiceError

logger = logging.getLogger("autopilot.http")
TIMEOUT = httpx.Timeout(20.0, connect=8.0)


def request_with_retry(method: str, url: str, *, service: str, retries: int = 3, idempotent: bool | None = None, **kwargs: Any) -> httpx.Response:
    """Non-idempotent requests are never retried once the request may have reached the server."""
    if idempotent is None:
        idempotent = method.upper() in {"GET", "HEAD", "PUT", "DELETE"}
    attempts = retries + 1 if idempotent else 1
    last_error: str = "unknown error"
    for attempt in range(attempts):
        try:
            response = httpx.request(method, url, timeout=TIMEOUT, **kwargs)
        except httpx.ConnectError as exc:
            # The connection never opened, so the request cannot have been applied.
            last_error = f"could not connect ({exc.__class__.__name__})"
        except httpx.HTTPError as exc:
            if not idempotent:
                raise ExternalServiceError(
                    f"{service} did not confirm the request ({exc.__class__.__name__}). It may or may not have been applied.",
                    outcome_unknown=True,
                ) from exc
            last_error = exc.__class__.__name__
        else:
            if response.status_code == 429 or (response.status_code >= 500 and idempotent):
                last_error = f"HTTP {response.status_code}"
                retry_after = response.headers.get("retry-after", "")
                if attempt + 1 < attempts:
                    delay = float(retry_after) if retry_after.isdigit() else _backoff(attempt)
                    time.sleep(min(delay, 20))
                    continue
            elif response.status_code >= 500:
                raise ExternalServiceError(f"{service} returned HTTP {response.status_code}; the outcome is unknown.", outcome_unknown=True)
            else:
                return response
            break
        if attempt + 1 < attempts:
            time.sleep(_backoff(attempt))
    raise ExternalServiceError(f"{service} is unavailable: {last_error}.", retryable=True)


def _backoff(attempt: int) -> float:
    return (2**attempt) * 0.5 + random.uniform(0, 0.25)  # noqa: S311


def raise_for_status(response: httpx.Response, service: str, action: str) -> None:
    if response.is_success:
        return
    try:
        detail = response.json().get("error", {})
        message = detail.get("message") if isinstance(detail, dict) else str(detail)
    except ValueError:
        message = None
    logger.warning("external_api_rejected", extra={"service": service, "status": response.status_code, "action": action})
    raise ExternalServiceError(f"{service} rejected {action}: {message or f'HTTP {response.status_code}'}")
