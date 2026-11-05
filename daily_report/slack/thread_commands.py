import asyncio
import os
from collections.abc import Coroutine
from pathlib import Path
from typing import Annotated

import typer

from daily_report.collect.commands import ConfigOption, StateDirOption
from daily_report.config.exceptions import ConfigError, SlackUnsetError
from daily_report.config.services import ConfigResolver
from daily_report.effects.commands import DryRunOption, command_effects
from daily_report.exit_codes import ExitCode, fail
from daily_report.slack.commands import _EXITS, ThreadOption
from daily_report.slack.services import ThreadService

thread_app = typer.Typer(help="Pick the thread the next post replies in.", no_args_is_help=True)


def _service(
    ctx: typer.Context,
    config: Path | None,
    state_dir: Path | None,
    *,
    dry_run: bool,
    token: bool,
) -> ThreadService:
    """Build the service; `token=False` for work that makes no Slack call."""
    effects = command_effects(ctx, dry_run=dry_run)
    resolver = ConfigResolver(os.environ)
    settings = resolver.load(config)
    return ThreadService.from_config(
        settings,
        token=resolver.slack_token() if token else None,
        state_dir=resolver.state_dir(state_dir, settings),
        effects=effects,
    )


def _run(work: Coroutine[None, None, None]) -> None:
    """Run `work`, mapping every known failure to its exit code."""
    try:
        asyncio.run(work)
    except ConfigError as exc:
        fail(exc, ExitCode.CONFIG)
    except tuple(_EXITS) as exc:
        fail(exc, _EXITS[type(exc)])


@thread_app.command("list")
def list_threads(*, config: ConfigOption = None) -> None:
    """List the thread patterns: name, channel, lookback and match rules."""
    try:
        slack = ConfigResolver(os.environ).load(config).slack
        if slack is None:
            raise SlackUnsetError
    except ConfigError as exc:
        fail(exc, ExitCode.CONFIG)
    for name in ["default", *slack.threads]:
        spec = slack.spec(name)
        summary = " ".join(f"{key}={value}" for key, value in spec.match.rules().items())
        typer.echo(f"{spec.name}\t{spec.channel}\t{spec.lookback_days}d\t{summary}")


@thread_app.command()
def show(
    ctx: typer.Context,
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    thread: ThreadOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Show the thread the next post replies in."""

    async def work() -> None:
        service = _service(ctx, config, state_dir, dry_run=dry_run, token=True)
        resolved = await service.show(thread)
        located = resolved.located
        typer.echo(f"source: {located.source}")
        typer.echo(f"channel: {located.channel}")
        typer.echo(f"ts: {located.ts}")
        typer.echo(f"permalink: {resolved.permalink}")
        if located.message:
            typer.echo(f"author: {located.message.author}")
            typer.echo(f"text: {located.message.first_line}")

    _run(work())


@thread_app.command()
def pin(
    ctx: typer.Context,
    value: Annotated[
        str,
        typer.Argument(help="A Slack message link, or a thread name to pin what it finds now."),
    ],
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Pin the thread the next post replies in, until that post or midnight."""
    link = value.strip().startswith("https://")

    async def work() -> None:
        service = _service(ctx, config, state_dir, dry_run=dry_run, token=not link)
        resolved = await (service.pin_permalink(value) if link else service.pin_named(value))
        typer.echo(f"pinned {resolved.permalink}")

    _run(work())


@thread_app.command()
def clear(
    ctx: typer.Context,
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Remove the thread pin."""

    async def work() -> None:
        service = _service(ctx, config, state_dir, dry_run=dry_run, token=False)
        typer.echo("pin cleared" if await service.clear_pin() else "no pin set")

    _run(work())
