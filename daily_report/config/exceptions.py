from dataclasses import dataclass

from daily_report.exc import TemplatedError


class ConfigError(TemplatedError):
    """Base for configuration errors; every one exits 2."""


@dataclass
class ConfigFileUnsetError(ConfigError):
    """No config file source is set."""

    TEMPLATE = "no config file: pass --config or set DAILY_REPORT_CONFIG"


@dataclass
class InvalidConfigError(ConfigError):
    """The config file cannot be read or does not validate."""

    TEMPLATE = "invalid config file {path}: {reason}"
    path: str
    reason: str


@dataclass
class StateDirUnsetError(ConfigError):
    """No state dir source is set."""

    TEMPLATE = (
        "no state dir: pass --state-dir, set DAILY_REPORT_STATE_DIR "
        "or set state_dir in the config file"
    )


@dataclass
class SecretsFileUnsetError(ConfigError):
    """No secrets file source is set."""

    TEMPLATE = (
        "no secrets file: pass --secrets-file, set DAILY_REPORT_SECRETS_FILE "
        "or set secrets_file in the config file"
    )


@dataclass
class TokenUnsetError(ConfigError):
    """`JIRA_API_TOKEN` is not set."""

    TEMPLATE = "JIRA_API_TOKEN is not set"


@dataclass
class SlackTokenUnsetError(ConfigError):
    """`SLACK_USER_TOKEN` is not set."""

    TEMPLATE = "SLACK_USER_TOKEN is not set"


@dataclass
class SlackUnsetError(ConfigError):
    """The config file has no `[slack]` table."""

    TEMPLATE = "no [slack] table in the config file: set slack.channel and slack.parent_match"


@dataclass
class UnknownThreadError(ConfigError):
    """A thread name that is neither `default` nor a `[slack.threads.<name>]` key."""

    TEMPLATE = "unknown thread {name}; known: {known}"
    name: str
    known: str


@dataclass
class BadPermalinkError(ConfigError):
    """The value given to pin is not a Slack message link."""

    TEMPLATE = "not a Slack message link: {value}"
    value: str


@dataclass
class InvalidPinError(ConfigError):
    """`thread_pin.json` exists but does not validate."""

    TEMPLATE = "invalid thread pin {path}: run `daily-report thread clear`"
    path: str


@dataclass
class CookUnsetError(ConfigError):
    """`--cook` was given but `cook.command` is empty."""

    TEMPLATE = "cook.command unset: set it in the config file to use --cook"


@dataclass
class BotTokenUnsetError(ConfigError):
    """`SLACK_BOT_TOKEN` is not set."""

    TEMPLATE = "SLACK_BOT_TOKEN is not set"


@dataclass
class AppTokenUnsetError(ConfigError):
    """`SLACK_APP_TOKEN` is not set."""

    TEMPLATE = "SLACK_APP_TOKEN is not set"


@dataclass
class OwnerUnsetError(ConfigError):
    """The config file has no `owner_slack_user_id`."""

    TEMPLATE = "owner_slack_user_id unset: set it in the config file to your Slack user ID"
