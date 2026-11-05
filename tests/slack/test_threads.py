import datetime as dt
import json

import pytest
from slack_sdk.web.async_client import AsyncWebClient
from typer.testing import CliRunner, Result

from daily_report.cli import app
from daily_report.compose.models import Target
from daily_report.config.exceptions import UnknownThreadError
from daily_report.config.models import Config, ParentMatch, ThreadSpec
from daily_report.effects.services import DryRunEffects, Effects, RealEffects
from daily_report.retry import RetryPolicy
from daily_report.slack.exceptions import NoParentError
from daily_report.slack.fake import FakeSlack
from daily_report.slack.services import ParentFinder, PostService, SlackClient
from tests.slack.helpers import NOW, OLD_TS, PARENT_TS, RUN_ID, TOKEN, SlackEnv

LATER_TS = "1762077000.000200"  # 2025-11-02 09:50 UTC
PIN_TS = "1762070000.000300"
PIN_LINK = "https://acme.slack.com/archives/C0OTHER/p1762070000000300"
WEEKLY = (
    '\n[slack.threads.weekly]\nchannel = "C0WEEKLY"\nparent_match = { text_regex = "^Weekly" }\n'
)
WEEKLY_PARENT = {"ts": LATER_TS, "user": "U0LEAD", "text": "Weekly sync"}


def finder(env: SlackEnv, lookback_days: int = 0, **match: str) -> ParentFinder:
    """Return a finder for the default pattern with `match` rules."""
    client = SlackClient(AsyncWebClient(token=TOKEN, base_url=env.api_url), RetryPolicy())
    spec = ThreadSpec("default", "C0STANDUP", ParentMatch.model_validate(match), lookback_days)
    return ParentFinder(client, spec, dt.UTC)


def service(
    env: SlackEnv,
    thread: str | None = None,
    effects: Effects | None = None,
) -> PostService:
    """Build the post service over the config file `env` wrote."""
    settings = Config.model_validate(
        {
            "timezone": "UTC",
            "jira": {"base_url": "https://jira.example", "email": "owner@example.com"},
            "slack": {
                "channel": "C0STANDUP",
                "api_url": env.api_url,
                "parent_match": {"user_id": "U0BOT"},
                "threads": {
                    "weekly": {"channel": "C0WEEKLY", "parent_match": {"text_regex": "^Weekly"}},
                },
            },
        },
    )
    return PostService.from_config(
        settings,
        token=TOKEN,
        state_dir=env.state,
        effects=effects or RealEffects(),
        thread=thread,
    )


def write_pin(env: SlackEnv, day: str = "2025-11-02") -> None:
    """Pin the thread `PIN_TS` in `C0OTHER` on `day`."""
    pin = {"channel": "C0OTHER", "ts": PIN_TS, "permalink": PIN_LINK, "source": "permalink"}
    env.state.mkdir(parents=True, exist_ok=True)
    (env.state / "thread_pin.json").write_text(json.dumps({**pin, "day": day}), encoding="utf-8")


def thread(env: SlackEnv, *args: str, token: str | None = TOKEN, dry_run: bool = False) -> Result:
    """Run `daily-report [--dry-run] thread <args> --config ... [--state-dir ...]`."""
    argv = [*(["--dry-run"] if dry_run else []), "thread", *args, "--config", str(env.config)]
    if args[0] != "list":
        argv += ["--state-dir", str(env.state)]
    return CliRunner().invoke(app, argv, env={"SLACK_USER_TOKEN": token} if token else {})


async def test_a_bot_parent_matches_on_attachment_text(
    env: SlackEnv,
    fake_slack: FakeSlack,
) -> None:
    """Empty `text`, words in an attachment: bot_id plus text_contains still finds it."""
    fake_slack.history = [
        {"ts": PARENT_TS, "bot_id": "B1", "text": "", "attachments": [{"text": "Daily standup"}]},
        {"ts": LATER_TS, "bot_id": "B2", "text": "Daily standup"},
    ]

    found = await finder(env, bot_id="B1", text_contains="Daily standup").find(NOW)

    assert found.ts == PARENT_TS


async def test_username_and_regex_rules_must_all_hold(
    env: SlackEnv,
    fake_slack: FakeSlack,
) -> None:
    """A message with the right words from another sender is skipped."""
    fake_slack.history = [
        {"ts": PARENT_TS, "username": "standup-bot", "text": "Standup for Nov 2"},
        {"ts": LATER_TS, "username": "other-bot", "text": "Standup for Nov 2"},
    ]

    found = await finder(env, username="standup-bot", text_regex=r"Standup for \w+ \d+").find(NOW)

    assert found.ts == PARENT_TS
    assert found.author == "standup-bot"


async def test_the_newest_match_wins(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """Two parents today: replies go under the later one."""
    fake_slack.history.append({"ts": LATER_TS, "user": "U0BOT", "text": "Daily standup again"})

    assert (await finder(env, user_id="U0BOT").find(NOW)).ts == LATER_TS


async def test_lookback_days_widens_the_window(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """A parent from three days ago is found only with a lookback that reaches it."""
    fake_slack.history = [{"ts": OLD_TS, "user": "U0BOT", "text": "Daily standup"}]

    with pytest.raises(NoParentError) as caught:
        await finder(env, user_id="U0BOT").find(NOW)
    found = await finder(env, 7, user_id="U0BOT").find(NOW)

    assert str(caught.value) == (
        "no thread matches default in #C0STANDUP since 2025-11-02; "
        "pin one with a thread permalink or post with --target channel"
    )
    assert found.ts == OLD_TS
    assert fake_slack.called("conversations.history")[-1].body["oldest"] == "1761436800.000000"


async def test_a_named_thread_posts_in_its_own_channel(
    env: SlackEnv,
    fake_slack: FakeSlack,
) -> None:
    """`weekly` reads and posts in C0WEEKLY under its own parent."""
    fake_slack.history.append(WEEKLY_PARENT)

    posted = await service(env, "weekly").post(RUN_ID, style="bluf", target="thread")

    assert posted is not None
    assert fake_slack.called("conversations.history")[0].body["channel"] == "C0WEEKLY"
    (request,) = fake_slack.called("chat.postMessage")
    assert (request.body["channel"], request.body["thread_ts"]) == ("C0WEEKLY", LATER_TS)


async def test_todays_pin_wins_and_ends_with_the_post(
    env: SlackEnv,
    fake_slack: FakeSlack,
) -> None:
    """No pattern matches, the pin is used without reading history, then it is gone."""
    fake_slack.history = []
    write_pin(env)

    await service(env).post(RUN_ID, style="bluf", target="thread")

    (request,) = fake_slack.called("chat.postMessage")
    assert (request.body["channel"], request.body["thread_ts"]) == ("C0OTHER", PIN_TS)
    assert fake_slack.called("conversations.history") == []
    assert not (env.state / "thread_pin.json").exists()


@pytest.mark.parametrize(
    ("thread_name", "day", "target", "channel", "thread_ts"),
    [
        ("weekly", "2025-11-02", "thread", "C0WEEKLY", LATER_TS),  # a named thread beats a pin
        (None, "2025-11-01", "thread", "C0STANDUP", PARENT_TS),  # yesterday's pin is stale
        (None, "2025-11-02", "channel", "C0STANDUP", None),  # a pin never applies top-level
    ],
)
async def test_a_pin_is_skipped(  # noqa: PLR0913 - one row of the table
    env: SlackEnv,
    fake_slack: FakeSlack,
    *,
    thread_name: str | None,
    day: str,
    target: Target,
    channel: str,
    thread_ts: str | None,
) -> None:
    """The pin stays for the next post when this one did not use it."""
    fake_slack.history.append(WEEKLY_PARENT)
    write_pin(env, day)

    await service(env, thread_name).post(RUN_ID, style="bluf", target=target)

    (request,) = fake_slack.called("chat.postMessage")
    assert (request.body["channel"], request.body.get("thread_ts")) == (channel, thread_ts)
    assert (env.state / "thread_pin.json").exists()


async def test_a_dry_run_post_keeps_the_pin(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """Nothing is sent, so the pin is still owed to the real post."""
    write_pin(env)

    dry = service(env, effects=DryRunEffects())

    assert await dry.post(RUN_ID, style="bluf", target="thread") is None

    assert fake_slack.called("chat.postMessage") == []
    assert (env.state / "thread_pin.json").exists()


def test_an_unknown_thread_fails_before_slack(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """The name is checked when the service is built."""
    with pytest.raises(UnknownThreadError, match="unknown thread nope; known: default, weekly"):
        service(env, "nope")

    assert fake_slack.requests == []


def test_thread_list_prints_every_pattern_offline(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """Default first, then the named threads; no token, no Slack call."""
    env.write_config(match='user_id = "U0BOT", text_contains = "Daily standup"', extra=WEEKLY)

    result = thread(env, "list", token=None)

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "default\tC0STANDUP\t0d\tuser_id=U0BOT text_contains=Daily standup",
        "weekly\tC0WEEKLY\t0d\ttext_regex=^Weekly",
    ]
    assert fake_slack.requests == []


def test_thread_list_without_slack_exits_2(env: SlackEnv) -> None:
    """No [slack] table, nothing to list."""
    env.write_config(slack=False)

    result = thread(env, "list")

    assert result.exit_code == 2
    assert "no [slack] table" in result.stderr


def test_thread_show_names_the_matched_parent(env: SlackEnv) -> None:
    """Source, place, permalink, sender and first line of the parent."""
    result = thread(env, "show")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "source: default",
        "channel: C0STANDUP",
        f"ts: {PARENT_TS}",
        "permalink: https://fake.slack.com/archives/C0STANDUP/p1762074000000100",
        "author: U0BOT",
        "text: Daily standup",
    ]


def test_thread_show_prefers_todays_pin(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """A pin is shown as is, without reading Slack history."""
    write_pin(env)

    result = thread(env, "show")

    assert result.stdout.splitlines() == [
        "source: pin",
        "channel: C0OTHER",
        f"ts: {PIN_TS}",
        f"permalink: {PIN_LINK}",
    ]
    assert fake_slack.called("conversations.history") == []


@pytest.mark.parametrize(
    ("setup", "token", "code", "message"),
    [
        ("no-parent", TOKEN, 10, "no thread matches default in #C0STANDUP since 2025-11-02"),
        ("", None, 2, "SLACK_USER_TOKEN is not set"),
        ("bad-pin", TOKEN, 2, "run `daily-report thread clear`"),
        ("not_in_channel", TOKEN, 9, "Slack conversations.history failed: not_in_channel"),
    ],
)
def test_thread_show_failures(  # noqa: PLR0913 - one row of the table
    env: SlackEnv,
    fake_slack: FakeSlack,
    *,
    setup: str,
    token: str | None,
    code: int,
    message: str,
) -> None:
    """Each failure exits with its code and says why."""
    if setup == "no-parent":
        fake_slack.history = []
    if setup == "bad-pin":
        env.state.joinpath("thread_pin.json").write_text("{}", encoding="utf-8")
    if setup == "not_in_channel":
        fake_slack.script("conversations.history", "not_in_channel")

    result = thread(env, "show", token=token)

    assert result.exit_code == code
    assert message in result.stderr


def test_thread_pin_a_link_needs_no_token_or_slack(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """The link alone is enough; the pin lasts for today."""
    result = thread(env, "pin", PIN_LINK, token=None)

    assert result.exit_code == 0, result.output
    assert result.stdout == f"pinned {PIN_LINK}\n"
    assert json.loads((env.state / "thread_pin.json").read_text(encoding="utf-8")) == {
        "channel": "C0OTHER",
        "ts": PIN_TS,
        "permalink": PIN_LINK,
        "source": "permalink",
        "day": "2025-11-02",
    }
    assert fake_slack.requests == []


def test_thread_pin_dry_run_writes_nothing(env: SlackEnv) -> None:
    """The pin is announced, not written."""
    result = thread(env, "pin", PIN_LINK, dry_run=True)

    assert result.exit_code == 0, result.output
    assert result.stdout == f"pinned {PIN_LINK}\n"
    assert "[dry-run] would write thread_pin.json" in result.stderr
    assert not (env.state / "thread_pin.json").exists()


def test_thread_pin_a_reply_link_pins_its_root(env: SlackEnv) -> None:
    """A link to a reply pins the thread it belongs to."""
    reply = f"{PIN_LINK[:-6]}000900?thread_ts={PIN_TS}"

    assert thread(env, "pin", reply).exit_code == 0

    pin = json.loads((env.state / "thread_pin.json").read_text(encoding="utf-8"))
    assert pin["ts"] == PIN_TS


def test_thread_pin_a_name_pins_what_it_finds(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """`weekly` is looked up now and pinned under its name."""
    env.write_config(extra=WEEKLY)
    fake_slack.history.append(WEEKLY_PARENT)

    result = thread(env, "pin", "weekly")

    assert result.stdout == "pinned https://fake.slack.com/archives/C0WEEKLY/p1762077000000200\n"
    pin = json.loads((env.state / "thread_pin.json").read_text(encoding="utf-8"))
    assert (pin["channel"], pin["ts"], pin["source"]) == ("C0WEEKLY", LATER_TS, "weekly")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("not-a-link-or-name", "unknown thread not-a-link-or-name; known: default"),
        ("https://example.com/x", "not a Slack message link: https://example.com/x"),
    ],
)
def test_thread_pin_rejects_junk(env: SlackEnv, value: str, message: str) -> None:
    """Neither a thread name nor a Slack link: exit 2 and nothing pinned."""
    result = thread(env, "pin", value)

    assert result.exit_code == 2
    assert message in result.stderr
    assert not (env.state / "thread_pin.json").exists()


def test_thread_clear_twice(env: SlackEnv) -> None:
    """The first clear removes the pin; the second finds none."""
    write_pin(env)

    first = thread(env, "clear", token=None)
    second = thread(env, "clear", token=None)

    assert (first.stdout, second.stdout) == ("pin cleared\n", "no pin set\n")
    assert not (env.state / "thread_pin.json").exists()


def test_post_thread_option_targets_the_named_thread(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """`post --thread weekly` replies under the weekly parent."""
    env.write_config(extra=WEEKLY)
    fake_slack.history.append(WEEKLY_PARENT)

    result = env.post("--style", "bluf", "--thread", "weekly", "--yes")

    assert result.exit_code == 0, result.output
    (request,) = fake_slack.called("chat.postMessage")
    assert (request.body["channel"], request.body["thread_ts"]) == ("C0WEEKLY", LATER_TS)


def test_post_unknown_thread_exits_2(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """A thread name not in the config never reaches Slack."""
    result = env.post("--style", "bluf", "--thread", "nope", "--yes")

    assert result.exit_code == 2
    assert "unknown thread nope; known: default" in result.stderr
    assert fake_slack.requests == []
