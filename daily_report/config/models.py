import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, tzinfo
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

from daily_report.compose.models import StyleName, Target
from daily_report.config.exceptions import UnknownThreadError

_THREAD_NAME = re.compile(r"[a-z][a-z0-9_-]*")
_MATCH_FIELDS = ("user_id", "bot_id", "username", "text_contains", "text_regex")


class JiraConfig(BaseModel):
    """The `[jira]` table."""

    base_url: str
    email: str
    blocked_statuses: list[str] = ["Blocked"]
    flagged_field: str = "Flagged"  # "" disables the Flagged check
    flagged_value: str = "Impediment"


class RenderConfig(BaseModel):
    """The `[render]` table."""

    style: StyleName | None = None  # unset: every preview shows all seven styles
    builder_team: str | None = None  # Slack team ID: Builder links open in that workspace


class EmojiConfig(BaseModel):
    """The `[emoji]` table."""

    blocked_animated: str | None = None  # custom emoji name, without colons


class ContextCommand(BaseModel):
    """One `[[context.commands]]` entry: a program printing context JSON on stdout."""

    name: str
    argv: list[str] = Field(min_length=1)


class ContextConfig(BaseModel):
    """The `[context]` table."""

    commands: list[ContextCommand] = []


class ParentMatch(BaseModel):
    """How to spot a standup parent message; every field that is set must match."""

    user_id: str | None = None
    bot_id: str | None = None
    username: str | None = None
    text_contains: str | None = None
    text_regex: str | None = None

    @model_validator(mode="after")
    def _one_rule(self) -> Self:
        if not self.rules():
            msg = "set user_id, bot_id, username, text_contains or text_regex"
            raise ValueError(msg)
        if self.text_regex is not None:
            try:
                re.compile(self.text_regex)
            except re.error as exc:
                msg = f"text_regex is not a valid regex: {exc}"
                raise ValueError(msg) from exc
        return self

    def rules(self) -> dict[str, str]:
        """Return the set fields, in declaration order."""
        return {
            name: value for name in _MATCH_FIELDS if (value := getattr(self, name)) is not None
        }


class ThreadRule(BaseModel):
    """One `[slack.threads.<name>]` table: an extra, named parent pattern."""

    channel: str | None = None  # unset: slack.channel
    parent_match: ParentMatch
    lookback_days: int = Field(default=0, ge=0, le=7)


@dataclass(frozen=True)
class ThreadSpec:
    """A resolved thread pattern: where to look and what to match."""

    name: str
    channel: str
    match: ParentMatch
    lookback_days: int


class SlackConfig(BaseModel):
    """The `[slack]` table."""

    channel: str  # channel ID, e.g. C0123456789
    parent_match: ParentMatch
    lookback_days: int = Field(default=0, ge=0, le=7)  # 0: since local midnight
    threads: dict[str, ThreadRule] = {}
    api_url: str = "https://slack.com/api/"

    @field_validator("threads")
    @classmethod
    def _names(cls, threads: dict[str, ThreadRule]) -> dict[str, ThreadRule]:
        for name in threads:
            if name == "default" or not _THREAD_NAME.fullmatch(name):
                msg = (
                    f"thread name {name!r} must be lowercase letters, digits, - or _ "
                    "and not 'default'"
                )
                raise ValueError(msg)
        return threads

    def spec(self, name: str | None) -> ThreadSpec:
        """Return the pattern `name` (`None` or `default`: the top-level one)."""
        if name is None or name == "default":
            return ThreadSpec("default", self.channel, self.parent_match, self.lookback_days)
        rule = self.threads.get(name)
        if rule is None:
            raise UnknownThreadError(name, ", ".join(["default", *self.threads]))
        return ThreadSpec(
            name,
            rule.channel or self.channel,
            rule.parent_match,
            rule.lookback_days,
        )


class PostConfig(BaseModel):
    """The `[post]` table."""

    target: Target = "thread"


class CookConfig(BaseModel):
    """The `[cook]` table: the program `compose --cook` runs to polish the wording."""

    command: list[str] = []
    timeout_s: int = Field(default=60, ge=1)


class DemoConfig(BaseModel):
    """The `[demo]` table: the test Slack workspace `demo` may post into."""

    slack_team_id: str | None = None  # team_id `auth.test` must report before any post
    channel: str | None = None  # channel ID in the demo workspace
    parent_text: str = "(demo)"  # text the seeded parent contains; the finder matches on it
    state_dir: Path | None = None


class Config(BaseModel):
    """The whole daily-report TOML config file."""

    state_dir: Path | None = None
    timezone: ZoneInfo | None = None
    display_name: str | None = None
    jira: JiraConfig
    render: RenderConfig = RenderConfig()
    emoji: EmojiConfig = EmojiConfig()
    context: ContextConfig = ContextConfig()
    slack: SlackConfig | None = None
    post: PostConfig = PostConfig()
    cook: CookConfig = CookConfig()
    demo: DemoConfig = DemoConfig()
    approval_cutoff: time = time(12, 0)  # a draft is no longer approvable after this, local time
    secrets_file: Path | None = None
    schedule: str = "Mon..Fri *-*-* 09:00:00"  # systemd OnCalendar= expression
    owner_slack_user_id: str | None = None  # the only Slack user the listener obeys

    @property
    def zone(self) -> tzinfo:
        """Zone for "today", snapshot names and working days; the system zone when unset."""
        return self.timezone or datetime.now(tz=UTC).astimezone().tzinfo or UTC
