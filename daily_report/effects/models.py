from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    """A side effect a command wants to perform, e.g. `write snapshots/2026-11-03.json`."""

    verb: str
    target: str
