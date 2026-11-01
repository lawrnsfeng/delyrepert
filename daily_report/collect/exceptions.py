from dataclasses import dataclass

from daily_report.exc import TemplatedError


class CollectError(TemplatedError):
    """Base for collector errors."""


@dataclass
class SearchFailedError(CollectError):
    """The run-wide Jira calls (myself, fields, search) failed; nothing can be reported."""

    TEMPLATE = "Jira search failed: {reason}"
    reason: str
