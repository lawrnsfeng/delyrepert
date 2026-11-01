import asyncio
import getpass
import os
import shutil
from pathlib import Path
from typing import Annotated

import typer

from daily_report.collect.commands import ConfigOption, StateDirOption
from daily_report.config.exceptions import ConfigError
from daily_report.config.services import ConfigResolver
from daily_report.effects.commands import DryRunOption, command_effects
from daily_report.exit_codes import ExitCode, fail
from daily_report.service.installer import (
    CommandFailedError,
    ExecutableNotFoundError,
    Installer,
    InvalidScheduleError,
    LingerOffError,
    SubprocessRunner,
    UnitSources,
    UnitsStatus,
)

service_app = typer.Typer(
    no_args_is_help=True,
    help="Run the standup on a systemd user timer and keep the Slack listener up.",
)

SecretsFileOption = Annotated[
    Path | None,
    typer.Option(
        "--secrets-file",
        help="0600 env file with the tokens. Default: $DAILY_REPORT_SECRETS_FILE, then "
        "secrets_file in the config file.",
    ),
]


def _installer(ctx: typer.Context, *, dry_run: bool) -> Installer:
    return Installer(SubprocessRunner(), command_effects(ctx, dry_run=dry_run))


def _sources(
    config: Path | None,
    state_dir: Path | None,
    secrets_file: Path | None,
) -> UnitSources:
    resolver = ConfigResolver(os.environ)
    config_path = resolver.config_path(config)
    settings = resolver.load(config_path)
    executable = shutil.which("daily-report")
    if executable is None:
        raise ExecutableNotFoundError
    return UnitSources(
        executable=Path(executable).absolute(),
        config=config_path.absolute(),
        state_dir=resolver.state_dir(state_dir, settings).absolute(),
        secrets_file=resolver.secrets_file(secrets_file, settings).absolute(),
        schedule=settings.schedule,
    )


@service_app.command()
def install(
    ctx: typer.Context,
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    secrets_file: SecretsFileOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Write the units to <state_dir>/systemd/, link them and start the timer and listener."""
    try:
        sources = _sources(config, state_dir, secrets_file)
    except (ConfigError, ExecutableNotFoundError) as exc:
        fail(exc, ExitCode.CONFIG)
    installer = _installer(ctx, dry_run=dry_run)
    try:
        units = asyncio.run(installer.install(sources, user=getpass.getuser()))
    except InvalidScheduleError as exc:
        fail(exc, ExitCode.CONFIG)
    except (LingerOffError, CommandFailedError) as exc:
        fail(exc, ExitCode.PRECONDITION)
    for path, text in units.items():
        typer.echo(f"# {path}\n{text}")


@service_app.command()
def start(ctx: typer.Context, *, dry_run: DryRunOption = False) -> None:
    """Start the timer and the Slack listener."""
    try:
        asyncio.run(_installer(ctx, dry_run=dry_run).start())
    except CommandFailedError as exc:
        fail(exc, ExitCode.PRECONDITION)


@service_app.command()
def stop(ctx: typer.Context, *, dry_run: DryRunOption = False) -> None:
    """Stop the timer and the Slack listener; the installed units stay."""
    try:
        asyncio.run(_installer(ctx, dry_run=dry_run).stop())
    except CommandFailedError as exc:
        fail(exc, ExitCode.PRECONDITION)


async def read_units_status(ctx: typer.Context, *, dry_run: bool) -> UnitsStatus:
    """Read the timer's next run and the listener's state from systemd."""
    return await _installer(ctx, dry_run=dry_run).status()
