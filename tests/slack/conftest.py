import asyncio
import datetime as dt
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer

from daily_report.slack import services
from daily_report.slack.fake import API_PREFIX, FakeSlack
from tests.slack.helpers import NOW, PARENT_TS, SlackEnv, notes, write_draft


@pytest.fixture
def fake_slack() -> FakeSlack:
    """Fake Slack holding today's standup parent and the `alert-anim` emoji."""
    parent = {"ts": PARENT_TS, "user": "U0BOT", "text": "Daily standup"}
    return FakeSlack(history=[parent], emoji={"alert-anim": "https://emoji.example/a.gif"})


@pytest.fixture
def slack_url(fake_slack: FakeSlack) -> Iterator[str]:
    """Serve `fake_slack` from a background loop, so sync CLI tests can use it."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    server = TestServer(fake_slack.build_app())
    asyncio.run_coroutine_threadsafe(server.start_server(), loop).result()
    yield str(server.make_url(API_PREFIX))
    asyncio.run_coroutine_threadsafe(server.close(), loop).result()
    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    loop.close()


@pytest.fixture(autouse=True)
def today(monkeypatch: pytest.MonkeyPatch) -> dt.datetime:
    """Pin "now" to the day of the fake's standup parent."""
    monkeypatch.setattr(services, "utc_now", lambda: NOW)
    return NOW


@pytest.fixture
def env(tmp_path: Path, slack_url: str) -> SlackEnv:
    """Config file, state dir with a draft carrying 2 pending notes, and a user token."""
    state = tmp_path / "state"
    pending = notes()
    write_draft(state, pending)
    inbox = state / "notes" / "pending.jsonl"
    inbox.parent.mkdir(parents=True)
    inbox.write_text("".join(n.model_dump_json() + "\n" for n in pending), encoding="utf-8")
    slack_env = SlackEnv(tmp_path, state, slack_url)
    slack_env.write_config()
    return slack_env
