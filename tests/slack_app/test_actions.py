import json

import pytest

from daily_report.slack.fake import FakeSlack
from tests.service.conftest import Clock, ServiceEnv
from tests.slack_app.conftest import OTHER, Listener, build_listener
from tests.slack_app.helpers import RUN_ID, as_text, replies, standup_posts

CHANNEL = {"standup_target": "channel"}


def status(env: ServiceEnv) -> str:
    """Return the run's status."""
    return str(json.loads(env.record())["status"])


async def test_ac4_two_post_clicks_post_once(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """One standup message, one DM update to Posted, then `already posted`."""
    chosen = {"standup_style": "ytb", **CHANNEL}
    first = await listener.action("standup_post", RUN_ID, chosen=chosen)
    second = await listener.action("standup_post", RUN_ID, chosen=chosen)

    assert first.status == second.status == 200
    (post,) = standup_posts(fake_slack)
    assert "thread_ts" not in post.body
    (update,) = fake_slack.called("chat.update")
    record = json.loads(pending.record())
    assert update.body["ts"] == record["dm"]["ts"]
    assert update.body["channel"] == record["dm"]["channel"]
    assert update.body["text"] == f"✅ Posted {record['posted']['permalink']}"
    assert record["status"] == "posted"
    assert replies(fake_slack) == ["already posted"]


async def test_ac4_post_uses_the_selected_style_and_defaults_the_rest(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """With no selection the style is bluf and the target is post.target; junk is ignored."""
    del pending
    await listener.action("standup_style", RUN_ID, chosen={"standup_style": "ytb"})
    await listener.action("standup_post", RUN_ID, chosen={"standup_style": "nope", **CHANNEL})

    (post,) = standup_posts(fake_slack)
    assert "Standup" in as_text(post.body["blocks"])


async def test_ac4_a_refused_post_replies_with_the_reason(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """No standup thread today and no target chosen: the owner is told, the run stays pending."""
    await listener.action("standup_post", RUN_ID)

    assert replies(fake_slack) == [
        "no standup thread found today in #C0STANDUP; rerun with --target channel",
    ]
    assert status(pending) == "pending"
    assert fake_slack.called("chat.update") == []


async def test_ac4_an_unknown_run_replies_not_pending(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """A stale button names a run that does not exist."""
    del pending
    await listener.action("standup_post", "20200101-000000")

    assert replies(fake_slack) == ["run 20200101-000000 is unknown, not pending"]


async def test_ac4_a_draft_already_posted_from_the_cli_is_reported(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """The draft file says posted while the run is still pending: `already posted`."""
    draft = pending.state / "drafts" / f"{RUN_ID}.json"
    data = json.loads(draft.read_text(encoding="utf-8"))
    data["posted"] = {"channel": "C0STANDUP", "ts": "1.1", "permalink": "https://x"}
    draft.write_text(json.dumps(data), encoding="utf-8")

    await listener.action("standup_post", RUN_ID, chosen=CHANNEL)

    assert replies(fake_slack) == ["already posted"]


async def test_ac5_skip_marks_the_run_skipped(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """Skipped: the DM says so and nothing is posted."""
    await listener.action("standup_skip", RUN_ID)

    assert status(pending) == "skipped"
    (update,) = fake_slack.called("chat.update")
    assert "⏭ Skipped" in as_text(update.body)
    assert standup_posts(fake_slack) == []
    await listener.action("standup_skip", RUN_ID)
    assert replies(fake_slack) == [f"run {RUN_ID} is skipped, not pending"]


async def test_ac6_edit_notes_opens_the_modal(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """views.open carries standup_notes_modal and the run id."""
    del pending
    await listener.action("standup_edit_notes", RUN_ID)

    (opened,) = fake_slack.called("views.open")
    view = opened.body["view"]
    assert opened.body["trigger_id"] == "t1"
    assert view["callback_id"] == "standup_notes_modal"
    assert view["private_metadata"] == RUN_ID


async def test_ac6_edit_notes_of_a_finished_run_is_refused(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """No modal for a skipped run."""
    await listener.action("standup_skip", RUN_ID)
    del pending

    await listener.action("standup_edit_notes", RUN_ID)

    assert fake_slack.called("views.open") == []
    assert replies(fake_slack) == [f"run {RUN_ID} is skipped, not pending"]


async def test_ac6_submitting_a_note_updates_the_dm_and_the_draft(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """The note reaches the inbox and the draft; the same DM is redrawn; still pending."""
    response = await listener.submit(RUN_ID, "Pairing with Ana on PAY-12")

    assert response.status == 200
    assert json.loads(response.body or "{}") == {}
    inbox = (pending.state / "notes" / "pending.jsonl").read_text(encoding="utf-8")
    assert "Pairing with Ana on PAY-12" in inbox
    draft = json.loads((pending.state / "drafts" / f"{RUN_ID}.json").read_text(encoding="utf-8"))
    assert [note["text"] for note in draft["notes"]] == ["Pairing with Ana on PAY-12"]
    (update,) = fake_slack.called("chat.update")
    assert update.body["ts"] == json.loads(pending.record())["dm"]["ts"]
    assert "Pairing with Ana on PAY-12" in as_text(update.body)
    assert status(pending) == "pending"


async def test_ac6_a_bad_note_keeps_the_modal_open_with_the_reason(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """Too long, empty or for a finished run: an inline error and no change."""
    long = await listener.submit(RUN_ID, "x" * 121)
    empty = await listener.submit(RUN_ID, "  ")
    await listener.action("standup_skip", RUN_ID)
    late = await listener.submit(RUN_ID, "hi")

    errors = [json.loads(r.body or "{}")["errors"]["note"] for r in (long, empty, late)]
    assert errors == [
        "note is 121 characters; the limit is 120",
        "usage: /standup note <text>",
        f"run {RUN_ID} is skipped, not pending",
    ]
    assert not (pending.state / "notes" / "pending.jsonl").exists()
    assert len(fake_slack.called("chat.update")) == 1  # the skip


async def test_ac7_a_stranger_is_refused_everywhere(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """Commands, clicks and the modal: `not authorized` and no change."""
    before = pending.record()
    posts = len(fake_slack.called("chat.postMessage"))

    run = await listener.command("run", user=OTHER)
    note = await listener.command("note hi", user=OTHER)
    click = await listener.action("standup_post", RUN_ID, user=OTHER, chosen=CHANNEL)
    modal = await listener.submit(RUN_ID, "hi", user=OTHER)

    assert {run.status, note.status, click.status, modal.status} == {200}
    assert replies(fake_slack) == ["not authorized"] * 3
    assert pending.record() == before
    assert not (pending.state / "notes" / "pending.jsonl").exists()
    assert len(fake_slack.called("chat.postMessage")) == posts


async def test_ac8_a_post_after_the_cutoff_expires_the_draft(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
    clock: Clock,
) -> None:
    """12:05: ephemeral `draft <id> expired at 12:00` and the DM shows Expired."""
    clock.now = clock.now.replace(hour=12, minute=5)

    await listener.action("standup_post", RUN_ID, chosen=CHANNEL)

    assert replies(fake_slack) == [f"draft {RUN_ID} expired at 12:00"]
    (update,) = fake_slack.called("chat.update")
    assert "⌛ Expired" in as_text(update.body)
    assert status(pending) == "expired"
    assert standup_posts(fake_slack) == []


async def test_ac8_a_click_on_a_superseded_draft_shows_expired(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
    clock: Clock,
) -> None:
    """A newer run expired this one earlier: the click closes its DM too."""
    clock.now = clock.now.replace(minute=10)
    await listener.command("run")
    del pending

    await listener.action("standup_post", RUN_ID, chosen=CHANNEL)

    assert replies(fake_slack)[-1] == f"run {RUN_ID} is expired, not pending"
    assert "⌛ Expired" in as_text(fake_slack.called("chat.update")[0].body)


@pytest.fixture
def manual_listener(env: ServiceEnv, slack_site: str) -> Listener:
    """Return the Bolt app over a terminal run, which has no DM."""
    assert env.cli("run").exit_code == 0
    return build_listener(env, slack_site)


async def test_ac6_a_note_for_a_run_without_a_dm_is_saved_without_a_redraw(
    env: ServiceEnv,
    manual_listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """A terminal run has no DM: the note lands in the draft and nothing is updated."""
    await manual_listener.submit(RUN_ID, "Pairing with Ana on PAY-12")

    assert fake_slack.called("chat.update") == []
    assert "Pairing" in (env.state / "drafts" / f"{RUN_ID}.json").read_text(encoding="utf-8")
