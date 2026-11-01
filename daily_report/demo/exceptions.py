from dataclasses import dataclass

from daily_report.config.exceptions import ConfigError
from daily_report.exc import TemplatedError


@dataclass
class DemoKeyUnsetError(ConfigError):
    """A live demo needs a `[demo]` key the config file does not set."""

    TEMPLATE = "demo.{key} is not set in the config file; a Slack demo needs it"
    key: str


@dataclass
class DemoParentTextError(ConfigError):
    """`demo.parent_text` does not occur in the seeded parent, so the finder could not find it."""

    TEMPLATE = "demo.parent_text {wanted!r} is not in the seeded parent text {seeded!r}"
    wanted: str
    seeded: str


@dataclass
class DemoFixturesError(ConfigError):
    """The fixtures dir does not exist."""

    TEMPLATE = "demo fixtures dir {path} does not exist"
    path: str


@dataclass
class CookUnsetError(ConfigError):
    """`--cook` was given but `cook.command` is empty."""

    TEMPLATE = "--cook needs cook.command in the config file"


@dataclass
class WorkspaceMismatchError(TemplatedError):
    """The demo token belongs to another Slack workspace (exit 5)."""

    TEMPLATE = "refusing: token team {actual} is not demo.slack_team_id {expected}"
    actual: str
    expected: str
