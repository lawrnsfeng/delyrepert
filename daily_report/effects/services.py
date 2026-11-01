import sys
from collections.abc import Awaitable, Callable
from typing import Protocol

from daily_report.effects.models import Intent


class Effects(Protocol):
    """The gate every side effect goes through."""

    async def perform[T](self, intent: Intent, action: Callable[[], Awaitable[T]]) -> T | None:
        """Perform `action` (or only report `intent`) and return its result."""


class RealEffects:
    """Performs every side effect."""

    async def perform[T](self, intent: Intent, action: Callable[[], Awaitable[T]]) -> T | None:
        """Await `action` and return its result."""
        del intent  # the real gate performs every intent without reporting it
        return await action()


class DryRunEffects:
    """Records and reports side effects without performing them."""

    def __init__(self) -> None:
        """Start with no recorded intents."""
        self.intents: list[Intent] = []

    async def perform[T](self, intent: Intent, action: Callable[[], Awaitable[T]]) -> T | None:
        """Record `intent`, print it on stderr and skip `action`."""
        del action  # dry-run never performs the effect
        self.intents.append(intent)
        sys.stderr.write(f"[dry-run] would {intent.verb} {intent.target}\n")
        return None
