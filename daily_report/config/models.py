from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

from daily_report.compose.models import StyleName, Target


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
    """The `[slack.parent_match]` table: how to spot today's standup parent message."""

    user_id: str | None = None
    text_contains: str | None = None

    @model_validator(mode="after")
    def _one_rule(self) -> Self:
        if self.user_id is None and self.text_contains is None:
            msg = "set user_id or text_contains"
            raise ValueError(msg)
        return self


class SlackConfig(BaseModel):
    """The `[slack]` table."""

    channel: str  # channel ID, e.g. C0123456789
    parent_match: ParentMatch
    api_url: str = "https://slack.com/api/"


class PostConfig(BaseModel):
    """The `[post]` table."""

    target: Target = "thread"


class CookConfig(BaseModel):
    """The `[cook]` table: the program `compose --cook` runs to polish the wording."""

    command: list[str] = []
    timeout_s: int = Field(default=60, ge=1)


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

    @property
    def zone(self) -> tzinfo:
        """Zone for "today", snapshot names and working days; the system zone when unset."""
        return self.timezone or datetime.now(tz=UTC).astimezone().tzinfo or UTC
