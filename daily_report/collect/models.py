import datetime as dt
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from daily_report.jira.models import JiraStatus

type Bucket = Literal["blocked", "at_risk", "done", "in_progress", "comment", "left_scope"]
type ScopeChange = Literal["none", "entered", "left"]

BUCKET_ORDER: tuple[Bucket, ...] = (
    "blocked",
    "at_risk",
    "done",
    "in_progress",
    "comment",
    "left_scope",
)
PRIORITY_ORDER = ("Highest", "High", "Medium", "Low", "Lowest")
SUMMARY_MAX = 120
EXCERPT_MAX = 200


def clip(text: str, limit: int) -> str:
    """Return `text` cut to `limit` characters, ending in an ellipsis when cut."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Status(BaseModel):
    """An issue status and its category key (`new`, `indeterminate` or `done`)."""

    name: str
    category: str

    @classmethod
    def from_jira(cls, status: JiraStatus) -> Self:
        """Convert a Jira status."""
        return cls(name=status.name, category=status.status_category.key)


class Author(BaseModel):
    """Who made a change or wrote a comment."""

    account_id: str
    display_name: str
    is_me: bool


class Event(BaseModel):
    """One status change or comment inside an item's window."""

    model_config = ConfigDict(validate_by_name=True, serialize_by_alias=True)

    at: dt.datetime
    type: Literal["status", "comment"]
    author: Author
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    comment_id: str | None = None
    excerpt: str | None = Field(default=None, max_length=EXCERPT_MAX)


class Item(BaseModel):
    """One ticket with everything that happened to it since its baseline."""

    key: str
    url: str
    title: str
    status: Status
    previous_status: Status | None
    scope_change: ScopeChange
    bucket: Bucket
    jira_priority: str | None
    due: dt.date | None = None
    summary: str = Field(max_length=SUMMARY_MAX)
    events: list[Event]

    @property
    def sort_key(self) -> tuple[int, int, str, int]:
        """Order by bucket, then Jira priority (none last), then key (numerically)."""
        priority = self.jira_priority or ""
        rank = (
            PRIORITY_ORDER.index(priority) if priority in PRIORITY_ORDER else len(PRIORITY_ORDER)
        )
        project, _, number = self.key.rpartition("-")
        return BUCKET_ORDER.index(self.bucket), rank, project, int(number)


class Baseline(BaseModel):
    """The snapshot a report diffs against."""

    date: dt.date
    taken_at: dt.datetime


class Window(BaseModel):
    """The span of time a report covers."""

    start: dt.datetime
    end: dt.datetime


class CollectWarning(BaseModel):
    """An issue whose changelog or comments could not be fetched."""

    key: str
    stage: Literal["changelog", "comments"]
    reason: str


class Report(BaseModel):
    """The collector's output."""

    schema_version: Literal[1] = 1
    as_of: dt.datetime
    baseline: Baseline | None
    window: Window
    items: list[Item]
    warnings: list[CollectWarning]


class SnapshotEntry(BaseModel):
    """What the collector saw of one issue."""

    status: Status
    checked_at: dt.datetime
    duedate: dt.date | None


class Snapshot(BaseModel):
    """`<state_dir>/snapshots/<date>.json`."""

    taken_at: dt.datetime
    issues: dict[str, SnapshotEntry]
