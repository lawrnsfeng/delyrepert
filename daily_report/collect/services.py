import datetime as dt
import math
from dataclasses import dataclass
from pathlib import Path

from daily_report.collect.exceptions import SearchFailedError
from daily_report.collect.models import (
    EXCERPT_MAX,
    SUMMARY_MAX,
    Author,
    Baseline,
    Bucket,
    CollectWarning,
    Event,
    Item,
    Report,
    Snapshot,
    SnapshotEntry,
    Status,
    Window,
    clip,
)
from daily_report.config.models import Config, JiraConfig
from daily_report.effects.models import Intent
from daily_report.effects.services import Effects
from daily_report.jira.exceptions import JiraRequestError
from daily_report.jira.models import ChangeHistory, IssueFields, JiraComment, JiraIssue, JiraUser
from daily_report.jira.services import JiraClient
from daily_report.retry import RetryPolicy

_SNAPSHOT_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json"
_FIRST_RUN_WINDOW = dt.timedelta(days=1)
_JQL_SLACK_MINUTES = 5
_SATURDAY = 5


@dataclass(frozen=True)
class DatedSnapshot:
    """A stored snapshot and the day it is named after."""

    day: dt.date
    snapshot: Snapshot


class SnapshotStore:
    """Reads and writes `<state_dir>/snapshots/<YYYY-MM-DD>.json`; writes go through Effects."""

    def __init__(self, state_dir: Path, effects: Effects) -> None:
        """Store snapshots under `state_dir`."""
        self._state_dir = state_dir
        self._effects = effects

    def latest_before(self, day: dt.date) -> DatedSnapshot | None:
        """Return the newest snapshot dated before `day`."""
        folder = self._state_dir / "snapshots"
        days = sorted(dt.date.fromisoformat(path.stem) for path in folder.glob(_SNAPSHOT_GLOB))
        earlier = [stored for stored in days if stored < day]
        if not earlier:
            return None
        text = (folder / f"{earlier[-1].isoformat()}.json").read_text(encoding="utf-8")
        return DatedSnapshot(earlier[-1], Snapshot.model_validate_json(text))

    async def save(self, day: dt.date, snapshot: Snapshot) -> None:
        """Write `snapshot` as the snapshot of `day`, replacing any earlier one."""
        relative = f"snapshots/{day.isoformat()}.json"
        path = self._state_dir / relative
        text = snapshot.model_dump_json(indent=2) + "\n"

        async def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        await self._effects.perform(Intent("write", relative), write)


@dataclass(frozen=True)
class IssueActivity:
    """One issue with its baseline entry, window and fetched history."""

    issue: JiraIssue
    previous: SnapshotEntry | None
    start: dt.datetime
    end: dt.datetime
    histories: list[ChangeHistory]
    comments: list[JiraComment]

    def covers(self, at: dt.datetime) -> bool:
        """Whether `at` falls inside this issue's window (start exclusive, end inclusive)."""
        return self.start < at <= self.end


@dataclass(frozen=True)
class ItemRules:
    """Per-run settings that turn issue activity into report items."""

    owner_id: str
    base_url: str
    blocked_statuses: frozenset[str]
    flagged_field_id: str | None
    flagged_value: str
    due_cutoff: dt.date
    has_baseline: bool

    def item(self, activity: IssueActivity) -> Item | None:
        """Build the item for `activity`, or `None` when nothing worth reporting happened."""
        fields = activity.issue.fields
        status_events = [
            Event(
                at=history.created,
                type="status",
                author=self._author(history.author),
                from_=change.from_string,
                to=change.to_string,
            )
            for history in activity.histories
            if activity.covers(history.created)
            for change in history.items
            if change.field == "status"
        ]
        comment_events = [
            Event(
                at=comment.created,
                type="comment",
                author=self._author(comment.author),
                comment_id=comment.id,
                excerpt=clip(" ".join(comment.body.plain_text.split()), EXCERPT_MAX),
            )
            for comment in activity.comments
            if activity.covers(comment.created) and comment.author.account_type != "app"
        ]
        entered = self.has_baseline and activity.previous is None
        done = fields.status.status_category.key == "done"
        at_risk = not done and fields.duedate is not None and fields.duedate <= self.due_cutoff
        if not (status_events or comment_events or entered or at_risk):
            return None
        parts: list[str] = []
        if status_events:
            parts.append(f"{status_events[0].from_} → {status_events[-1].to}")
        elif entered:
            parts.append(f"Newly assigned · {fields.status.name}")
        if comment_events:
            count = len(comment_events)
            parts.append(f"{count} new comment{'' if count == 1 else 's'}")
        if at_risk:
            parts.append(f"Due {fields.duedate}")
        events = sorted([*status_events, *comment_events], key=lambda event: event.at)
        return Item(
            key=activity.issue.key,
            url=f"{self.base_url}/browse/{activity.issue.key}",
            title=fields.summary,
            status=Status.from_jira(fields.status),
            previous_status=activity.previous.status if activity.previous else None,
            scope_change="entered" if entered else "none",
            bucket=self._bucket(
                fields,
                done=done,
                at_risk=at_risk,
                changed=bool(status_events) or entered,
            ),
            jira_priority=fields.priority.name if fields.priority else None,
            summary=clip(" · ".join(parts), SUMMARY_MAX),
            events=events,
        )

    def left(self, key: str, entry: SnapshotEntry) -> Item:
        """Build the item for a baseline issue no longer assigned to the owner."""
        # The snapshot keeps no title, so the key stands in for it.
        return Item(
            key=key,
            url=f"{self.base_url}/browse/{key}",
            title=key,
            status=entry.status,
            previous_status=entry.status,
            scope_change="left",
            bucket="left_scope",
            jira_priority=None,
            summary="No longer assigned to you",
            events=[],
        )

    def _bucket(self, fields: IssueFields, *, done: bool, at_risk: bool, changed: bool) -> Bucket:
        """First match wins: blocked, done, at_risk, in_progress, comment."""
        if self._blocked(fields):
            return "blocked"
        if done:
            return "done"
        if at_risk:
            return "at_risk"
        return "in_progress" if changed else "comment"

    def _blocked(self, fields: IssueFields) -> bool:
        if fields.status.name.lower() in self.blocked_statuses:
            return True
        if self.flagged_field_id is None:
            return False
        return self.flagged_value in fields.flags(self.flagged_field_id)

    def _author(self, user: JiraUser | None) -> Author:
        if user is None:
            return Author(account_id="", display_name="Jira", is_me=False)
        return Author(
            account_id=user.account_id,
            display_name=user.display_name,
            is_me=user.account_id == self.owner_id,
        )


@dataclass(frozen=True)
class _Fetched:
    histories: dict[str, list[ChangeHistory]]
    comments: dict[str, list[JiraComment]]
    warnings: list[CollectWarning]

    @property
    def failed(self) -> set[str]:
        return {warning.key for warning in self.warnings}


class CollectService:
    """Diffs the owner's Jira issues against the latest snapshot before today."""

    def __init__(
        self,
        *,
        client: JiraClient,
        store: SnapshotStore,
        jira: JiraConfig,
        zone: dt.tzinfo,
    ) -> None:
        """Collect through `client`, keeping snapshots in `store`."""
        self._client = client
        self._store = store
        self._jira = jira
        self._zone = zone

    @classmethod
    async def collect(
        cls,
        config: Config,
        *,
        token: str,
        state_dir: Path,
        effects: Effects,
        as_of: dt.datetime,
    ) -> Report:
        """Connect to the configured Jira and build the report as of `as_of`."""
        jira = config.jira
        async with JiraClient.connect(
            base_url=jira.base_url,
            email=jira.email,
            token=token,
            retry=RetryPolicy(),
        ) as client:
            store = SnapshotStore(state_dir, effects)
            return await cls(client=client, store=store, jira=jira, zone=config.zone).run(as_of)

    async def run(self, as_of: dt.datetime) -> Report:
        """Build the report as of `as_of` (naive means the config zone) and save the snapshot."""
        as_of = (as_of if as_of.tzinfo else as_of.replace(tzinfo=self._zone)).astimezone(dt.UTC)
        today = as_of.astimezone(self._zone).date()
        baseline = self._store.latest_before(today)
        entries = baseline.snapshot.issues if baseline else {}
        default_start = baseline.snapshot.taken_at if baseline else as_of - _FIRST_RUN_WINDOW
        oldest = min([default_start, *(entry.checked_at for entry in entries.values())])
        try:
            owner = await self._client.myself()
            flagged_id = await self._flagged_field_id()
            issues = await self._client.search(self._jql(as_of, oldest), self._fields(flagged_id))
        except JiraRequestError as exc:
            raise SearchFailedError(str(exc)) from exc

        windows = {
            issue.key: entries[issue.key].checked_at if issue.key in entries else default_start
            for issue in issues
        }
        active = [issue for issue in issues if issue.fields.updated >= windows[issue.key]]
        fetched = await self._fetch(active)
        failed = fetched.failed
        rules = ItemRules(
            owner_id=owner.account_id,
            base_url=self._jira.base_url.rstrip("/"),
            blocked_statuses=frozenset(name.lower() for name in self._jira.blocked_statuses),
            flagged_field_id=flagged_id,
            flagged_value=self._jira.flagged_value,
            due_cutoff=self._next_working_day(today),
            has_baseline=baseline is not None,
        )
        items = [
            item
            for issue in issues
            if issue.key not in failed
            and (
                item := rules.item(
                    IssueActivity(
                        issue=issue,
                        previous=entries.get(issue.key),
                        start=windows[issue.key],
                        end=as_of,
                        histories=fetched.histories.get(issue.id, []),
                        comments=fetched.comments.get(issue.key, []),
                    ),
                )
            )
        ]
        returned = {issue.key for issue in issues}
        items += [
            rules.left(key, entry)
            for key, entry in entries.items()
            if key not in returned and entry.status.category != "done"
        ]
        await self._save(today, as_of, issues, entries, failed)
        items.sort(key=lambda item: item.sort_key)
        return Report(
            as_of=as_of,
            baseline=Baseline(date=baseline.day, taken_at=baseline.snapshot.taken_at)
            if baseline
            else None,
            window=Window(start=oldest, end=as_of),
            items=items,
            warnings=fetched.warnings,
        )

    async def _flagged_field_id(self) -> str | None:
        if not self._jira.flagged_field:
            return None
        fields = await self._client.fields()
        return next((f.id for f in fields if f.name == self._jira.flagged_field), None)

    async def _fetch(self, active: list[JiraIssue]) -> _Fetched:
        """Fetch changelogs and comments; a failing issue becomes a warning, not an error."""
        warnings: list[CollectWarning] = []
        histories: dict[str, list[ChangeHistory]] = {}
        comments: dict[str, list[JiraComment]] = {}
        try:
            histories = (
                await self._client.changelogs([issue.id for issue in active]) if active else {}
            )
        except JiraRequestError as exc:
            warnings = [
                CollectWarning(key=issue.key, stage="changelog", reason=str(exc))
                for issue in active
            ]
            return _Fetched(histories, comments, warnings)
        for issue in active:
            try:
                comments[issue.key] = await self._client.comments(issue.key)
            except JiraRequestError as exc:
                warnings.append(CollectWarning(key=issue.key, stage="comments", reason=str(exc)))
        return _Fetched(histories, comments, warnings)

    async def _save(
        self,
        today: dt.date,
        as_of: dt.datetime,
        issues: list[JiraIssue],
        entries: dict[str, SnapshotEntry],
        failed: set[str],
    ) -> None:
        """Snapshot fresh entries; failed issues keep their baseline entry to catch up later."""
        # shortcut: a failed issue absent from the baseline is left out, so its first-day events
        # are lost if it fails again tomorrow; store a stub entry if that ever matters.
        snapshot: dict[str, SnapshotEntry] = {}
        for issue in issues:
            if issue.key not in failed:
                snapshot[issue.key] = SnapshotEntry(
                    status=Status.from_jira(issue.fields.status),
                    checked_at=as_of,
                    duedate=issue.fields.duedate,
                )
            elif issue.key in entries:
                snapshot[issue.key] = entries[issue.key]
        await self._store.save(
            today,
            Snapshot(taken_at=as_of, issues=dict(sorted(snapshot.items()))),
        )

    def _jql(self, as_of: dt.datetime, oldest: dt.datetime) -> str:
        """JQL with a relative bound, so the Jira profile's time zone never matters."""
        span = max(dt.datetime.now(tz=dt.UTC), as_of) - oldest
        minutes = math.ceil(span.total_seconds() / 60) + _JQL_SLACK_MINUTES
        return f'assignee = currentUser() AND (statusCategory != Done OR updated >= "-{minutes}m")'

    def _fields(self, flagged_id: str | None) -> list[str]:
        fields = ["summary", "status", "priority", "duedate", "updated"]
        return [*fields, flagged_id] if flagged_id else fields

    def _next_working_day(self, today: dt.date) -> dt.date:
        day = today + dt.timedelta(days=1)
        while day.weekday() >= _SATURDAY:
            day += dt.timedelta(days=1)
        return day
