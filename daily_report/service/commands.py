import asyncio
import datetime as dt
import json
import os
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from daily_report.collect.commands import ConfigOption, StateDirOption
from daily_report.collect.exceptions import SearchFailedError
from daily_report.collect.services import CollectService
from daily_report.compose.exceptions import InvalidDraftError
from daily_report.compose.models import STYLE_NAMES, Posted, StyleName, Target
from daily_report.compose.render import Renderer
from daily_report.compose.services import Composed, ComposeService, DraftStore
from daily_report.config.exceptions import ConfigError, OwnerUnsetError
from daily_report.config.services import ConfigResolver
from daily_report.context.services import ContextLoader
from daily_report.effects.commands import DryRunOption, command_effects
from daily_report.effects.services import DryRunEffects, Effects
from daily_report.exc import TemplatedError
from daily_report.exit_codes import ExitCode, fail
from daily_report.jira.exceptions import JiraAuthError
from daily_report.notes.services import NotesInbox
from daily_report.service import services
from daily_report.service.exceptions import (
    ExpiredError,
    NotPendingError,
    PastDateError,
    RunInProgressError,
    SecretsError,
)
from daily_report.service.install_commands import read_units_status
from daily_report.service.models import Paused
from daily_report.service.secrets import load_secrets
from daily_report.service.services import (
    ApproveService,
    PauseStore,
    Poster,
    RunLock,
    RunService,
    RunStore,
)
from daily_report.slack.exceptions import (
    AlreadyPostedError,
    BotTokenError,
    NoParentError,
    SlackCallError,
    StyleUnsetError,
)
from daily_report.slack.services import PostService
from daily_report.slack_app.dm import DmService

SecretsFileOption = Annotated[
    Path | None,
    typer.Option(
        "--secrets-file",
        help="0600 env file. Default: $DAILY_REPORT_SECRETS_FILE, then config secrets_file.",
    ),
]

_EXITS: dict[type[TemplatedError], ExitCode] = {
    InvalidDraftError: ExitCode.CONFIG,
    BotTokenError: ExitCode.CONFIG,
    JiraAuthError: ExitCode.JIRA_AUTH,
    SearchFailedError: ExitCode.JIRA_SEARCH,
    RunInProgressError: ExitCode.RUN_IN_PROGRESS,
    NotPendingError: ExitCode.NOT_PENDING,
    ExpiredError: ExitCode.NOT_PENDING,
    AlreadyPostedError: ExitCode.NOT_PENDING,
    SlackCallError: ExitCode.SLACK_API,
    NoParentError: ExitCode.OWNER_MUST_CHOOSE,
    StyleUnsetError: ExitCode.OWNER_MUST_CHOOSE,
}


def _fail(exc: TemplatedError) -> NoReturn:
    if isinstance(exc, SecretsError):
        fail(exc, ExitCode.SECRETS)
    fail(exc, _EXITS.get(type(exc), ExitCode.CONFIG))


class _Env:
    """What every command resolves first: config, state dir and the effects gate."""

    def __init__(
        self,
        ctx: typer.Context,
        config: Path | None,
        state_dir: Path | None,
        *,
        dry_run: bool,
    ) -> None:
        self.effects: Effects = command_effects(ctx, dry_run=dry_run)
        self.resolver = ConfigResolver(os.environ)
        try:
            self.settings = self.resolver.load(config)
            self.root = self.resolver.state_dir(state_dir, self.settings)
        except ConfigError as exc:
            fail(exc, ExitCode.CONFIG)

    def with_secrets(self, secrets_file: Path | None) -> None:
        """Overlay the secrets file's variables on the environment (exit 2 or 6 on failure)."""
        try:
            path = self.resolver.secrets_file(secrets_file, self.settings)
            self.resolver = ConfigResolver({**os.environ, **load_secrets(path)})
        except (ConfigError, SecretsError) as exc:
            _fail(exc)

    def owner(self) -> str:
        """Return `owner_slack_user_id` (exit 2 when unset)."""
        owner = self.settings.owner_slack_user_id
        if not owner:
            fail(OwnerUnsetError(), ExitCode.CONFIG)
        return owner

    @property
    def today(self) -> dt.date:
        """Return today's date in the config zone."""
        return services.utc_now().astimezone(self.settings.zone).date()


def run(
    ctx: typer.Context,
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    secrets_file: SecretsFileOption = None,
    scheduled: Annotated[
        bool,
        typer.Option("--scheduled", help="Triggered by the timer: skipped while paused."),
    ] = False,
    dry_run: DryRunOption = False,
) -> None:
    """Collect and compose now, then hold the draft as pending until it is approved."""
    env = _Env(ctx, config, state_dir, dry_run=dry_run)
    env.with_secrets(secrets_file)
    try:
        service = run_service(env)
    except ConfigError as exc:
        _fail(exc)
    try:
        record = asyncio.run(service.run("scheduled" if scheduled else "cli"))
    except TemplatedError as exc:
        _fail(exc)
    typer.echo(f"{record.run_id} {record.status}")


def run_service(env: _Env) -> RunService:
    """Build the run service: its notifier DMs the owner a pending draft or a failure."""
    owner = env.owner()
    dms = DmService.from_config(
        env.settings,
        bot_token=env.resolver.slack_bot_token(),
        owner=owner,
        state_dir=env.root,
        effects=env.effects,
    )
    return RunService(
        runs=RunStore(env.root, env.effects),
        pauses=PauseStore(env.root, env.effects),
        lock=RunLock(env.root, env.effects),
        compose=lambda now: _compose(env, now),
        zone=env.settings.zone,
        now=services.utc_now,
        notifier=dms,
    )


async def _compose(env: _Env, now: dt.datetime) -> Composed:
    settings = env.settings
    report = await CollectService.collect(
        settings,
        token=env.resolver.jira_token(),
        state_dir=env.root,
        effects=env.effects,
        as_of=now,
    )
    chosen = settings.render.style
    return await ComposeService(
        inbox=NotesInbox(env.root, env.effects),
        store=DraftStore(env.root, env.effects),
        context=ContextLoader(settings.context.commands),
        renderer=Renderer(
            display_name=settings.display_name,
            blocked_animated=settings.emoji.blocked_animated,
        ),
        zone=settings.zone,
    ).compose(
        report,
        context_files=[],
        styles=[chosen] if chosen else list(STYLE_NAMES),
        now=now,
    )


class _PreviewPoster:
    """Dry-run poster: shows the request, then lets the gate skip the send."""

    def __init__(self, service: PostService) -> None:
        self._service = service

    async def post(self, run_id: str, *, style: StyleName, target: Target) -> Posted | None:
        request = await self._service.prepare(run_id, style=style, target=target)
        typer.echo(request.target_line)
        typer.echo(json.dumps(request.body, indent=2, ensure_ascii=False))
        return await self._service.send(request)


def approve(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run to post, as printed by run.")],
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    secrets_file: SecretsFileOption = None,
    style: Annotated[
        StyleName | None,
        typer.Option(help="Style to post. Default: render.style."),
    ] = None,
    target: Annotated[
        Target | None,
        typer.Option(help="Where it lands. Default: post.target."),
    ] = None,
    dry_run: DryRunOption = False,
) -> None:
    """Post a pending run's draft to Slack as the owner."""
    env = _Env(ctx, config, state_dir, dry_run=dry_run)
    env.with_secrets(secrets_file)
    settings = env.settings
    chosen = style or settings.render.style
    try:
        if chosen is None:
            raise StyleUnsetError  # noqa: TRY301 - one exit table
        post = PostService.from_config(
            settings,
            token=env.resolver.slack_token(),
            state_dir=env.root,
            effects=env.effects,
        )
    except (ConfigError, StyleUnsetError) as exc:
        _fail(exc)
    poster: Poster = _PreviewPoster(post) if isinstance(env.effects, DryRunEffects) else post
    service = ApproveService(
        runs=RunStore(env.root, env.effects),
        poster=poster,
        cutoff=settings.approval_cutoff,
        zone=settings.zone,
        now=services.utc_now,
    )
    try:
        record = asyncio.run(
            service.approve(run_id, style=chosen, target=target or settings.post.target),
        )
    except TemplatedError as exc:
        _fail(exc)
    if record.posted:
        typer.echo(record.posted.permalink)


def pause(
    ctx: typer.Context,
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    until: Annotated[
        dt.datetime | None,
        typer.Option(
            formats=["%Y-%m-%d"],
            metavar="YYYY-MM-DD",
            help="First day scheduled runs resume. Default: until `resume`.",
        ),
    ] = None,
    dry_run: DryRunOption = False,
) -> None:
    """Skip scheduled runs until `resume` or --until; manual runs still work."""
    env = _Env(ctx, config, state_dir, dry_run=dry_run)
    day = until.date() if until else None
    if day and day < env.today:
        fail(PastDateError(day.isoformat(), env.today.isoformat()), ExitCode.CONFIG)
    paused = Paused(paused_at=services.utc_now(), until=day, by="cli")
    asyncio.run(PauseStore(env.root, env.effects).pause(paused))


def resume(
    ctx: typer.Context,
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Lift the pause."""
    env = _Env(ctx, config, state_dir, dry_run=dry_run)
    asyncio.run(PauseStore(env.root, env.effects).resume())


def status(
    ctx: typer.Context,
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Show whether runs are paused, how the last run ended, and the timer and listener state."""
    env = _Env(ctx, config, state_dir, dry_run=False)
    paused = PauseStore(env.root, env.effects).current()
    runs = RunStore(env.root, env.effects).all()
    last = runs[-1] if runs else None
    units = asyncio.run(read_units_status(ctx, dry_run=False))
    summary = {
        "paused": {"until": paused.until.isoformat() if paused.until else None}
        if paused
        else None,
        "last_run": {"id": last.run_id, "status": last.status} if last else None,
        **units.model_dump(),
    }
    if as_json:
        typer.echo(json.dumps(summary))
        return
    if paused:
        typer.echo(f"paused until {paused.until}" if paused.until else "paused")
    else:
        typer.echo("not paused")
    typer.echo(f"last run: {last.run_id} {last.status}" if last else "last run: none")
    typer.echo(f"timer_next: {units.timer_next or 'none'}")
    typer.echo(f"serve_state: {units.serve_state}")
