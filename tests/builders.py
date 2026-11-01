"""Builders for Jira REST v3 fixture JSON and stored snapshots."""

import datetime as dt
import json
from pathlib import Path

from daily_report.jira.fake import Json

OWNER: Json = {"accountId": "owner-1", "displayName": "Owner", "accountType": "atlassian"}
ALICE: Json = {"accountId": "alice-1", "displayName": "Alice", "accountType": "atlassian"}
BOT: Json = {"accountId": "bot-1", "displayName": "Automation", "accountType": "app"}
CATEGORIES = {"To Do": "new", "Done": "done"}


def issue_id(key: str) -> str:
    """Return the numeric Jira id the builders give `key`."""
    return str(10000 + int(key.rpartition("-")[2]))


def issue(
    key: str,
    status: str = "In Progress",
    *,
    updated: str = "2026-11-02T15:00:00Z",
    due: str | None = None,
    flagged: object = None,
) -> Json:
    """Return a `search/jql` issue."""
    return {
        "id": issue_id(key),
        "key": key,
        "fields": {
            "summary": f"{key} title",
            "status": {
                "name": status,
                "statusCategory": {"key": CATEGORIES.get(status, "indeterminate")},
            },
            "priority": {"name": "Medium"},
            "duedate": due,
            "updated": updated,
            "customfield_10021": flagged,
        },
    }


def changelog(key: str, *histories: Json) -> Json:
    """Return the bulkfetch changelog of issue `key`."""
    return {"issueId": issue_id(key), "changeHistories": list(histories)}


def status_change(at: str, before: str, after: str, author: Json = OWNER) -> Json:
    """Return a changelog history entry moving the status from `before` to `after`."""
    return {
        "id": f"h-{at}",
        "author": author,
        "created": at,
        "items": [{"field": "status", "fromString": before, "toString": after}],
    }


def comment(comment_id: str, at: str, text: str, author: Json = ALICE) -> Json:
    """Return a comment whose body is one ADF paragraph holding `text`."""
    body = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }
    return {"id": comment_id, "author": author, "body": body, "created": at}


def write_snapshot(
    state_dir: Path,
    day: str,
    statuses: dict[str, str],
    *,
    taken_at: str | None = None,
) -> Path:
    """Write `snapshots/<day>.json` with one entry per key, checked at `taken_at`."""
    checked = taken_at or f"{day}T09:00:00Z"
    issues = {
        key: {
            "status": {"name": status, "category": CATEGORIES.get(status, "indeterminate")},
            "checked_at": checked,
            "duedate": None,
        }
        for key, status in statuses.items()
    }
    path = state_dir / "snapshots" / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"taken_at": checked, "issues": issues}), encoding="utf-8")
    return path


def as_of(text: str) -> dt.datetime:
    """Parse an ISO-8601 instant."""
    return dt.datetime.fromisoformat(text)
