"""Ways to run the collector in tests: the CLI in-process, or the service directly."""

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from typer.testing import CliRunner, Result

from daily_report.cli import app
from daily_report.collect.models import Report
from daily_report.collect.services import CollectService, SnapshotStore
from daily_report.config.models import JiraConfig
from daily_report.effects.services import Effects, RealEffects
from daily_report.jira.services import JiraClient
from daily_report.retry import RetryPolicy

AS_OF = "2026-11-03T09:00:00+00:00"
CREDENTIAL = "fake-api-token"


def run_cli(*args: str, env: dict[str, str] | None = None) -> Result:
    """Invoke `daily-report <args>` in-process with a Jira token set."""
    return CliRunner().invoke(app, list(args), env={"JIRA_API_TOKEN": CREDENTIAL, **(env or {})})


def collect_args(config: Path, state_dir: Path, as_of: str = AS_OF) -> list[str]:
    """Return `collect --config … --state-dir … --as-of …`."""
    return ["collect", "--config", str(config), "--state-dir", str(state_dir), "--as-of", as_of]


async def no_sleep(_: float) -> None:
    """Sleeper that returns at once."""


@dataclass(frozen=True)
class Collector:
    """Runs `CollectService` against the fake Jira and a state dir."""

    jira_url: str
    state_dir: Path

    async def run(
        self,
        as_of: str = AS_OF,
        *,
        effects: Effects | None = None,
        retry: RetryPolicy | None = None,
        jira: dict[str, object] | None = None,
    ) -> Report:
        """Collect as of `as_of` and return the report."""
        config = JiraConfig.model_validate(
            {"base_url": self.jira_url, "email": "owner@example.com", **(jira or {})},
        )
        async with JiraClient.connect(
            base_url=self.jira_url,
            email=config.email,
            token=CREDENTIAL,
            retry=retry or RetryPolicy(sleep=no_sleep),
        ) as client:
            service = CollectService(
                client=client,
                store=SnapshotStore(self.state_dir, effects or RealEffects()),
                jira=config,
                zone=dt.UTC,
            )
            return await service.run(dt.datetime.fromisoformat(as_of))


def items_by_key(report: Report) -> dict[str, dict[str, Any]]:
    """Return the report's items as JSON objects keyed by issue key."""
    return {item["key"]: item for item in json.loads(report.model_dump_json())["items"]}
