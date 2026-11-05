import json

import pytest

from daily_report.slack import services as slack_services
from daily_report.slack.fake import FakeSlack
from tests.service.conftest import Clock, ServiceEnv
from tests.slack_app.conftest import Listener, build_listener
from tests.slack_app.helpers import replies

PARENT_TS = "1793610000.000100"  # 2026-11-02 09:00 UTC
WEEKLY_TS = "1793606400.000200"  # 2026-11-02 08:00 UTC
PARENT = {"ts": PARENT_TS, "user": "U0BOT", "text": "Daily standup\nsecond line"}
WEEKLY_PARENT = {"ts": WEEKLY_TS, "user": "U0LEAD", "text": "Weekly sync"}
PIN_LINK = "https://acme.slack.com/archives/C0OTHER/p1793600000000300"
FAKE_LINK = "https://fake.slack.com/archives"
NO_PARENT = (
    "no thread matches default in #C0STANDUP since 2026-11-02; "
    "pin one with a thread permalink or post with --target channel"
)


@pytest.fixture(autouse=True)
def slack_today(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    """Let the thread lookup see the service clock's day too."""
    monkeypatch.setattr(slack_services, "utc_now", lambda: clock.now)


def with_weekly(env: ServiceEnv, slack_site: str) -> Listener:
    """Add `[slack.threads.weekly]` (its own channel) and return a listener that reads it."""
    with env.config.open("a", encoding="utf-8") as config:
        config.write('\n[slack.threads.weekly]\nchannel = "C0WEEKLY"\n')
        config.write('parent_match = { text_contains = "Weekly" }\n')
    return build_listener(env, slack_site)


async def test_thread_shows_the_matched_parent(listener: Listener, fake_slack: FakeSlack) -> None:
    """Pattern, permalink, sender and the first line of the parent."""
    fake_slack.history = [PARENT]

    await listener.command("thread")

    assert replies(fake_slack) == [
        f"thread default: {FAKE_LINK}/C0STANDUP/p1793610000000100\nfrom U0BOT · Daily standup",
    ]


async def test_thread_without_a_match_says_how_to_recover(
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """No parent today: the reply names the pattern and the two ways out."""
    await listener.command("thread")

    assert replies(fake_slack) == [NO_PARENT]


async def test_thread_pin_show_and_clear(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """A pinned link is shown as the pin until it is cleared."""
    await listener.command(f"thread {PIN_LINK}")
    await listener.command("thread")
    await listener.command("thread clear")
    await listener.command("thread clear")

    assert replies(fake_slack) == [
        f"pinned for the next post: {PIN_LINK}",
        f"thread pin: {PIN_LINK}",
        "pin cleared",
        "no pin set",
    ]
    assert not (pending.state / "thread_pin.json").exists()


async def test_thread_name_pins_what_it_finds(
    pending: ServiceEnv,
    slack_site: str,
    fake_slack: FakeSlack,
) -> None:
    """`thread weekly` pins the weekly parent; an unknown name lists the known ones."""
    listener = with_weekly(pending, slack_site)
    fake_slack.history = [PARENT, WEEKLY_PARENT]

    await listener.command("thread weekly")
    await listener.command("thread nope")

    assert replies(fake_slack) == [
        f"pinned weekly: {FAKE_LINK}/C0WEEKLY/p1793606400000200",
        "unknown thread nope; known: default, weekly",
    ]
    pin = json.loads((pending.state / "thread_pin.json").read_text(encoding="utf-8"))
    assert (pin["channel"], pin["ts"], pin["source"]) == ("C0WEEKLY", WEEKLY_TS, "weekly")


async def test_post_sends_the_pending_draft_to_the_thread(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """The reply carries the permalink; the run is posted and its DM closed."""
    fake_slack.history = [PARENT]

    await listener.command("post")

    record = json.loads(pending.record())
    assert record["status"] == "posted"
    assert replies(fake_slack) == [f"✅ Posted {record['posted']['permalink']}"]
    (post,) = [r for r in fake_slack.called("chat.postMessage") if r.body.get("thread_ts")]
    assert (post.body["channel"], post.body["thread_ts"]) == ("C0STANDUP", PARENT_TS)
    assert fake_slack.called("chat.update")[0].body["text"].startswith("✅ Posted")


async def test_post_a_named_thread_uses_up_the_pin_only_when_unnamed(
    pending: ServiceEnv,
    slack_site: str,
    fake_slack: FakeSlack,
) -> None:
    """`post weekly` ignores the pin, which stays for a later post."""
    listener = with_weekly(pending, slack_site)
    fake_slack.history = [WEEKLY_PARENT]
    await listener.command(f"thread {PIN_LINK}")

    await listener.command("post weekly")

    (post,) = [r for r in fake_slack.called("chat.postMessage") if r.body.get("thread_ts")]
    assert (post.body["channel"], post.body["thread_ts"]) == ("C0WEEKLY", WEEKLY_TS)
    assert (pending.state / "thread_pin.json").exists()


async def test_post_into_the_pin_clears_it(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """No pattern match needed; the pin ends with the post."""
    await listener.command(f"thread {PIN_LINK}")

    await listener.command("post")

    (post,) = [r for r in fake_slack.called("chat.postMessage") if r.body.get("thread_ts")]
    assert (post.body["channel"], post.body["thread_ts"]) == ("C0OTHER", "1793600000.000300")
    assert not (pending.state / "thread_pin.json").exists()


async def test_post_refused_replies_the_reason(
    pending: ServiceEnv,
    listener: Listener,
    fake_slack: FakeSlack,
) -> None:
    """No parent and no pin: nothing posted, the run stays pending."""
    await listener.command("post")
    await listener.command("post nope")

    assert replies(fake_slack) == [NO_PARENT, "unknown thread nope; known: default"]
    assert json.loads(pending.record())["status"] == "pending"


async def test_post_with_nothing_pending(
    env: ServiceEnv,
    slack_site: str,
    fake_slack: FakeSlack,
) -> None:
    """No pending run: point the owner at `run`."""
    await build_listener(env, slack_site).command("post")

    assert replies(fake_slack) == ["no pending draft — /standup run"]
