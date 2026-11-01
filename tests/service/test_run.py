import datetime as dt
import fcntl
import json

import pytest

from daily_report.jira.fake import FakeJira
from daily_report.service import services
from daily_report.service.services import RunLock
from daily_report.slack.fake import FakeSlack
from tests.service.conftest import RUN_ID, Clock, ServiceEnv

SEARCH = "/rest/api/3/search/jql"


def test_ac4_scheduled_run_leaves_a_pending_record(env: ServiceEnv, fake_slack: FakeSlack) -> None:
    """The record is pending with its draft path, and nothing is posted."""
    result = env.cli("run", "--scheduled")

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == f"{RUN_ID} pending"
    record = json.loads(env.record())
    assert record["status"] == "pending"
    assert record["trigger"] == "scheduled"
    assert record["draft_path"] == f"drafts/{RUN_ID}.json"
    assert (env.state / record["draft_path"]).is_file()
    assert fake_slack.requests == []


def test_ac6_next_run_expires_older_pending_drafts(env: ServiceEnv, clock: Clock) -> None:
    """A second run turns the first pending run into expired."""
    assert env.cli("run").exit_code == 0
    clock.now = clock.now.replace(minute=10)

    assert env.cli("run").exit_code == 0

    assert json.loads(env.record())["status"] == "expired"
    assert json.loads(env.record("20261102-091000"))["status"] == "pending"


def test_ac7_run_refuses_while_another_run_holds_the_lock(env: ServiceEnv) -> None:
    """A held run.lock exits 7 naming the holder and writes no record."""
    env.state.mkdir(exist_ok=True)
    with (env.state / "run.lock").open("w", encoding="utf-8") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        held.write("20261101-090000")
        held.flush()

        result = env.cli("run")

    assert result.exit_code == 7
    assert "run 20261101-090000 already in progress" in result.output
    assert not (env.state / "runs").exists()


def test_ac7_run_that_loses_the_lock_race_exits_7(
    env: ServiceEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock taken between the check and the acquire still exits 7."""
    env.state.mkdir(exist_ok=True)
    with (env.state / "run.lock").open("w", encoding="utf-8") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        monkeypatch.setattr(RunLock, "holder", lambda _: None)

        result = env.cli("run")

    assert result.exit_code == 7
    assert "already in progress" in result.output


def test_ac7_the_lock_is_free_after_a_run(env: ServiceEnv, clock: Clock) -> None:
    """A finished run releases run.lock."""
    assert env.cli("run").exit_code == 0
    clock.now = clock.now.replace(minute=10)

    assert env.cli("run").exit_code == 0


def test_ac8_scheduled_run_is_skipped_while_paused(
    env: ServiceEnv,
    clock: Clock,
    fake_jira: FakeJira,
) -> None:
    """Paused: a scheduled run records paused_skip and calls nothing; a manual run still works."""
    clock.now = clock.now.replace(day=9)
    assert env.cli("pause", "--until", "2026-11-10", secrets=False).exit_code == 0

    skipped = env.cli("run", "--scheduled")

    assert skipped.exit_code == 0
    assert "skipped: paused" in skipped.stderr
    assert json.loads(env.record("20261109-090500"))["status"] == "paused_skip"
    assert fake_jira.requests == []
    assert env.cli("run").exit_code == 0
    assert json.loads(env.record("20261109-090500"))["status"] == "pending"


def test_ac8_pause_lifts_on_the_until_day(env: ServiceEnv, clock: Clock) -> None:
    """On the --until day the scheduled run proceeds and deletes paused.json."""
    clock.now = clock.now.replace(day=9)
    assert env.cli("pause", "--until", "2026-11-10", secrets=False).exit_code == 0
    clock.now = clock.now.replace(day=10)

    result = env.cli("run", "--scheduled")

    assert result.exit_code == 0
    assert json.loads(env.record("20261110-090500"))["status"] == "pending"
    assert not (env.state / "paused.json").exists()


def test_ac11_jira_rejecting_credentials_records_failed(
    env: ServiceEnv,
    fake_jira: FakeJira,
) -> None:
    """The collector's exit 3 is recorded and returned."""
    fake_jira.script(SEARCH, 401)

    result = env.cli("run", "--scheduled")

    assert result.exit_code == 3
    record = json.loads(env.record())
    assert record["status"] == "failed"
    assert "rejected credentials" in record["error"]


def test_ac4_the_service_clock_is_utc() -> None:
    """Run ids and records are stamped from a UTC instant."""
    assert services.utc_now().utcoffset() == dt.timedelta(0)
