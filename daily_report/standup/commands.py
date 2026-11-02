import datetime as dt
import os
from pathlib import Path
from typing import Annotated, Literal

import typer

from daily_report.collect.commands import AsOfOption, ConfigOption, StateDirOption
from daily_report.compose.commands import _compose, _loop, _run, _show
from daily_report.compose.models import STYLE_NAMES, Mode, StyleName
from daily_report.compose.paste import render_paste
from daily_report.compose.services import Composed
from daily_report.config.exceptions import (
    ConfigError,
    CookUnsetError,
    OwnerUnsetError,
)
from daily_report.config.models import Config
from daily_report.config.services import ConfigResolver
from daily_report.effects.commands import DryRunOption, command_effects
from daily_report.exit_codes import ExitCode, fail
from daily_report.slack_app.dm import DmService
from daily_report.standup.clipboard import copy_to_clipboard
from daily_report.standup.exceptions import CopyFormatError

Format = Literal["text", "html", "blocks"]

FormatOption = Annotated[
    Format | None,
    typer.Option(
        "--format",
        help=(
            "text: paste-ready text. html: a page to open in a browser and copy from. "
            "blocks: Block Kit JSON and Builder link. "
            "Default: blocks (preview and post) for standup when Slack is configured, else text."
        ),
    ),
]
StyleOption = Annotated[
    StyleName | None,
    typer.Option(help="Block Kit style. Default: render.style, else bluf (standup preview: all)."),
]
ContextOption = Annotated[
    list[Path] | None,
    typer.Option(
        "--context",
        exists=True,
        dir_okay=False,
        help="Context JSON file from another skill (repeatable).",
    ),
]
CookOption = Annotated[
    bool,
    typer.Option("--cook", help="Let cook.command polish the wording (runs even in dry-run)."),
]
CopyOption = Annotated[
    bool,
    typer.Option("--copy", help="Also copy the --format text output to the clipboard."),
]
OutOption = Annotated[
    Path | None,
    typer.Option(
        dir_okay=False,
        help="Write the output to this file instead of stdout (written even under --dry-run).",
    ),
]


def _slack_ready(settings: Config, resolver: ConfigResolver) -> bool:
    """Whether the preview and post loop can run: `[slack]` is set and a user token exists."""
    try:
        resolver.slack_token()
    except ConfigError:
        return False
    return settings.slack is not None


def _emit(
    composed: Composed,
    settings: Config,
    *,
    fmt: Format,
    style: StyleName,
    out: Path | None,
) -> str:
    """Print (or write) the report in `fmt` and return its text."""
    _show(composed, None)
    if fmt == "blocks":
        payload = composed.payloads[style]
        text = payload.as_json
        typer.echo(payload.builder_url(settings.render.builder_team))
    else:
        text = render_paste(composed.draft, fmt, display_name=settings.display_name)
    if out:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out}", err=True)
    else:
        typer.echo(text, nl=False)
    return text


def _report(
    ctx: typer.Context,
    mode: Mode,
    *,
    config: Path | None,
    state_dir: Path | None,
    as_of: dt.datetime | None,
    style: StyleName | None,
    fmt: Format | None,
    context: list[Path] | None,
    cook: bool,
    copy: bool,
    out: Path | None,
    dm: bool,
    dry_run: bool,
) -> None:
    effects = command_effects(ctx, dry_run=dry_run)
    resolver = ConfigResolver(os.environ)
    try:
        settings = resolver.load(config)
        root = resolver.state_dir(state_dir, settings)
        interactive = mode == "team" and _slack_ready(settings, resolver)
        chosen = fmt or ("blocks" if interactive else "text")
        if cook and not settings.cook.command:
            raise CookUnsetError
        if copy and chosen != "text":
            raise CopyFormatError
        dms = None
        if dm:
            bot = resolver.slack_bot_token()
            owner = settings.owner_slack_user_id
            if not owner:
                raise OwnerUnsetError
            dms = DmService.from_config(
                settings,
                bot_token=bot,
                owner=owner,
                state_dir=root,
                effects=effects,
            )
    except ConfigError as exc:
        fail(exc, ExitCode.CONFIG)
    loop_style = style or settings.render.style
    loops = interactive and chosen == "blocks"
    styles: list[StyleName] = (
        [loop_style] if loop_style else list(STYLE_NAMES) if loops else ["bluf"]
    )

    async def flow() -> None:
        composed = await _compose(
            settings,
            resolver=resolver,
            state_dir=root,
            effects=effects,
            report=None,
            as_of=as_of or dt.datetime.now(tz=dt.UTC),
            context_files=context or [],
            styles=styles,
            cook=cook,
            mode=mode,
        )
        if loops:
            _show(composed, None)
            await _loop(
                composed.draft,
                settings,
                resolver=resolver,
                state_dir=root,
                config_path=resolver.config_path(config),
                effects=effects,
                style=loop_style,
                gallery=False,
            ).run()
        else:
            text = _emit(composed, settings, fmt=chosen, style=styles[0], out=out)
            if copy:
                await copy_to_clipboard(text, effects)
        if dms:
            await dms.send_report(composed.payloads[styles[0]])

    _run(flow())


def standup(
    ctx: typer.Context,
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    as_of: AsOfOption = None,
    style: StyleOption = None,
    fmt: FormatOption = None,
    context: ContextOption = None,
    cook: CookOption = False,
    copy: CopyOption = False,
    out: OutOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Report to my team: preview and post it (Slack set up) or print it to paste by hand.

    Collects from Jira and composes the team report. Only a real post consumes pending notes.
    A paste run replaces today's snapshot.
    """
    _report(
        ctx,
        "team",
        config=config,
        state_dir=state_dir,
        as_of=as_of,
        style=style,
        fmt=fmt,
        context=context,
        cook=cook,
        copy=copy,
        out=out,
        dm=False,
        dry_run=dry_run,
    )


def self_report(
    ctx: typer.Context,
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    as_of: AsOfOption = None,
    style: StyleOption = None,
    fmt: FormatOption = None,
    context: ContextOption = None,
    cook: CookOption = False,
    copy: CopyOption = False,
    out: OutOption = None,
    dm: Annotated[
        bool,
        typer.Option("--dm", help="Also DM the report to yourself as the bot (SLACK_BOT_TOKEN)."),
    ] = False,
    dry_run: DryRunOption = False,
) -> None:
    """Report to myself: every detail, never posted, pending notes untouched.

    Collects from Jira and prints the report to paste. Touches Slack only with --dm.
    A run replaces today's snapshot.
    """
    _report(
        ctx,
        "self",
        config=config,
        state_dir=state_dir,
        as_of=as_of,
        style=style,
        fmt=fmt,
        context=context,
        cook=cook,
        copy=copy,
        out=out,
        dm=dm,
        dry_run=dry_run,
    )
