"""Retry policy shared by every HTTP client (Jira now, Slack later): collector D9."""

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Self

from daily_report.exc import TemplatedError

type Sleeper = Callable[[float], Awaitable[None]]


@dataclass
class RetryableError(TemplatedError):
    """Raised by an attempt that may succeed if repeated (HTTP 429 or 5xx)."""

    TEMPLATE = "HTTP {status}"
    status: int
    retry_after: float | None = None

    @classmethod
    def from_response(cls, status: int, retry_after: str | None) -> Self:
        """Build from a status and a raw `Retry-After` header (seconds; dates are ignored)."""
        return cls(status, float(retry_after) if retry_after and retry_after.isdigit() else None)


@dataclass
class RetriesExhaustedError(TemplatedError):
    """Every attempt failed with a retryable error."""

    TEMPLATE = "HTTP {status} after {attempts} attempts"
    status: int
    attempts: int


def _jitter() -> float:
    return secrets.randbelow(501) / 1000


@dataclass(frozen=True)
class RetryPolicy:
    """Honour `Retry-After`, else back off 1/2/4 s plus jitter; give up after 4 attempts."""

    attempts: int = 4
    base_delay: float = 1.0
    sleep: Sleeper = field(default=asyncio.sleep)
    jitter: Callable[[], float] = field(default=_jitter)

    async def run[T](self, call: Callable[[], Awaitable[T]]) -> T:
        """Await `call` until it stops raising `RetryableError` or attempts run out."""
        attempt = 1
        while True:
            try:
                return await call()
            except RetryableError as exc:
                if attempt >= self.attempts:
                    raise RetriesExhaustedError(exc.status, attempt) from exc
                delay = exc.retry_after
                if delay is None:
                    delay = self.base_delay * 2 ** (attempt - 1) + self.jitter()
                await self.sleep(delay)
                attempt += 1
