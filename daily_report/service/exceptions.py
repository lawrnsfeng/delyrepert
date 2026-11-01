from dataclasses import dataclass

from daily_report.config.exceptions import ConfigError
from daily_report.exc import TemplatedError


class ServiceError(TemplatedError):
    """Base for standup service errors."""


@dataclass
class PastDateError(ConfigError):
    """`pause --until` names a day before today."""

    TEMPLATE = "--until {until} is before today ({today})"
    until: str
    today: str


class SecretsError(ServiceError):
    """Base for secrets file errors; every one exits 6."""


@dataclass
class UnreadableSecretsError(SecretsError):
    """The secrets file cannot be read."""

    TEMPLATE = "cannot read secrets file {path}: {reason}"
    path: str
    reason: str


@dataclass
class InsecureSecretsError(SecretsError):
    """The secrets file is readable by group or others."""

    TEMPLATE = "secrets file {path} must be 0600"
    path: str


@dataclass
class MissingSecretsError(SecretsError):
    """The secrets file lacks variables; only their names are shown."""

    TEMPLATE = "secrets file {path} lacks {names}"
    path: str
    names: str


@dataclass
class RunInProgressError(ServiceError):
    """Another run holds `run.lock` (exit 7)."""

    TEMPLATE = "run {run_id} already in progress"
    run_id: str


@dataclass
class NotPendingError(ServiceError):
    """The run is posted, skipped, expired, failed or unknown (exit 8)."""

    TEMPLATE = "run {run_id} is {status}, not pending"
    run_id: str
    status: str


@dataclass
class ExpiredError(ServiceError):
    """The approval cutoff passed (exit 8)."""

    TEMPLATE = "draft {run_id} expired at {cutoff}"
    run_id: str
    cutoff: str
