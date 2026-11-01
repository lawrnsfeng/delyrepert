import asyncio
import json
import os
from typing import Annotated

import typer

from daily_report.collect.commands import ConfigOption, StateDirOption
from daily_report.compose.exceptions import InvalidDraftError
from daily_report.compose.models import Draft, StyleName, Target
from daily_report.config.exceptions import ConfigError
from daily_report.config.services import ConfigResolver
from daily_report.effects.commands import DryRunOption, command_effects
from daily_report.effects.services import DryRunEffects
from daily_report.exc import TemplatedError
from daily_report.exit_codes import ExitCode, fail
from daily_report.slack.exceptions import (
    AlreadyPostedError,
    BotTokenError,
    NoParentError,
    SlackCallError,
    StyleUnsetError,
)
from daily_report.slack.services import PostService

_EXITS: dict[type[TemplatedError], ExitCode] = {
    InvalidDraftError: ExitCode.CONFIG,
    BotTokenError: ExitCode.CONFIG,
    AlreadyPostedError: ExitCode.NOT_PENDING,
    NoParentError: ExitCode.OWNER_MUST_CHOOSE,
    SlackCallError: ExitCode.SLACK_API,
}


def post(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Draft to post, as printed by compose.")],
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    style: Annotated[
        StyleName | None,
        typer.Option(help="Style to post. Default: render.style."),
    ] = None,
    target: Annotated[
        Target | None,
        typer.Option(help="Where it lands. Default: post.target, else thread."),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Post without asking first.")] = False,
    dry_run: DryRunOption = False,
) -> None:
    """Post a draft to Slack as the owner, exactly once."""
    effects = command_effects(ctx, dry_run=dry_run)
    resolver = ConfigResolver(os.environ)
    try:
        settings = resolver.load(config)
        root = resolver.state_dir(state_dir, settings)
        chosen = style or settings.render.style
        if chosen is None:
            raise StyleUnsetError  # noqa: TRY301 - one exit table below
        service = PostService.from_config(
            settings,
            token=resolver.slack_token(),
            state_dir=root,
            effects=effects,
        )
    except ConfigError as exc:
        fail(exc, ExitCode.CONFIG)
    except StyleUnsetError as exc:
        fail(exc, ExitCode.OWNER_MUST_CHOOSE)

    try:
        permalink = asyncio.run(
            _post(
                service,
                run_id,
                style=chosen,
                target=target or settings.post.target,
                yes=yes,
                preview=isinstance(effects, DryRunEffects),
            ),
        )
    except tuple(_EXITS) as exc:
        fail(exc, _EXITS[type(exc)])
    if permalink:
        typer.echo(permalink)


async def _post(
    service: PostService,
    run_id: str,
    *,
    style: StyleName,
    target: Target,
    yes: bool,
    preview: bool,
    draft: Draft | None = None,
) -> str:
    """Prepare the request, show or confirm it, send it; return the permalink (or "")."""
    request = await service.prepare(run_id, style=style, target=target, draft=draft)
    if preview:
        typer.echo(request.target_line)
        typer.echo(json.dumps(request.body, indent=2, ensure_ascii=False))
    elif not yes:
        typer.echo(request.target_line)
        typer.confirm("Post this?", abort=True)
    posted = await service.send(request)
    return posted.permalink if posted else ""
