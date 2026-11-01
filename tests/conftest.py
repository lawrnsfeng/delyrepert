import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer

from daily_report.jira.fake import FakeJira, JiraFixtures

JIRA_FIXTURES = Path(__file__).parent / "fixtures" / "jira"


@pytest.fixture
def fake_jira() -> FakeJira:
    """Fake Jira state loaded from `tests/fixtures/jira/`."""
    return FakeJira(JiraFixtures.load(JIRA_FIXTURES))


@pytest.fixture
def jira_url(fake_jira: FakeJira) -> Iterator[str]:
    """Serve `fake_jira` on a free port from a background loop, so sync CLI tests can use it."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    server = TestServer(fake_jira.build_app())
    asyncio.run_coroutine_threadsafe(server.start_server(), loop).result()
    yield str(server.make_url("")).rstrip("/")
    asyncio.run_coroutine_threadsafe(server.close(), loop).result()
    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    loop.close()


@pytest.fixture
def config_file(tmp_path: Path, jira_url: str) -> Path:
    """Write a config file pointing at the fake Jira, with no state_dir."""
    path = tmp_path / "config.toml"
    path.write_text(
        f'timezone = "UTC"\n\n[jira]\nbase_url = "{jira_url}"\nemail = "owner@example.com"\n',
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from an environment with no daily-report variables."""
    for name in (
        "DAILY_REPORT_CONFIG",
        "DAILY_REPORT_STATE_DIR",
        "DAILY_REPORT_DRY_RUN",
        "JIRA_API_TOKEN",
        "SLACK_USER_TOKEN",
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "DAILY_REPORT_SECRETS_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
