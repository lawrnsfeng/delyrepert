"""Fixture R1 (as Jira data and as a report) and builders for compose tests."""

import datetime as dt
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from typer.testing import CliRunner, Result

from daily_report.cli import app
from daily_report.collect.models import Baseline, Bucket, Item, Report, Status, Window
from daily_report.compose.models import Draft, SlackPayload, StyleName
from daily_report.compose.render import Renderer
from daily_report.context.models import ContextResult
from daily_report.jira.fake import JiraFixtures, Json
from daily_report.notes.models import Note
from tests.builders import ALICE, OWNER, changelog, comment, issue, status_change, write_snapshot

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"
R1_AS_OF = "2026-11-02T09:00:00+00:00"
R1_NOTE_TEXT = "Out 15:00\N{EN DASH}17:00 (dentist)"
CREATED_AT = dt.datetime(2026, 11, 2, 9, 5, tzinfo=dt.UTC)
DANA: Json = {"accountId": "dana-1", "displayName": "Dana", "accountType": "atlassian"}
UPDATED = "2026-11-01T12:00:00Z"


def titled(source: Json, title: str, priority: str = "Medium") -> Json:
    """Return the `search/jql` issue `source` with its summary and priority replaced."""
    fields = {**source["fields"], "summary": title, "priority": {"name": priority}}
    return {**source, "fields": fields}


def r1_jira() -> JiraFixtures:
    """Return the fake Jira data whose report, diffed against `r1_baseline`, is R1."""
    issues = [
        titled(issue("PAY-412", "Blocked", updated=UPDATED), "Ledger export to the bank"),
        titled(issue("PAY-398", due="2026-11-03", updated=UPDATED), "Refund webhook retries"),
        titled(issue("PAY-405", "Done", updated=UPDATED), "Settlement report totals", "High"),
        titled(issue("PAY-377", "Done", updated=UPDATED), "Card BIN lookup cache"),
        titled(issue("PAY-420", updated=UPDATED), "Payout scheduler"),
        titled(issue("PAY-430", updated=UPDATED), "Currency rounding rules"),
        titled(issue("PAY-431", updated=UPDATED), "Chargeback evidence upload"),
    ]
    changelogs = [
        changelog("PAY-412", status_change("2026-10-31T10:00:00Z", "In Progress", "Blocked")),
        changelog("PAY-405", status_change("2026-10-30T15:00:00Z", "In Progress", "Done")),
        changelog("PAY-377", status_change("2026-10-30T16:00:00Z", "In Progress", "Done")),
        changelog("PAY-420", status_change("2026-11-01T09:00:00Z", "To Do", "In Progress")),
    ]
    comments = {
        "PAY-430": [
            comment("c-1", "2026-10-31T11:00:00Z", "Should we round half-even?", DANA),
            comment("c-2", "2026-11-01T11:00:00Z", "Agree with Dana", ALICE),
        ],
        "PAY-431": [comment("c-3", "2026-10-31T12:00:00Z", "Evidence uploaded", OWNER)],
    }
    return JiraFixtures(
        myself=OWNER,
        fields=[],
        issues=issues,
        changelogs=changelogs,
        comments=comments,
    )


def r1_baseline(state_dir: Path) -> None:
    """Write R1's Friday snapshot (PAY-440 is in it, so it leaves scope on Monday)."""
    statuses = dict.fromkeys(("PAY-412", "PAY-398", "PAY-405", "PAY-377"), "In Progress")
    statuses |= {"PAY-420": "To Do", "PAY-430": "In Progress", "PAY-431": "In Progress"}
    statuses |= {"PAY-440": "In Progress"}
    write_snapshot(state_dir, "2026-10-30", statuses, taken_at="2026-10-30T09:00:00Z")


def r1_report() -> Report:
    """Return fixture R1's report (`tests/compose/fixtures/r1.json`)."""
    return Report.model_validate_json((FIXTURES / "r1.json").read_text(encoding="utf-8"))


def note(text: str = R1_NOTE_TEXT, group: str = "today") -> Note:
    """Return a pending note."""
    return Note.model_validate({"at": "2026-11-01T18:00:00Z", "text": text, "group": group})


def draft_of(source: Report, notes: list[Note] | None = None) -> Draft:
    """Return the draft of `source` with `notes` and no context."""
    return Draft.build(source, notes or [], ContextResult(), created_at=CREATED_AT, zone=dt.UTC)


def r1_draft(*, notes: list[Note] | None = None, context: ContextResult | None = None) -> Draft:
    """Return R1's draft: the report plus its one pending note."""
    return Draft.build(
        r1_report(),
        [note()] if notes is None else notes,
        context or ContextResult(),
        created_at=CREATED_AT,
        zone=dt.UTC,
    )


def render(
    draft: Draft,
    style: StyleName,
    *,
    animated: str | None = None,
    available: bool = False,
    display_name: str | None = None,
) -> SlackPayload:
    """Render `draft` in `style`."""
    renderer = Renderer(display_name=display_name, blocked_animated=animated)
    return renderer.render(draft, style, animated_available=available)


def report_item(key: str, bucket: Bucket, title: str | None = None) -> Item:
    """Return a report item with no events."""
    return Item(
        key=key,
        url=f"https://jira.example/browse/{key}",
        title=title or f"{key} title",
        status=Status(name="In Progress", category="indeterminate"),
        previous_status=None,
        scope_change="none",
        bucket=bucket,
        jira_priority=None,
        summary="To Do → In Progress",
        events=[],
    )


def report(items: list[Item], *, baseline: bool = True) -> Report:
    """Return a report as of R1's instant, diffed against Friday 30 Oct when `baseline`."""
    as_of = dt.datetime.fromisoformat(R1_AS_OF)
    friday = dt.datetime(2026, 10, 30, 9, tzinfo=dt.UTC)
    return Report(
        as_of=as_of,
        baseline=Baseline(date=friday.date(), taken_at=friday) if baseline else None,
        window=Window(start=friday, end=as_of),
        items=items,
        warnings=[],
    )


def plain(payload: SlackPayload) -> str:
    """Return the payload's visible text: mrkdwn links as labels, rich text joined per line."""
    return "\n".join(_texts(payload.blocks))


def _texts(node: object) -> list[str]:
    if isinstance(node, list):
        return [text for child in node for text in _texts(child)]
    if not isinstance(node, dict):
        return []
    if node.get("type") == "rich_text_section":
        return ["".join(_inline(element) for element in node["elements"])]
    if node.get("type") in {"mrkdwn", "plain_text", "raw_text", "markdown"}:
        return [_unlink(node["text"])]
    return [text for value in node.values() for text in _texts(value)]


def _inline(element: dict[str, str]) -> str:
    return f":{element['name']}:" if element["type"] == "emoji" else element["text"]


def _unlink(text: str) -> str:
    """Replace `<url|label>` and `[label](url)` with `label`."""
    while "<http" in text:
        start = text.index("<http")
        end = text.index(">", start)
        text = text[:start] + text[start:end].partition("|")[2] + text[end + 1 :]
    while "](http" in text:
        end = text.index("](http")
        start = text.rindex("[", 0, end)
        close = text.index(")", end)
        text = text[:start] + text[start + 1 : end] + text[close + 1 :]
    return text


def run_cli(
    *args: str,
    env: Mapping[str, str | None] | None = None,
    stdin: str | None = None,
) -> Result:
    """Invoke `daily-report <args>` in-process with a Jira token set."""
    return CliRunner().invoke(
        app,
        list(args),
        input=stdin,
        env={"JIRA_API_TOKEN": "fake-api-token", **(env or {})},
    )


def draft_files(state_dir: Path) -> list[Path]:
    """Return the saved drafts."""
    return sorted((state_dir / "drafts").glob("*.json"))


def links(stdout: str) -> list[str]:
    """Return the Block Kit Builder links printed on stdout."""
    return [
        line
        for line in stdout.splitlines()
        if line.startswith("https://app.slack.com/block-kit-builder/#")
    ]


def payload_json(path: Path) -> dict[str, Any]:
    """Read a payload file."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
