import asyncio
import datetime as dt
import os
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from daily_report.collect.exceptions import SearchFailedError
from daily_report.collect.models import Report
from daily_report.collect.services import CollectService, SnapshotStore
from daily_report.config.exceptions import ConfigError
from daily_report.config.models import Config
from daily_report.config.services import ConfigResolver
from daily_report.effects.commands import DryRunOption, command_effects
from daily_report.effects.services import Effects
from daily_report.exc import TemplatedError
from daily_report.exit_codes import ExitCode
from daily_report.jira.exceptions import JiraAuthError
from daily_report.jira.services import JiraClient
from daily_report.logger import get_logger
from daily_report.retry import RetryPolicy

ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", help="Config file. Default: $DAILY_REPORT_CONFIG."),
]
StateDirOption = Annotated[
    Path | None,
    typer.Option(
        "--state-dir",
        help="State dir. Default: $DAILY_REPORT_STATE_DIR, then state_dir in the config file.",
    ),
]


def _fail(exc: TemplatedError, code: ExitCode) -> NoReturn:
    typer.echo(str(exc), err=True)
    raise typer.Exit(code) from exc


async def _collect(
    config: Config,
    *,
    token: str,
    state_dir: Path,
    effects: Effects,
    as_of: dt.datetime,
) -> Report:
    jira = config.jira
    async with JiraClient.connect(
        base_url=jira.base_url,
        email=jira.email,
        token=token,
        retry=RetryPolicy(),
    ) as client:
        store = SnapshotStore(state_dir, effects)
        service = CollectService(client=client, store=store, jira=jira, zone=config.zone)
        return await service.run(as_of)


def collect(
    ctx: typer.Context,
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    as_of: Annotated[
        dt.datetime | None,
        typer.Option(
            parser=dt.datetime.fromisoformat,
            metavar="ISO-8601",
            help="Report as of this instant (naive means the config timezone). Default: now.",
        ),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(help="Also write the report here (written even under --dry-run)."),
    ] = None,
    dry_run: DryRunOption = False,
) -> None:
    """Diff my Jira tickets against the last snapshot and print the report JSON."""
    effects = command_effects(ctx, dry_run=dry_run)
    resolver = ConfigResolver(os.environ)
    try:
        settings = resolver.load(config)
        root = resolver.state_dir(state_dir, settings)
        token = resolver.jira_token()
    except ConfigError as exc:
        _fail(exc, ExitCode.CONFIG)
    try:
        report = asyncio.run(
            _collect(
                settings,
                token=token,
                state_dir=root,
                effects=effects,
                as_of=as_of or dt.datetime.now(tz=dt.UTC),
            ),
        )
    except JiraAuthError as exc:
        _fail(exc, ExitCode.JIRA_AUTH)
    except SearchFailedError as exc:
        _fail(exc, ExitCode.JIRA_SEARCH)
    log = get_logger()
    for warning in report.warnings:
        log.warning("jira issue skipped", **warning.model_dump())
    text = report.model_dump_json(indent=2)
    if out:
        out.write_text(text + "\n", encoding="utf-8")
    typer.echo(text)
