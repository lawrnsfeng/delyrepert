from dataclasses import dataclass

from daily_report.exc import TemplatedError


class CookRejectedError(TemplatedError):
    """The cook pass failed or returned something unusable; the script text stays.

    The message is what `Draft.cook_error` records and the preview shows.
    """


@dataclass
class CookStartError(CookRejectedError):
    """`cook.command` could not be started."""

    TEMPLATE = "cook failed to start: {reason}"
    reason: str


@dataclass
class CookTimeoutError(CookRejectedError):
    """`cook.command` ran past `cook.timeout_s`."""

    TEMPLATE = "cook timed out after {seconds}s"
    seconds: int


@dataclass
class CookExitError(CookRejectedError):
    """`cook.command` exited non-zero."""

    TEMPLATE = "cook exited {code}"
    code: int


@dataclass
class CookInvalidJsonError(CookRejectedError):
    """`cook.command` did not print JSON."""

    TEMPLATE = "cook returned invalid JSON"


@dataclass
class CookInvalidOutputError(CookRejectedError):
    """The cook JSON breaks the `{headline, summaries}` shape."""

    TEMPLATE = "cook output invalid: {where} {reason}"
    where: str
    reason: str


@dataclass
class CookUnknownItemError(CookRejectedError):
    """The cook output has a summary for an item that is not in the draft."""

    TEMPLATE = "cook added unknown item {item}"
    item: str


@dataclass
class CookDroppedItemError(CookRejectedError):
    """The cook output has no summary for a visible item."""

    TEMPLATE = "cook dropped item {item}"
    item: str
