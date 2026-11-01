import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from daily_report.collect.models import Snapshot
from daily_report.context.models import ContextEnvelope
from daily_report.jira.models import (
    ChangeLogPage,
    CommentPage,
    JiraFieldList,
    JiraUser,
    SearchPage,
)
from daily_report.notes.models import Note
from tests.collect.helpers import AS_OF, Collector
from tests.demo.helpers import FIXTURES


def load(name: str) -> Any:  # noqa: ANN401 - raw JSON
    """Read one fixture file."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_ac1_jira_fixtures_validate_against_the_v3_models() -> None:
    """Every `jira/*.json` parses with the collector's REST v3 models."""
    JiraUser.model_validate(load("jira/myself.json"))
    JiraFieldList.model_validate(load("jira/field.json"))
    issues = SearchPage.model_validate({"issues": load("jira/issues.json")}).issues
    logs = ChangeLogPage.model_validate({"issueChangeLogs": load("jira/changelogs.json")})
    comments = load("jira/comments.json")
    for key in comments:
        assert CommentPage.model_validate({"comments": comments[key]}).comments
    assert {issue.id for issue in issues} >= {log.issue_id for log in logs.issue_change_logs}
    assert sorted(path.name for path in (FIXTURES / "jira").glob("*.json")) == [
        "changelogs.json",
        "comments.json",
        "field.json",
        "issues.json",
        "myself.json",
    ]
    Snapshot.model_validate_json((FIXTURES / "state/snapshots/2026-11-02.json").read_text("utf-8"))
    Note.model_validate_json((FIXTURES / "notes/pending.jsonl").read_text("utf-8"))
    ContextEnvelope.model_validate(load("context/demo-skill.json"))


@pytest.mark.asyncio
async def test_ac2_fixtures_produce_every_collector_bucket(
    demo_jira_url: str,
    tmp_path: Path,
) -> None:
    """Fake Jira on the demo fixtures, diffed against the seeded baseline."""
    state = tmp_path / "state"
    (state / "snapshots").mkdir(parents=True)
    source = FIXTURES / "state/snapshots/2026-11-02.json"
    (state / "snapshots/2026-11-02.json").write_text(source.read_text("utf-8"), encoding="utf-8")

    report = await Collector(demo_jira_url, state).run(AS_OF)

    items = {item.key: item for item in report.items}
    assert report.baseline is not None
    assert report.baseline.date == dt.date(2026, 11, 2)
    assert {key: item.bucket for key, item in items.items()} == {
        "DEMO-101": "done",
        "DEMO-102": "in_progress",
        "DEMO-103": "blocked",
        "DEMO-104": "comment",
        "DEMO-105": "in_progress",
        "DEMO-107": "left_scope",
        "DEMO-108": "at_risk",
    }
    assert items["DEMO-102"].status.name == "In Review"
    assert [e.author.is_me for e in items["DEMO-102"].events if e.type == "comment"] == [True]
    assert items["DEMO-105"].scope_change == "entered"
    assert items["DEMO-108"].events == []
    assert items["DEMO-108"].summary == "Due 2026-11-04"
    assert all(e.author.account_id != "demo-bot" for i in items.values() for e in i.events)
