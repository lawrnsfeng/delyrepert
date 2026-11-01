import asyncio
import fcntl
import json

from daily_report.jira.fake import FakeJira
from daily_report.slack.fake import FakeSlack
from tests.service.conftest import Clock, ServiceEnv
from tests.slack_app.conftest import Listener, build_listener
from tests.slack_app.helpers import RUN_ID, dm_requests, replies


async def test_ac9_run_while_another_holds_the_lock(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """`run <A> already in progress`, and no new record."""
    with (pending.state / "run.lock").open("w", encoding="utf-8") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        held.write("20261101-090000")
        held.flush()

        await listener.command("run")

    assert replies(fake_slack) == ["run 20261101-090000 already in progress"]
    assert len(list((pending.state / "runs").glob("*.json"))) == 1


async def test_ac9_run_from_slack_leaves_a_pending_draft_and_a_dm(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
    clock: Clock,
) -> None:
    """The reply names the run; its record says trigger slack; the DM goes out."""
    clock.now = clock.now.replace(minute=10)
    before = len(dm_requests(fake_slack))

    await listener.command("run")

    assert replies(fake_slack) == ["run 20261102-091000 pending — preview sent in DM"]
    record = json.loads(pending.record("20261102-091000"))
    assert record["trigger"] == "slack"
    assert record["dm"]
    assert len(dm_requests(fake_slack)) == before + 1


async def test_ac9_a_failed_run_from_slack_replies_the_cause(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
    fake_jira: FakeJira,
    clock: Clock,
) -> None:
    """The reply carries the error; no failure DM for a run the owner started."""
    clock.now = clock.now.replace(minute=10)
    fake_jira.script("/rest/api/3/search/jql", 401)
    before = len(dm_requests(fake_slack))

    await listener.command("run")

    assert "Jira rejected credentials" in replies(fake_slack)[0]
    assert len(dm_requests(fake_slack)) == before
    del pending


async def test_ac9_a_paused_run_from_slack_replies_its_status(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """A slack run ignores the pause (only scheduled runs skip) and is pending."""
    await listener.command("pause")

    await listener.command("run")

    assert replies(fake_slack)[1].endswith("pending — preview sent in DM")
    del pending


async def test_ac10_status_reports_timer_serve_pause_and_last_run(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """Four lines, answered after an immediate ack."""
    response = await listener.command("status")

    assert response.status == 200
    assert replies(fake_slack) == [
        (
            "next run: Tue 2026-11-03 09:00:00 UTC\nserve: running\nnot paused\n"
            f"last run: {RUN_ID} pending"
        ),
    ]
    del pending


async def test_ac10_status_with_nothing_known(
    env: ServiceEnv,
    slack_site: str,
    fake_slack: FakeSlack,
) -> None:
    """No runs and a paused-until flag."""
    listener = build_listener(env, slack_site)
    await listener.command("pause 2026-11-10")

    await listener.command("status")

    assert replies(fake_slack)[1].splitlines()[2:] == ["paused until 2026-11-10", "last run: none"]


async def test_ac10_help_unknown_and_empty_verbs(
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """Help and an empty text list the verbs; a stranger verb is named first."""
    await listener.command("help")
    await listener.command("")
    await listener.command("bogus")

    usage = "usage: /standup run|preview|status|pause|resume|note|help"
    assert replies(fake_slack) == [usage, usage, f"unknown verb bogus\n{usage}"]


async def test_ac10_preview_resends_the_pending_draft(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """A fresh DM goes out and the record points at it."""
    before = len(dm_requests(fake_slack))

    await listener.command("preview")

    assert replies(fake_slack) == [f"preview for run {RUN_ID} sent in DM"]
    assert len(dm_requests(fake_slack)) == before + 1
    assert json.loads(pending.record())["dm"]["ts"] != "1"


async def test_ac10_preview_with_nothing_pending(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """After a skip there is nothing to preview."""
    await listener.action("standup_skip", RUN_ID)
    del pending

    await listener.command("preview")

    assert replies(fake_slack) == ["no pending draft — /standup run"]


async def test_ac11_pause_and_resume_share_the_cli_file(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """paused.json says by slack with the date; resume deletes it; the CLI's status agrees."""
    await listener.command("pause 2026-11-10")

    flag = json.loads((pending.state / "paused.json").read_text(encoding="utf-8"))
    assert flag["by"] == "slack"
    assert flag["until"] == "2026-11-10"
    # `status` runs its own event loop, so it cannot share this test's.
    status = await asyncio.to_thread(pending.cli, "status", "--json", secrets=False)
    assert json.loads(status.stdout)["paused"] == {"until": "2026-11-10"}

    await listener.command("pause")
    await listener.command("resume")

    assert not (pending.state / "paused.json").exists()
    assert replies(fake_slack) == ["paused until 2026-11-10", "paused", "resumed"]


async def test_ac11_a_bad_or_past_date_is_refused(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """Not a date, or before today: usage or the reason, and no flag."""
    await listener.command("pause soon")
    await listener.command("pause 2026-11-01")

    assert replies(fake_slack) == [
        "usage: /standup pause [YYYY-MM-DD]",
        "--until 2026-11-01 is before today (2026-11-02)",
    ]
    assert not (pending.state / "paused.json").exists()


async def test_ac12_note_appends_the_cli_shape(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """One line {at, text, group: today} and the pending count."""
    await listener.command("note Pairing with Ana on PAY-12")

    lines = (pending.state / "notes" / "pending.jsonl").read_text(encoding="utf-8").splitlines()
    note = json.loads(lines[0])
    assert (len(lines), note["text"], note["group"]) == (1, "Pairing with Ana on PAY-12", "today")
    assert note["at"]
    assert replies(fake_slack) == ["📝 Noted for next standup (1 pending)"]


async def test_ac12_a_long_or_missing_note_is_refused(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """Over 120 characters, or no text: the limit or the usage, and no line."""
    await listener.command("note " + "x" * 121)
    await listener.command("note")

    assert replies(fake_slack) == [
        "note is 121 characters; the limit is 120",
        "usage: /standup note <text>",
    ]
    assert not (pending.state / "notes" / "pending.jsonl").exists()
