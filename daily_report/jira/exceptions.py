from dataclasses import dataclass

from daily_report.exc import TemplatedError


class JiraError(TemplatedError):
    """Base for Jira client errors."""


@dataclass
class JiraAuthError(JiraError):
    """Jira answered 401 or 403."""

    TEMPLATE = "Jira rejected credentials for {email} at {base_url} ({status})"
    email: str
    base_url: str
    status: int


class JiraRequestError(JiraError):
    """A Jira request failed for a reason other than credentials."""


@dataclass
class JiraStatusError(JiraRequestError):
    """Jira answered a non-retryable error status."""

    TEMPLATE = "HTTP {status}"
    status: int


@dataclass
class JiraRetriesExhaustedError(JiraRequestError):
    """Jira kept answering a retryable status until the attempts ran out."""

    TEMPLATE = "HTTP {status} after {attempts} attempts"
    status: int
    attempts: int


@dataclass
class JiraUnreachableError(JiraRequestError):
    """The HTTP connection to Jira failed."""

    TEMPLATE = "cannot reach Jira at {base_url}: {reason}"
    base_url: str
    reason: str
