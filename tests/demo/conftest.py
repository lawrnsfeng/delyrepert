import asyncio
import threading
from collections.abc import Generator, Iterator
from contextlib import contextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from daily_report.jira.fake import FakeJira, JiraFixtures
from daily_report.slack import services
from daily_report.slack.fake import API_PREFIX, FakeSlack
from tests.demo.helpers import FIXTURES, NOW


@contextmanager
def serve(app: web.Application, prefix: str = "") -> Generator[str]:
    """Serve `app` from a background loop, so sync CLI tests can use it."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    server = TestServer(app)
    asyncio.run_coroutine_threadsafe(server.start_server(), loop).result()
    yield str(server.make_url(prefix)).rstrip("/") + ("/" if prefix else "")
    asyncio.run_coroutine_threadsafe(server.close(), loop).result()
    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    loop.close()


@pytest.fixture
def demo_jira_url() -> Iterator[str]:
    """Serve the demo fixtures' fake Jira."""
    with serve(FakeJira(JiraFixtures.load(FIXTURES / "jira")).build_app()) as url:
        yield url


@pytest.fixture
def fake_slack() -> FakeSlack:
    """Return a fake Slack in the demo workspace."""
    return FakeSlack()


@pytest.fixture
def slack_url(fake_slack: FakeSlack) -> Iterator[str]:
    """Serve `fake_slack`."""
    with serve(fake_slack.build_app(), API_PREFIX) as url:
        yield url


@pytest.fixture(autouse=True)
def today(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin "now" to the day of the fake Slack's first message."""
    monkeypatch.setattr(services, "utc_now", lambda: NOW)
    monkeypatch.delenv("DAILY_REPORT_DEMO_SLACK_USER_TOKEN", raising=False)
    monkeypatch.delenv("DAILY_REPORT_DEMO_FIXTURES", raising=False)
    monkeypatch.delenv("DAILY_REPORT_DEMO_STATE_DIR", raising=False)
