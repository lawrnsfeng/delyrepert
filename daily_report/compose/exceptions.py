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


class InvalidEditError(ComposeError):
    """An edited draft YAML breaks the editable-fields contract; the message names what."""


@dataclass
class NotYamlError(InvalidEditError):
    """The edit file is not YAML."""

    TEMPLATE = "invalid edit: not valid YAML ({reason})"
    reason: str


@dataclass
class BadFieldError(InvalidEditError):
    """A field of the edit YAML is missing, mistyped or too long."""

    TEMPLATE = "invalid edit: {where}: {reason}"
    where: str
    reason: str


@dataclass
class UnknownItemError(InvalidEditError):
    """The edit YAML names an item the draft does not have."""

    TEMPLATE = "invalid edit: unknown item {item}"
    item: str


@dataclass
class MissingItemError(InvalidEditError):
    """The edit YAML dropped an item of the draft."""

    TEMPLATE = "invalid edit: missing item {item}"
    item: str
