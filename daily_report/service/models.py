import datetime as dt
from typing import Literal

from pydantic import BaseModel

from daily_report.compose.models import Posted

SECRET_NAMES = ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_USER_TOKEN", "JIRA_API_TOKEN")

type Trigger = Literal["scheduled", "cli", "slack"]
type RunStatus = Literal[
    "running",
    "pending",
    "posted",
    "skipped",
    "expired",
    "failed",
    "paused_skip",
]


class DmRef(BaseModel):
    """The approval DM of a run (written by the Slack listener)."""

    channel: str
    ts: str


class RunRecord(BaseModel):
    """`<state_dir>/runs/<run-id>.json`: one standup run."""

    schema_version: Literal[1] = 1
    run_id: str
    trigger: Trigger
    started_at: dt.datetime
    status: RunStatus
    draft_path: str | None = None
    dm: DmRef | None = None
    posted: Posted | None = None
    error: str | None = None


class Paused(BaseModel):
    """`<state_dir>/paused.json`: scheduled runs are skipped while it exists."""

    paused_at: dt.datetime
    until: dt.date | None = None
    by: Literal["cli", "slack"]
