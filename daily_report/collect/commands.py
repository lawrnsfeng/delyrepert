import asyncio
import datetime as dt
import os
from pathlib import Path
from typing import Annotated

import typer

from daily_report.collect.exceptions import SearchFailedError
from daily_report.collect.services import CollectService
from daily_report.config.exceptions import ConfigError
from daily_report.config.services import ConfigResolver
from daily_report.effects.commands import DryRunOption, command_effects
from daily_report.exit_codes import ExitCode, fail
from daily_report.jira.exceptions import JiraAuthError
from daily_report.logger import get_logger

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
AsOfOption = Annotated[
    dt.datetime | None,
    typer.Option(
        parser=dt.datetime.fromisoformat,
        metavar="ISO-8601",
        help="Report as of this instant (naive means the config timezone). Default: now.",
    ),
]


def collect(
    ctx: typer.Context,
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    as_of: AsOfOption = None,
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
        fail(exc, ExitCode.CONFIG)
    try:
        report = asyncio.run(
            CollectService.collect(
                settings,
                token=token,
                state_dir=root,
                effects=effects,
                as_of=as_of or dt.datetime.now(tz=dt.UTC),
            ),
        )
    except JiraAuthError as exc:
        fail(exc, ExitCode.JIRA_AUTH)
    except SearchFailedError as exc:
        fail(exc, ExitCode.JIRA_SEARCH)
    log = get_logger()
    for warning in report.warnings:
        log.warning("jira issue skipped", **warning.model_dump())
    text = report.model_dump_json(indent=2)
    if out:
        out.write_text(text + "\n", encoding="utf-8")
    typer.echo(text)
