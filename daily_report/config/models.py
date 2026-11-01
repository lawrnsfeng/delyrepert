from datetime import UTC, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from daily_report.compose.models import StyleName


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


class Config(BaseModel):
    """The whole daily-report TOML config file."""

    state_dir: Path | None = None
    timezone: ZoneInfo | None = None
    display_name: str | None = None
    jira: JiraConfig
    render: RenderConfig = RenderConfig()
    emoji: EmojiConfig = EmojiConfig()
    context: ContextConfig = ContextConfig()

    @property
    def zone(self) -> tzinfo:
        """Zone for "today", snapshot names and working days; the system zone when unset."""
        return self.timezone or datetime.now(tz=UTC).astimezone().tzinfo or UTC
