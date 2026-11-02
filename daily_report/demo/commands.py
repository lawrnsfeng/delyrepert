import asyncio
import datetime as dt
import os
from importlib import resources
from pathlib import Path
from typing import Annotated

import typer
from slack_sdk.web.async_client import AsyncWebClient

from daily_report.collect.commands import AsOfOption, ConfigOption
from daily_report.compose.models import STYLE_NAMES, Mode, StyleName
from daily_report.compose.render import Renderer
from daily_report.config.exceptions import ConfigError, ConfigFileUnsetError
from daily_report.config.models import Config, CookConfig, DemoConfig
from daily_report.config.services import ConfigResolver
from daily_report.demo.exceptions import (
    CookUnsetError,
    DemoFixturesError,
    DemoKeyUnsetError,
    WorkspaceMismatchError,
)
from daily_report.demo.services import DEMO_AS_OF, DemoComposed, DemoPoster, DemoService
from daily_report.effects.commands import DryRunOption, command_effects
from daily_report.effects.services import DryRunEffects, Effects
from daily_report.exit_codes import ExitCode, fail
from daily_report.retry import RetryPolicy
from daily_report.slack.exceptions import SlackCallError
from daily_report.slack.services import SlackClient

_SLACK_API = "https://slack.com/api/"


def _optional_config(resolver: ConfigResolver, flag: Path | None) -> Config | None:
    """Load the config file when one is named; the demo runs without any."""
    try:
        return resolver.load(flag)
    except ConfigFileUnsetError:
        return None


def demo(
    ctx: typer.Context,
    *,
    out: Annotated[
        Path | None,
        typer.Option(
            file_okay=False,
            help="Write <mode>/<style>.json payloads here (even in dry-run).",
        ),
    ] = None,
    mode: Annotated[
        Mode | None,
        typer.Option(help="Render only this mode. Default: both team and self."),
    ] = None,
    style: Annotated[
        StyleName | None,
        typer.Option(help="Render and post only this style."),
    ] = None,
    as_of: AsOfOption = None,
    fixtures_dir: Annotated[
        Path | None,
        typer.Option(
            help="Fixtures dir. Default: $DAILY_REPORT_DEMO_FIXTURES, then the built-in ones.",
        ),
    ] = None,
    state_dir: Annotated[
        Path | None,
        typer.Option(
            help="Demo state dir. Default: $DAILY_REPORT_DEMO_STATE_DIR, demo.state_dir, "
            "else a fresh temp dir.",
        ),
    ] = None,
    config: ConfigOption = None,
    cook: Annotated[
        bool,
        typer.Option("--cook", help="Polish the wording with cook.command."),
    ] = False,
    broadcast: Annotated[
        bool,
        typer.Option(help="Also show every reply in the channel."),
    ] = False,
    dry_run: DryRunOption = False,
) -> None:
    """Run the standup flow from mock Jira fixtures; post to the test Slack with a token."""
    effects = command_effects(ctx, dry_run=dry_run)
    resolver = ConfigResolver(os.environ)
    try:
        settings = _optional_config(resolver, config)
        token = resolver.demo_token()
        demo_config = settings.demo if settings else DemoConfig()
        if cook and not (settings and settings.cook.command):
            raise CookUnsetError
        if token:
            for key in ("channel", "slack_team_id"):
                if not getattr(demo_config, key):
                    raise DemoKeyUnsetError(key)
        override = resolver.demo_fixtures(fixtures_dir)
        if override and not override.is_dir():
            raise DemoFixturesError(str(override))
    except ConfigError as exc:
        fail(exc, ExitCode.CONFIG)
    poster = _poster(token, demo_config, settings, effects, as_of or DEMO_AS_OF) if token else None
    with resources.as_file(resources.files("daily_report.demo") / "fixtures") as built_in:
        service = DemoService(
            fixtures=override or built_in,
            effects=effects,
            renderer=Renderer(
                display_name=settings.display_name if settings else None,
                blocked_animated=None,
            ),
        )
        try:
            asyncio.run(
                _run(
                    service,
                    poster,
                    modes=[mode] if mode else ["team", "self"],
                    styles=[style] if style else list(STYLE_NAMES),
                    out=out,
                    builder_team=settings.render.builder_team if settings else None,
                    broadcast=broadcast,
                    state_dir=resolver.demo_state_dir(state_dir, settings),
                    as_of=as_of or DEMO_AS_OF,
                    cook_config=settings.cook if cook and settings else None,
                    confirm=not isinstance(effects, DryRunEffects),
                ),
            )
        except ConfigError as exc:
            fail(exc, ExitCode.CONFIG)
        except WorkspaceMismatchError as exc:
            fail(exc, ExitCode.PRECONDITION)
        except SlackCallError as exc:
            fail(exc, ExitCode.SLACK_API)


def _poster(
    token: str,
    demo_config: DemoConfig,
    settings: Config | None,
    effects: Effects,
    as_of: dt.datetime,
) -> DemoPoster:
    api_url = settings.slack.api_url if settings and settings.slack else _SLACK_API
    return DemoPoster(
        SlackClient(AsyncWebClient(token=token, base_url=api_url), RetryPolicy()),
        team_id=str(demo_config.slack_team_id),
        channel=str(demo_config.channel),
        parent_text=demo_config.parent_text,
        day=as_of.astimezone(dt.UTC).date(),
        effects=effects,
    )


def _show(composed: DemoComposed, out: Path | None, builder_team: str | None) -> None:
    """Write the `--out` files and print one Block Kit Builder link per mode and style."""
    for (mode, name), payload in composed.payloads.items():
        if out:
            (out / mode).mkdir(parents=True, exist_ok=True)
            (out / mode / f"{name}.json").write_text(payload.as_json, encoding="utf-8")
        typer.echo(f"{mode} {name}", err=True)
        typer.echo(payload.builder_url(builder_team))


async def _run(
    service: DemoService,
    poster: DemoPoster | None,
    *,
    modes: list[Mode],
    styles: list[StyleName],
    out: Path | None,
    builder_team: str | None,
    broadcast: bool,
    state_dir: Path | None,
    as_of: dt.datetime,
    cook_config: CookConfig | None,
    confirm: bool,
) -> None:
    """Check the workspace, build and show the styles, then post them when a token is set."""
    if poster:
        await poster.check()
    composed, seeded = await service.build(
        as_of=as_of,
        modes=modes,
        styles=styles,
        state_dir=state_dir,
        cook_config=cook_config,
    )
    if seeded:
        typer.echo(f"state dir: {seeded}")
    _show(composed, out, builder_team)
    if poster is None:
        return
    question = f"Post {len(composed.payloads)} replies to #{poster.channel}?"
    if confirm and not typer.confirm(question):
        return
    await poster.post(composed.payloads, broadcast=broadcast)
