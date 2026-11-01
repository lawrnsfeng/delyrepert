from dataclasses import dataclass

from daily_report.exc import TemplatedError


class ComposeError(TemplatedError):
    """Base for compose errors."""


@dataclass
class InvalidReportError(ComposeError):
    """`--report` does not hold a collector `Report`."""

    TEMPLATE = "invalid report {name}: {reason}"
    name: str
    reason: str


@dataclass
class InvalidDraftError(ComposeError):
    """A run id names no readable draft."""

    TEMPLATE = "cannot read draft {run_id}: {reason}"
    run_id: str
    reason: str
