from datetime import UTC, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel


class JiraConfig(BaseModel):
    """The `[jira]` table."""

    base_url: str
    email: str
    blocked_statuses: list[str] = ["Blocked"]
    flagged_field: str = "Flagged"  # "" disables the Flagged check
    flagged_value: str = "Impediment"


class Config(BaseModel):
    """The whole daily-report TOML config file."""

    state_dir: Path | None = None
    timezone: ZoneInfo | None = None
    jira: JiraConfig

    @property
    def zone(self) -> tzinfo:
        """Zone for "today", snapshot names and working days; the system zone when unset."""
        return self.timezone or datetime.now(tz=UTC).astimezone().tzinfo or UTC
