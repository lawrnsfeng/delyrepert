import datetime as dt
import json
from typing import Any, Literal, Self
from urllib.parse import quote

from pydantic import BaseModel, Field

from daily_report.collect.models import SUMMARY_MAX, Bucket, CollectWarning, Item, Report
from daily_report.context.models import ContextEntry, ContextResult, Dropped
from daily_report.notes.models import Note

# Plain aliases where typer reads the type: it cannot unwrap a `type` statement.
StyleName = Literal["bluf", "ytb", "table", "markdown", "compact", "cards", "richlist"]
type Kind = Literal[
    "blocked",
    "at_risk",
    "comments",
    "replied",
    "done",
    "handed_off",
    "in_progress",
    "context",
]
type Group = Literal["attention", "since", "today"]
type Target = Literal["thread", "thread_broadcast", "channel"]
type Block = dict[str, Any]

STYLE_NAMES: tuple[StyleName, ...] = (
    "bluf",
    "ytb",
    "table",
    "markdown",
    "compact",
    "cards",
    "richlist",
)
BUILDER_URL = "https://app.slack.com/block-kit-builder/#"
MAX_BLOCKS = 50
SECTION_TEXT_MAX = 3000
MARKDOWN_TEXT_MAX = 12000
TABLE_ROWS_MAX = 100

# Collector D10: where each report bucket lands (a `comment` from others moves to attention).
_BUCKET_PLACES: dict[Bucket, tuple[Kind, Group]] = {
    "blocked": ("blocked", "attention"),
    "at_risk": ("at_risk", "attention"),
    "done": ("done", "since"),
    "in_progress": ("in_progress", "today"),
    "comment": ("replied", "since"),
    "left_scope": ("handed_off", "since"),
}
_CONTEXT_PRIORITY_PLACES: dict[str, tuple[Kind, Group]] = {
    "blocked": ("blocked", "attention"),
    "at_risk": ("at_risk", "attention"),
}
_CONTEXT_GROUP_PLACES: dict[str, tuple[Kind, Group]] = {
    "attention": ("context", "attention"),
    "done": ("done", "since"),
    "today": ("in_progress", "today"),
    "note": ("context", "today"),
}


def day_label(day: dt.date) -> str:
    """Return `Fri 30 Oct`."""
    return f"{day:%a} {day.day} {day:%b}"


class DraftItem(BaseModel):
    """One line of the standup: a Jira ticket (`id` is its key) or a context item."""

    id: str
    kind: Kind
    group: Group
    url: str | None
    title: str
    summary: str = Field(max_length=SUMMARY_MAX)
    comment_count: int = 0
    first_author: str | None = None
    hidden: bool = False

    @classmethod
    def from_report(cls, item: Item) -> Self:
        """Map a collector item to its kind and group (collector D10)."""
        others = [
            event.author.display_name
            for event in item.events
            if event.type == "comment" and not event.author.is_me
        ]
        place: tuple[Kind, Group] = (
            ("comments", "attention")
            if item.bucket == "comment" and others
            else _BUCKET_PLACES[item.bucket]
        )
        kind, group = place
        return cls(
            id=item.key,
            kind=kind,
            group=group,
            url=item.url,
            title=item.title,
            summary=f"{len(others)} new from {others[0]}" if kind == "comments" else item.summary,
            comment_count=len(others),
            first_author=others[0] if others else None,
        )

    @classmethod
    def from_context(cls, entry: ContextEntry) -> Self:
        """Map a context item: its priority wins, else its group decides."""
        item = entry.item
        kind, group = _CONTEXT_PRIORITY_PLACES.get(
            item.priority or "normal",
            _CONTEXT_GROUP_PLACES[item.group],
        )
        return cls(
            id=f"{entry.source}#{entry.index}",
            kind=kind,
            group=group,
            url=item.link,
            title=item.text,
            summary=entry.source,
        )

    @property
    def from_jira(self) -> bool:
        """Whether this item is a Jira ticket (context ids carry a `#`, Jira keys never do)."""
        return "#" not in self.id


class Posted(BaseModel):
    """Where a draft was posted."""

    channel: str
    ts: str
    permalink: str


class Draft(BaseModel):
    """`<state_dir>/drafts/<run-id>.json`: one standup before and after posting."""

    schema_version: Literal[1] = 1
    run_id: str
    created_at: dt.datetime
    as_of: dt.datetime
    baseline_date: dt.date | None
    headline: str
    items: list[DraftItem]
    notes: list[Note]
    sources: list[str]
    warnings: list[CollectWarning]
    dropped: list[Dropped]
    render_notice: str | None = None
    cooked: bool = False
    cook_error: str | None = None
    style: StyleName | None = None
    target: Target = "thread"
    posted: Posted | None = None

    @classmethod
    def build(
        cls,
        report: Report,
        notes: list[Note],
        context: ContextResult,
        *,
        created_at: dt.datetime,
        zone: dt.tzinfo,
    ) -> Self:
        """Compose the draft from the report, the pending notes and the context."""
        return cls(
            run_id=f"{created_at.astimezone(zone):%Y%m%d-%H%M%S}",
            created_at=created_at,
            as_of=report.as_of,
            baseline_date=report.baseline.date if report.baseline else None,
            headline=f"Standup · {day_label(report.as_of.astimezone(zone).date())}",
            items=[
                *(DraftItem.from_report(item) for item in report.items),
                *(DraftItem.from_context(entry) for entry in context.entries),
            ],
            notes=notes,
            sources=["Jira", *context.sources],
            warnings=report.warnings,
            dropped=context.dropped,
        )

    @property
    def visible(self) -> list[DraftItem]:
        """Items that are not hidden."""
        return [item for item in self.items if not item.hidden]


class SlackPayload(BaseModel):
    """A `chat.postMessage` body; `notice` says why another style stood in (never sent)."""

    text: str
    blocks: list[Block]
    unfurl_links: bool = False
    unfurl_media: bool = False
    notice: str | None = Field(default=None, exclude=True)

    @property
    def as_json(self) -> str:
        """Return the payload file content (`--out`, goldens)."""
        return self.model_dump_json(indent=2) + "\n"

    @property
    def builder_url(self) -> str:
        """Return a Block Kit Builder link previewing the blocks."""
        blocks = json.dumps({"blocks": self.blocks}, ensure_ascii=False, separators=(",", ":"))
        return BUILDER_URL + quote(blocks, safe="")

    @property
    def overflow(self) -> str | None:
        """Name the first Slack limit the payload breaks, or `None` when it fits."""
        # shortcut: rich_text has no documented size limit, so richlist never falls back;
        # add one if Slack ever rejects a long list.
        if len(self.blocks) > MAX_BLOCKS:
            return f"{MAX_BLOCKS} blocks"
        for block in self.blocks:
            if block["type"] == "section" and len(block["text"]["text"]) > SECTION_TEXT_MAX:
                return f"{SECTION_TEXT_MAX} characters in a section"
            if block["type"] == "markdown" and len(block["text"]) > MARKDOWN_TEXT_MAX:
                return f"{MARKDOWN_TEXT_MAX} characters in a markdown block"
            if block["type"] == "table" and len(block["rows"]) > TABLE_ROWS_MAX:
                return f"{TABLE_ROWS_MAX} table rows"
        return None
