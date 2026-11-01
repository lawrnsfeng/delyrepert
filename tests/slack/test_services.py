import datetime as dt

from daily_report.config.models import Config
from daily_report.effects.services import RealEffects
from daily_report.logger import configure_console_logging
from daily_report.retry import RetryPolicy
from daily_report.slack.fake import FakeSlack
from daily_report.slack.services import PostService, utc_now
from tests.slack.helpers import PARENT_TS, RUN_ID, TOKEN, SlackEnv


def service(env: SlackEnv, sleeps: list[float] | None = None) -> PostService:
    """Build the service the way service-control's approve does."""
    settings = Config.model_validate(
        {
            "timezone": "UTC",
            "jira": {"base_url": "https://jira.example", "email": "owner@example.com"},
            "slack": {
                "channel": "C0STANDUP",
                "api_url": env.api_url,
                "parent_match": {"user_id": "U0BOT"},
            },
        },
    )
    record = [] if sleeps is None else sleeps

    async def sleep(seconds: float) -> None:
        record.append(seconds)

    return PostService.from_config(
        settings,
        token=TOKEN,
        state_dir=env.state,
        effects=RealEffects(),
        retry=RetryPolicy(sleep=sleep, jitter=lambda: 0.0),
    )


async def test_ac11_post_returns_where_it_landed(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """post(run_id, style, target) posts the ytb render and returns Posted."""
    posted = await service(env).post(RUN_ID, style="ytb", target="channel")

    assert posted is not None
    assert posted.channel == "C0STANDUP"
    assert (
        posted.permalink
        == f"https://fake.slack.com/archives/C0STANDUP/p{posted.ts.replace('.', '')}"
    )
    (request,) = fake_slack.called("chat.postMessage")
    assert request.body["text"].startswith("Standup")
    assert len(fake_slack.called("chat.getPermalink")) == 1


async def test_ac7_retry_after_is_honoured(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """A 429 with Retry-After: 1, then ok: 2 requests and one sleep of 1 s."""
    fake_slack.script("chat.postMessage", "ratelimited", status=429, retry_after=1)
    sleeps: list[float] = []

    posted = await service(env, sleeps).post(RUN_ID, style="bluf", target="thread")

    assert posted is not None
    assert len(fake_slack.called("chat.postMessage")) == 2
    assert sleeps == [1]


async def test_ac7_server_errors_are_retried(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """An HTTP 500 backs off 1 s, then succeeds."""
    fake_slack.script("chat.postMessage", "internal_error", status=500)
    sleeps: list[float] = []

    assert await service(env, sleeps).post(RUN_ID, style="bluf", target="thread")

    assert sleeps == [1.0]


async def test_a_missing_permalink_still_records_the_post(
    env: SlackEnv,
    fake_slack: FakeSlack,
) -> None:
    """The message is out, so a failed getPermalink must not allow a second post."""
    fake_slack.script("chat.getPermalink", "message_not_found")
    configure_console_logging()  # the CLI tests left structlog bound to a closed stream
    svc = service(env)

    posted = await svc.post(RUN_ID, style="bluf", target="thread")

    assert posted is not None
    assert posted.permalink == ""
    assert env.inbox.read_text(encoding="utf-8") == ""
    assert fake_slack.called("chat.postMessage")[0].body["thread_ts"] == PARENT_TS


def test_utc_now_is_timezone_aware() -> None:
    """The default clock reports UTC."""
    assert utc_now().tzinfo is dt.UTC
