from enum import IntEnum


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
