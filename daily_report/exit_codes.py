from enum import IntEnum
from typing import NoReturn

import typer

from daily_report.exc import TemplatedError


class ExitCode(IntEnum):
    """Process exit codes shared by every daily-report command."""

    OK = 0
    CONFIG = 2
    JIRA_AUTH = 3
    JIRA_SEARCH = 4
    PRECONDITION = 5  # demo: workspace mismatch; service install: linger off
    SECRETS = 6
    RUN_IN_PROGRESS = 7
    NOT_PENDING = 8
    SLACK_API = 9
    OWNER_MUST_CHOOSE = 10


def fail(exc: TemplatedError, code: ExitCode) -> NoReturn:
    """Print `exc` on stderr and exit with `code`."""
    typer.echo(str(exc), err=True)
    raise typer.Exit(code) from exc
