import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
import typer
import typer.main
from slack_bolt.async_app import AsyncApp
from slack_bolt.request.async_request import AsyncBoltRequest
from slack_bolt.response import BoltResponse
from slack_sdk.web.async_client import AsyncWebClient

from daily_report.cli import app as cli_app
from daily_report.effects.services import RealEffects
from daily_report.service.commands import _Env
from daily_report.slack.fake import API_PREFIX
from daily_report.slack_app.app import build_app
from daily_report.slack_app.dm import DmService
from daily_report.slack_app.handlers import StandupHandlers
from daily_report.slack_app.models import NOTE_BLOCK, NOTE_INPUT, NOTES_MODAL
from tests.service.conftest import (  # noqa: F401 - fixtures shared with the service tests
    SECRETS,
    Clock,
    ServiceEnv,
    clock,
    env,
    fake_jira,
    fake_slack,
    fake_systemd,
    jira_site,
    slack_site,
)

OWNER = "U0OWNER"
OTHER = "U0OTHER"
TEAM = {"id": "T0DEMO"}


async def next_run() -> str | None:
    """Stand in for `systemctl`: the timer fires at nine."""
    return "Tue 2026-11-03 09:00:00 UTC"


@dataclass
class Listener:
    """The Bolt app plus helpers that send it Socket Mode payloads."""

    app: AsyncApp
    response_url: str

    async def send(self, body: dict[str, Any]) -> BoltResponse:
        """Dispatch one payload as Socket Mode would, then wait for its listener to finish."""
        response = await self.app.async_dispatch(AsyncBoltRequest(body=body, mode="socket_mode"))
        # Bolt acks first and runs the listener as a task; the work is done once it ends.
        await asyncio.gather(*(t for t in asyncio.all_tasks() if t is not asyncio.current_task()))
        return response

    async def command(self, text: str, user: str = OWNER) -> BoltResponse:
        """Send `/standup <text>`."""
        return await self.send(
            {
                "command": "/standup",
                "text": text,
                "user_id": user,
                "team_id": TEAM["id"],
                "response_url": self.response_url,
                "trigger_id": "t1",
            },
        )

    async def action(
        self,
        action_id: str,
        run_id: str,
        user: str = OWNER,
        chosen: dict[str, str] | None = None,
    ) -> BoltResponse:
        """Click a DM control; `chosen` maps select action ids to their selected value."""
        values = {
            "standup_choices": {
                key: {"type": "static_select", "selected_option": {"value": value}}
                for key, value in (chosen or {}).items()
            },
        }
        return await self.send(
            {
                "type": "block_actions",
                "user": {"id": user, "team_id": TEAM["id"]},
                "team": TEAM,
                "trigger_id": "t1",
                "response_url": self.response_url,
                "actions": [{"action_id": action_id, "type": "button", "value": run_id}],
                "state": {"values": values},
            },
        )

    async def submit(self, run_id: str, text: str, user: str = OWNER) -> BoltResponse:
        """Submit the notes modal."""
        value = {NOTE_BLOCK: {NOTE_INPUT: {"type": "plain_text_input", "value": text}}}
        return await self.send(
            {
                "type": "view_submission",
                "user": {"id": user, "team_id": TEAM["id"]},
                "team": TEAM,
                "view": {
                    "type": "modal",
                    "callback_id": NOTES_MODAL,
                    "private_metadata": run_id,
                    "state": {"values": value},
                },
            },
        )


def build_listener(service: ServiceEnv, site: str) -> Listener:
    """Build the Bolt app over `service`'s state dir with the owner `U0OWNER`."""
    ctx = typer.Context(typer.main.get_command(cli_app), obj=RealEffects())
    state = _Env(ctx, service.config, service.state, dry_run=False)
    state.with_secrets(service.secrets)
    dms = DmService.from_config(
        state.settings,
        bot_token=SECRETS["SLACK_BOT_TOKEN"],
        owner=OWNER,
        state_dir=state.root,
        effects=state.effects,
    )
    api = f"{site.rstrip('/')}{API_PREFIX}"
    web = AsyncWebClient(token=SECRETS["SLACK_BOT_TOKEN"], base_url=api)
    app = build_app(StandupHandlers(state, dms, next_run), owner=OWNER, web=web)
    return Listener(app, f"{site.rstrip('/')}/response_url")


@pytest.fixture
def pending(env: ServiceEnv) -> ServiceEnv:  # noqa: F811 - the shared fixture, with a run
    """Return an env holding one pending scheduled run and its approval DM."""
    assert env.cli("run", "--scheduled").exit_code == 0
    return env


@pytest.fixture
def listener(pending: ServiceEnv, slack_site: str) -> Listener:  # noqa: F811
    """Return the Bolt app over the pending run."""
    return build_listener(pending, slack_site)
