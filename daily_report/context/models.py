from dataclasses import dataclass, field
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, ValidationError

CONTEXT_TEXT_MAX = 120


class ContextItem(BaseModel):
    """One item another skill or tool contributes to the standup."""

    group: Literal["attention", "done", "today", "note"]
    text: str = Field(min_length=1, max_length=CONTEXT_TEXT_MAX)
    link: str | None = None
    priority: Literal["blocked", "at_risk", "normal"] | None = None


class ContextEnvelope(BaseModel):
    """`{"source", "items"}`; items are validated one by one so a bad one drops alone."""

    source: str = Field(min_length=1)
    items: list[Any]


class Dropped(BaseModel):
    """A context source or item left out of the draft, and why."""

    source: str
    index: int | None = None
    reason: str


@dataclass(frozen=True)
class ContextEntry:
    """A valid context item and where it came from (`index` is 1-based)."""

    source: str
    index: int
    item: ContextItem


def _reason(exc: ValidationError) -> str:
    error = exc.errors()[0]
    loc = ".".join(str(part) for part in error["loc"])
    return f"{loc}: {error['msg']}" if loc else error["msg"]


@dataclass(frozen=True)
class ContextResult:
    """Everything the context sources gave: source names, valid items and dropped ones."""

    sources: list[str] = field(default_factory=list)
    entries: list[ContextEntry] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)

    @classmethod
    def parse(cls, origin: str, text: str) -> Self:
        """Parse one source's JSON; `origin` names it when the JSON itself is invalid."""
        try:
            envelope = ContextEnvelope.model_validate_json(text)
        except ValidationError as exc:
            return cls(dropped=[Dropped(source=origin, reason=f"invalid context: {_reason(exc)}")])
        entries: list[ContextEntry] = []
        dropped: list[Dropped] = []
        for index, raw in enumerate(envelope.items, 1):
            try:
                entries.append(
                    ContextEntry(envelope.source, index, ContextItem.model_validate(raw)),
                )
            except ValidationError as exc:
                dropped.append(Dropped(source=envelope.source, index=index, reason=_reason(exc)))
        return cls([envelope.source], entries, dropped)

    def __add__(self, other: Self) -> Self:
        """Combine two results, keeping their order."""
        return type(self)(
            [*self.sources, *other.sources],
            [*self.entries, *other.entries],
            [*self.dropped, *other.dropped],
        )
