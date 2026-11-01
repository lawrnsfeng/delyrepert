import asyncio
import json
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

from daily_report.config.exceptions import AppTokenUnsetError, BotTokenUnsetError
from daily_report.config.services import ConfigResolver
from daily_report.slack import fake
from daily_report.slack_app import commands
from daily_report.slack_app.models import ACTION_IDS, VERBS
from daily_report.slack_app.timer import timer_next
from tests.service.conftest import SECRETS, ServiceEnv

ROOT = Path(__file__).parents[2]


class Connection:
    """Stands in for `AsyncSocketModeHandler`: remembers how it was built and never connects."""

    built: list[tuple[Any, str]] = []  # noqa: RUF012 - reset by the fixture
    started = 0

    def __init__(self, app: Any, app_token: str, **_: object) -> None:  # noqa: ANN401
        """Remember the app and the token."""
        Connection.built.append((app, app_token))

    async def start_async(self) -> None:
        """Count the connection instead of opening it."""
        Connection.started += 1


@pytest.fixture
def connection(monkeypatch: pytest.MonkeyPatch) -> type[Connection]:
    """Replace Socket Mode by a stub that counts connections."""
    Connection.built = []
    Connection.started = 0
    monkeypatch.setattr(commands, "AsyncSocketModeHandler", Connection)
    return Connection


def test_ac15_dry_run_serve_is_a_preflight(
    env: ServiceEnv,
    connection: type[Connection],
    fake_slack: fake.FakeSlack,
) -> None:
    """Only auth.test reaches Slack; the bot user, the verbs and the action ids are printed."""
    result = env.cli("serve", dry_run=True)

    assert result.exit_code == 0, result.output
    assert [r.method for r in fake_slack.requests] == ["auth.test"]
    assert fake_slack.requests[0].authorization == f"Bearer {SECRETS['SLACK_BOT_TOKEN']}"
    assert "[dry-run] would open Socket Mode as U0OWNER" in result.stderr
    assert result.stdout.splitlines() == [
        "verbs: " + " ".join(VERBS),
        "actions: " + " ".join(ACTION_IDS),
    ]
    assert (len(VERBS), len(ACTION_IDS)) == (7, 5)
    assert connection.built == []


def test_ac15_dry_run_serve_without_an_owner_exits_2(
    env: ServiceEnv,
    fake_slack: fake.FakeSlack,
) -> None:
    """No owner_slack_user_id: exit 2 before any Slack call."""
    text = env.config.read_text(encoding="utf-8")
    env.config.write_text(text.replace('owner_slack_user_id = "U0OWNER"\n', ""), encoding="utf-8")

    result = env.cli("serve", dry_run=True)

    assert result.exit_code == 2
    assert fake_slack.requests == []


def test_ac15_serve_connects_over_socket_mode_with_json_logs(
    env: ServiceEnv,
    connection: type[Connection],
) -> None:
    """Without --dry-run the listener opens Socket Mode with the app token and logs JSON."""
    result = env.cli("serve")

    assert result.exit_code == 0, result.output
    assert [token for _, token in connection.built] == [SECRETS["SLACK_APP_TOKEN"]]
    assert connection.started == 1
    logged = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
    assert [line["event"] for line in logged] == ["serving"]
    assert SECRETS["SLACK_APP_TOKEN"] not in result.output


def test_ac15_serve_exits_9_when_auth_test_fails(
    env: ServiceEnv,
    fake_slack: fake.FakeSlack,
    connection: type[Connection],
) -> None:
    """A bad bot token is a Slack API error."""
    fake_slack.script("auth.test", "invalid_auth")

    result = env.cli("serve")

    assert result.exit_code == 9
    assert "invalid_auth" in result.stderr
    assert connection.built == []


@pytest.mark.parametrize("command", ["serve", "run"])
def test_ac1_a_config_without_slack_exits_2(env: ServiceEnv, command: str) -> None:
    """Both commands need the [slack] table."""
    text = env.config.read_text(encoding="utf-8")
    env.config.write_text(text[: text.index("[slack]")], encoding="utf-8")

    result = env.cli(command)

    assert result.exit_code == 2
    assert "[slack]" in result.output


def test_ac10_timer_next_asks_systemctl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first line systemctl prints is the timer's next run; blank means unknown."""
    script = tmp_path / "systemctl"
    script.write_text('#!/bin/sh\necho "$FAKE_NEXT"\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("FAKE_NEXT", "Tue 2026-11-03 09:00:00 UTC")

    assert asyncio.run(timer_next()) == "Tue 2026-11-03 09:00:00 UTC"
    monkeypatch.setenv("FAKE_NEXT", "")
    assert asyncio.run(timer_next()) is None


def test_ac10_timer_next_is_unknown_without_systemctl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No systemctl on PATH: unknown, not an error."""
    monkeypatch.setenv("PATH", str(tmp_path))

    assert asyncio.run(timer_next()) is None


def test_missing_slack_tokens_are_named() -> None:
    """The resolver names the variable that is unset."""
    resolver = ConfigResolver({})
    with pytest.raises(BotTokenUnsetError, match="SLACK_BOT_TOKEN"):
        resolver.slack_bot_token()
    with pytest.raises(AppTokenUnsetError, match="SLACK_APP_TOKEN"):
        resolver.slack_app_token()


def test_manifest_declares_the_command_scopes_and_socket_mode() -> None:
    """`slack/manifest.yaml` matches the contract: /standup, three bot scopes, Socket Mode."""
    manifest = yaml.safe_load((ROOT / "slack" / "manifest.yaml").read_text(encoding="utf-8"))

    assert [c["command"] for c in manifest["features"]["slash_commands"]] == ["/standup"]
    assert manifest["oauth_config"]["scopes"]["bot"] == ["commands", "chat:write", "im:write"]
    assert manifest["settings"]["socket_mode_enabled"] is True
    assert manifest["settings"]["interactivity"]["is_enabled"] is True
