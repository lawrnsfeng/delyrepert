import asyncio
import datetime as dt
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from typer.testing import CliRunner, Result

from daily_report.cli import app
from daily_report.jira.fake import FakeJira
from daily_report.service import install_commands, services
from daily_report.slack.fake import API_PREFIX, FakeSlack
from tests.compose.helpers import r1_baseline, r1_jira
from tests.service.systemd import FakeSystemd

NOW = dt.datetime(2026, 11, 2, 9, 5, tzinfo=dt.UTC)
RUN_ID = "20261102-090500"
SECRETS = {
    "SLACK_BOT_TOKEN": "s3cr3t-bot",
    "SLACK_APP_TOKEN": "s3cr3t-app",
    "SLACK_USER_TOKEN": "s3cr3t-user",
    "JIRA_API_TOKEN": "s3cr3t-jira",
}


@dataclass
class Clock:
    """The instant the service sees as "now"."""

    now: dt.datetime


def serve(web_app: web.Application) -> Iterator[str]:
    """Serve an `aiohttp.web` app from a background loop, so sync CLI tests can use it."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    server = TestServer(web_app)
    asyncio.run_coroutine_threadsafe(server.start_server(), loop).result()
    yield str(server.make_url(""))
    asyncio.run_coroutine_threadsafe(server.close(), loop).result()
    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    loop.close()


@pytest.fixture
def fake_jira() -> FakeJira:
    """Fake Jira serving R1's tickets."""
    return FakeJira(r1_jira())


@pytest.fixture
def fake_slack() -> FakeSlack:
    """Fake Slack with no standup thread, so only `--target channel` can post."""
    return FakeSlack()


@pytest.fixture
def jira_site(fake_jira: FakeJira) -> Iterator[str]:
    """Serve the fake Jira."""
    yield from serve(fake_jira.build_app())


@pytest.fixture
def slack_site(fake_slack: FakeSlack) -> Iterator[str]:
    """Serve the fake Slack."""
    yield from serve(fake_slack.build_app())


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Pin "now" to R1's day."""
    pinned = Clock(NOW)
    monkeypatch.setattr(services, "utc_now", lambda: pinned.now)
    return pinned


@dataclass
class ServiceEnv:
    """A config file, secrets file and state dir wired to the fakes."""

    root: Path
    state: Path

    @property
    def config(self) -> Path:
        """Return the config path."""
        return self.root / "config.toml"

    @property
    def secrets(self) -> Path:
        """Return the secrets file path."""
        return self.root / "secrets.env"

    def record(self, run_id: str = RUN_ID) -> str:
        """Return the text of a run record."""
        return (self.state / "runs" / f"{run_id}.json").read_text(encoding="utf-8")

    def cli(
        self,
        command: str,
        *args: str,
        dry_run: bool = False,
        secrets: bool = True,
        state: bool = True,
    ) -> Result:
        """Run `daily-report [--dry-run] <command> <args>` against this env."""
        argv = [*(["--dry-run"] if dry_run else []), command, "--config", str(self.config)]
        if state:
            argv += ["--state-dir", str(self.state)]
        if secrets:
            argv += ["--secrets-file", str(self.secrets)]
        return CliRunner().invoke(app, [*argv, *args])


@pytest.fixture
def env(tmp_path: Path, jira_site: str, slack_site: str, clock: Clock) -> ServiceEnv:
    """Return an env with R1's Friday snapshot and a 0600 secrets file."""
    del clock
    state = tmp_path / "state"
    r1_baseline(state)
    lines = ["# fixture secrets", ""]
    lines += [
        f'{name}="{value}"' if name == "SLACK_USER_TOKEN" else f"{name}={value}"
        for name, value in SECRETS.items()
    ]
    secrets = tmp_path / "secrets.env"
    secrets.write_text("\n".join(lines) + "\n", encoding="utf-8")
    secrets.chmod(0o600)
    config = tmp_path / "config.toml"
    config.write_text(
        'timezone = "UTC"\n\n'
        f'[jira]\nbase_url = "{jira_site.rstrip("/")}"\nemail = "owner@example.com"\n\n'
        f'[slack]\nchannel = "C0STANDUP"\napi_url = "{slack_site.rstrip("/")}{API_PREFIX}"\n'
        'parent_match = { user_id = "U0BOT" }\n',
        encoding="utf-8",
    )
    return ServiceEnv(tmp_path, state)


@pytest.fixture(autouse=True)
def fake_systemd(monkeypatch: pytest.MonkeyPatch) -> FakeSystemd:
    """Never reach the host's systemd: `status` reads from a scripted fake."""
    systemd = FakeSystemd()
    monkeypatch.setattr(install_commands, "SubprocessRunner", lambda: systemd)
    return systemd
