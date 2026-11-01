import datetime as dt
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from daily_report.collect.models import Item, Report, Status
from daily_report.effects.models import Intent
from daily_report.jira.fake import FakeJira
from daily_report.retry import RetryPolicy
from tests.builders import (
    ALICE,
    BOT,
    OWNER,
    changelog,
    comment,
    issue,
    status_change,
    write_snapshot,
)
from tests.collect.helpers import Collector, items_by_key


def snapshot_keys(state_dir: Path, day: str) -> list[str]:
    """Return the issue keys stored in the snapshot of `day`."""
    data = json.loads((state_dir / "snapshots" / f"{day}.json").read_text(encoding="utf-8"))
    return list(data["issues"])


@pytest.mark.asyncio
async def test_ac4_status_change_since_snapshot(collector: Collector, state_dir: Path) -> None:
    """A status change after the snapshot becomes one in_progress item."""
    write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress"})

    items = items_by_key(await collector.run())

    assert list(items) == ["ABC-1"]
    item = items["ABC-1"]
    assert item["previous_status"]["name"] == "In Progress"
    assert item["status"]["name"] == "In Review"
    assert item["bucket"] == "in_progress"
    assert item["summary"] == "In Progress → In Review"
    assert [(e["type"], e["from"], e["to"]) for e in item["events"]] == [
        ("status", "In Progress", "In Review"),
    ]


@pytest.mark.asyncio
async def test_ac5_monday_diffs_against_friday(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """With only Friday's snapshot, Monday's run diffs against it and sees Saturday's change."""
    write_snapshot(state_dir, "2026-10-30", {"ABC-1": "In Progress"})
    fake_jira.fixtures.issues = [issue("ABC-1", "In Review", updated="2026-10-31T12:00:00Z")]
    fake_jira.fixtures.changelogs = [
        changelog("ABC-1", status_change("2026-10-31T12:00:00Z", "In Progress", "In Review")),
    ]

    report = await collector.run("2026-11-02T09:00:00+00:00")

    assert report.baseline is not None
    assert report.baseline.date == dt.date(2026, 10, 30)
    events = items_by_key(report)["ABC-1"]["events"]
    assert [e["at"] for e in events] == ["2026-10-31T12:00:00Z"]


@pytest.mark.asyncio
async def test_ac6_same_day_rerun_is_idempotent(collector: Collector, state_dir: Path) -> None:
    """Two runs with the same as-of give the same items and one snapshot per day."""
    write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress"})

    first = await collector.run()
    second = await collector.run()

    assert json.dumps(items_by_key(first)) == json.dumps(items_by_key(second))
    assert sorted(p.name for p in (state_dir / "snapshots").iterdir()) == [
        "2026-11-02.json",
        "2026-11-03.json",
    ]


@pytest.mark.asyncio
async def test_ac7_first_run_covers_the_last_24_hours(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """With no snapshot, the window is the 24 hours before as-of."""
    fake_jira.fixtures.issues = [issue("ABC-1", "Done", updated="2026-11-02T10:00:00Z")]
    fake_jira.fixtures.changelogs = [
        changelog(
            "ABC-1",
            status_change("2026-11-02T08:00:00Z", "To Do", "In Progress"),
            status_change("2026-11-02T10:00:00Z", "In Progress", "Done"),
        ),
    ]

    report = await collector.run()

    assert report.baseline is None
    assert report.window.start == dt.datetime(2026, 11, 2, 9, tzinfo=dt.UTC)
    events = items_by_key(report)["ABC-1"]["events"]
    assert [e["at"] for e in events] == ["2026-11-02T10:00:00Z"]
    assert (state_dir / "snapshots" / "2026-11-03.json").is_file()


@pytest.mark.asyncio
async def test_ac8_ticket_without_events_or_due_date_has_no_item(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """A ticket updated without status changes or comments produces no item."""
    write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress", "ABC-6": "In Progress"})
    fake_jira.fixtures.issues.append(issue("ABC-6", updated="2026-11-02T12:00:00Z"))

    items = items_by_key(await collector.run())

    assert "ABC-6" not in items


@pytest.mark.asyncio
async def test_ac9_comments_are_tagged_and_app_comments_dropped(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """Owner and human comments count, tagged is_me; app comments are dropped."""
    write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress", "ABC-4": "In Progress"})
    long_text = "word " * 80
    fake_jira.fixtures.issues.append(issue("ABC-4", updated="2026-11-02T12:00:00Z"))
    fake_jira.fixtures.comments["ABC-4"] = [
        comment("c1", "2026-11-02T10:00:00Z", "Pushed a fix", OWNER),
        comment("c2", "2026-11-02T11:00:00Z", long_text, ALICE),
        comment("c3", "2026-11-02T12:00:00Z", "Build passed", BOT),
    ]

    item = items_by_key(await collector.run())["ABC-4"]

    assert item["bucket"] == "comment"
    assert item["summary"] == "2 new comments"
    events = item["events"]
    assert [(e["comment_id"], e["author"]["is_me"]) for e in events] == [
        ("c1", True),
        ("c2", False),
    ]
    assert events[0]["excerpt"] == "Pushed a fix"
    assert len(events[1]["excerpt"]) == 200
    assert events[1]["excerpt"].startswith("word word")


@pytest.mark.asyncio
async def test_ac10_blocked_status_matches_case_insensitively(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """A change into "blocked" lands in the blocked bucket with the default config."""
    write_snapshot(state_dir, "2026-11-02", {"ABC-3": "In Progress"})
    fake_jira.fixtures.issues = [issue("ABC-3", "blocked")]
    fake_jira.fixtures.changelogs = [
        changelog("ABC-3", status_change("2026-11-02T15:00:00Z", "In Progress", "blocked")),
    ]

    items = items_by_key(await collector.run())

    assert items["ABC-3"]["bucket"] == "blocked"


@pytest.mark.parametrize("flagged", [[{"value": "Impediment"}], ["Impediment"]])
@pytest.mark.asyncio
async def test_ac11_flagged_impediment_is_blocked(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
    flagged: list[object],
) -> None:
    """An In Progress ticket flagged as Impediment with a new comment is blocked."""
    write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress", "ABC-9": "In Progress"})
    fake_jira.fixtures.issues.append(issue("ABC-9", flagged=flagged))
    fake_jira.fixtures.comments["ABC-9"] = [comment("c1", "2026-11-02T10:00:00Z", "Waiting")]

    report = await collector.run()

    assert items_by_key(report)["ABC-9"]["bucket"] == "blocked"
    search = next(r for r in fake_jira.requests if r.path.endswith("/search/jql"))
    assert search.body is not None
    assert "customfield_10021" in search.body["fields"]


@pytest.mark.asyncio
async def test_ac11_site_without_flagged_field_is_not_an_error(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """With no Flagged field on the site, the flag check is skipped silently."""
    write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress", "ABC-9": "In Progress"})
    fake_jira.fixtures.fields = [{"id": "summary", "name": "Summary"}]
    fake_jira.fixtures.issues.append(issue("ABC-9", flagged=[{"value": "Impediment"}]))
    fake_jira.fixtures.comments["ABC-9"] = [comment("c1", "2026-11-02T10:00:00Z", "Waiting")]

    report = await collector.run()

    assert report.warnings == []
    assert items_by_key(report)["ABC-9"]["bucket"] == "comment"


@pytest.mark.asyncio
async def test_ac12_user_configured_blocked_signals(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """Configured blocked statuses replace the default; an empty flagged_field disables flags."""
    write_snapshot(
        state_dir,
        "2026-11-02",
        {"ABC-20": "In Progress", "ABC-21": "In Progress", "ABC-22": "In Progress"},
    )
    fake_jira.fixtures.issues = [
        issue("ABC-20", "On Hold"),
        issue("ABC-21", "Blocked"),
        issue("ABC-22", flagged=[{"value": "Impediment"}]),
    ]
    fake_jira.fixtures.changelogs = [
        changelog("ABC-20", status_change("2026-11-02T15:00:00Z", "In Progress", "On Hold")),
        changelog("ABC-21", status_change("2026-11-02T15:00:00Z", "In Progress", "Blocked")),
    ]
    fake_jira.fixtures.comments["ABC-22"] = [comment("c1", "2026-11-02T10:00:00Z", "Hm")]

    report = await collector.run(jira={"blocked_statuses": ["On Hold"], "flagged_field": ""})

    buckets = {key: item["bucket"] for key, item in items_by_key(report).items()}
    assert buckets == {"ABC-20": "blocked", "ABC-21": "in_progress", "ABC-22": "comment"}
    assert not any(r.path.endswith("/field") for r in fake_jira.requests)


@pytest.mark.asyncio
async def test_ac13_due_by_next_working_day_is_at_risk(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """On a Friday, a not-done ticket due Monday is at risk even without events."""
    write_snapshot(
        state_dir,
        "2026-11-05",
        {"ABC-10": "In Progress", "ABC-11": "In Progress", "ABC-12": "Done", "ABC-13": "Review"},
    )
    fake_jira.fixtures.issues = [
        issue("ABC-10", updated="2026-11-01T09:00:00Z", due="2026-11-09"),
        issue("ABC-11", updated="2026-11-01T09:00:00Z", due="2026-11-10"),
        issue("ABC-12", "Done", updated="2026-11-01T09:00:00Z", due="2026-11-01"),
        issue("ABC-13", "Done", updated="2026-11-06T08:00:00Z", due="2026-11-01"),
    ]
    fake_jira.fixtures.changelogs = [
        changelog("ABC-13", status_change("2026-11-06T08:00:00Z", "Review", "Done")),
    ]

    items = items_by_key(await collector.run("2026-11-06T09:00:00+00:00"))

    assert sorted(items) == ["ABC-10", "ABC-13"]
    at_risk = items["ABC-10"]
    assert (at_risk["bucket"], at_risk["events"], at_risk["scope_change"], at_risk["summary"]) == (
        "at_risk",
        [],
        "none",
        "Due 2026-11-09",
    )
    assert items["ABC-13"]["bucket"] == "done"


@pytest.mark.asyncio
async def test_ac14_unassigned_ticket_left_scope(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """A not-done baseline ticket missing from today's search left scope; a done one vanishes."""
    write_snapshot(
        state_dir,
        "2026-11-02",
        {"ABC-1": "In Progress", "ABC-7": "In Progress", "ABC-8": "Done"},
    )
    fake_jira.fixtures.issues = [issue("ABC-1", updated="2026-11-01T09:00:00Z")]

    items = items_by_key(await collector.run())

    assert list(items) == ["ABC-7"]
    left = items["ABC-7"]
    assert (left["bucket"], left["scope_change"], left["summary"]) == (
        "left_scope",
        "left",
        "No longer assigned to you",
    )
    assert not any(r.path.endswith("/bulkfetch") for r in fake_jira.requests)


@pytest.mark.asyncio
async def test_ac15_newly_assigned_ticket_entered(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """A ticket absent from the baseline enters scope."""
    write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress"})
    fake_jira.fixtures.issues.append(issue("ABC-5", "To Do", updated="2026-11-01T09:00:00Z"))

    item = items_by_key(await collector.run())["ABC-5"]

    assert (item["scope_change"], item["bucket"], item["summary"]) == (
        "entered",
        "in_progress",
        "Newly assigned · To Do",
    )


def _item(key: str, bucket: str, priority: str | None) -> Item:
    status = Status(name="In Progress", category="indeterminate")
    return Item.model_validate(
        {
            "key": key,
            "url": f"https://jira.example/browse/{key}",
            "title": key,
            "status": status,
            "previous_status": None,
            "scope_change": "none",
            "bucket": bucket,
            "jira_priority": priority,
            "summary": "x",
            "events": [],
        },
    )


def test_ac16_items_sort_by_bucket_priority_then_key() -> None:
    """Items sort by bucket order, then Jira priority (none last), then key."""
    expected = [
        _item("ABC-9", "blocked", "Highest"),
        _item("ABC-2", "blocked", "Low"),
        _item("ABC-3", "at_risk", None),
        _item("ABC-4", "done", "High"),
        _item("ABC-5", "in_progress", "High"),
        _item("ABC-6", "in_progress", "Medium"),
        _item("ABC-7", "in_progress", "Lowest"),
        _item("ABC-2", "in_progress", None),
        _item("ABC-10", "in_progress", None),
        _item("ABC-1", "comment", "Highest"),
        _item("ABC-8", "left_scope", None),
    ]

    ordered = sorted(expected[1::2] + expected[::2], key=lambda item: item.sort_key)

    assert [(i.key, i.bucket) for i in ordered] == [(i.key, i.bucket) for i in expected]


@pytest.mark.asyncio
async def test_ac18_rate_limit_honours_retry_after(
    jira_url: str,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """Two 429s with Retry-After 1 are retried after sleeping 1 s each."""
    write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress"})
    comments_path = "/rest/api/3/issue/ABC-1/comment"
    fake_jira.script(comments_path, 429, times=2, retry_after=1)
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    report = await Collector(jira_url, state_dir).run(retry=RetryPolicy(sleep=sleep))

    assert report.warnings == []
    assert sum(r.path == comments_path for r in fake_jira.requests) == 3
    assert slept == [1, 1]


@pytest.mark.asyncio
async def test_ac21_follows_search_and_bulkfetch_pages(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """60 issues over two search pages and two bulkfetch pages all reach the snapshot."""
    keys = [f"ABC-{n}" for n in range(1, 61)]
    fake_jira.fixtures.issues = [issue(key) for key in keys]
    fake_jira.fixtures.changelogs = [
        changelog(key, status_change("2026-11-02T15:00:00Z", "To Do", "In Progress"))
        for key in keys
    ]

    report = await collector.run()

    assert sorted(snapshot_keys(state_dir, "2026-11-03")) == sorted(keys)
    assert len(report.items) == 60
    assert sum(r.path.endswith("/search/jql") for r in fake_jira.requests) == 2
    assert sum(r.path.endswith("/bulkfetch") for r in fake_jira.requests) == 2


@pytest.mark.asyncio
async def test_ac22_report_validates_and_clips_summaries(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """The serialized report validates as schema 1 with every summary at most 120 chars."""
    long_status = "Waiting for the vendor to answer " * 4
    write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress"})
    fake_jira.fixtures.issues = [issue("ABC-1", long_status)]
    fake_jira.fixtures.changelogs = [
        changelog("ABC-1", status_change("2026-11-02T15:00:00Z", "In Progress", long_status)),
    ]

    report = Report.model_validate_json((await collector.run()).model_dump_json())

    assert report.schema_version == 1
    assert report.items
    assert all(len(item.summary) <= 120 for item in report.items)


@pytest.mark.asyncio
async def test_ac30_service_writes_only_through_effects(
    collector: Collector,
    state_dir: Path,
) -> None:
    """The service's only side effect is the snapshot write, requested through the gate."""
    write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress"})

    class RecordingEffects:
        def __init__(self) -> None:
            self.intents: list[Intent] = []

        async def perform[T](
            self,
            intent: Intent,
            action: Callable[[], Awaitable[T]],
        ) -> T | None:
            del action
            self.intents.append(intent)
            return None

    effects = RecordingEffects()
    await collector.run(effects=effects)

    assert effects.intents == [Intent("write", "snapshots/2026-11-03.json")]
    assert not (state_dir / "snapshots" / "2026-11-03.json").exists()


@pytest.mark.asyncio
async def test_failed_changelog_fetch_warns_and_carries_baseline(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """A failed bulkfetch warns per issue; known issues keep their baseline entry."""
    baseline = write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress"})
    fake_jira.fixtures.issues.append(issue("ABC-5", "To Do"))
    fake_jira.script("/rest/api/3/changelog/bulkfetch", 500, times=4)

    report = await collector.run()

    assert [(w.key, w.stage, w.reason) for w in report.warnings] == [
        ("ABC-1", "changelog", "HTTP 500 after 4 attempts"),
        ("ABC-5", "changelog", "HTTP 500 after 4 attempts"),
    ]
    assert report.items == []
    today = json.loads((state_dir / "snapshots" / "2026-11-03.json").read_text(encoding="utf-8"))
    assert today["issues"] == json.loads(baseline.read_text(encoding="utf-8"))["issues"]


@pytest.mark.asyncio
async def test_rejected_comment_request_warns_without_retry(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """A 404 on comments is not retried and becomes a warning."""
    write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress"})
    fake_jira.script("/rest/api/3/issue/ABC-1/comment", 404)

    report = await collector.run()

    assert [(w.key, w.stage, w.reason) for w in report.warnings] == [
        ("ABC-1", "comments", "HTTP 404"),
    ]


@pytest.mark.asyncio
async def test_status_change_without_author_is_credited_to_jira(
    collector: Collector,
    fake_jira: FakeJira,
    state_dir: Path,
) -> None:
    """A changelog entry with no author (automation) is attributed to Jira."""
    write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress"})
    history: dict[str, Any] = status_change("2026-11-02T15:00:00Z", "In Progress", "In Review")
    del history["author"]
    fake_jira.fixtures.changelogs = [changelog("ABC-1", history)]

    events = items_by_key(await collector.run())["ABC-1"]["events"]

    assert events[0]["author"] == {"account_id": "", "display_name": "Jira", "is_me": False}
