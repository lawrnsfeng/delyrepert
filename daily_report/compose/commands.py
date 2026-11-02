import asyncio
import datetime as dt
import os
from collections.abc import Coroutine
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from daily_report.collect.commands import AsOfOption, ConfigOption, StateDirOption
from daily_report.collect.exceptions import SearchFailedError
from daily_report.collect.models import Report
from daily_report.collect.services import CollectService
from daily_report.compose.edit import apply_edit
from daily_report.compose.exceptions import InvalidDraftError, InvalidEditError, InvalidReportError
from daily_report.compose.models import STYLE_NAMES, Draft, Mode, StyleName
from daily_report.compose.preview import PreviewLoop
from daily_report.compose.render import Renderer
from daily_report.compose.services import Composed, ComposeService, DraftStore
from daily_report.config.exceptions import ConfigError, CookUnsetError
from daily_report.config.models import Config
from daily_report.config.services import ConfigResolver
from daily_report.context.services import ContextLoader
from daily_report.cook.services import CookRunner, CookService
from daily_report.effects.commands import DryRunOption, command_effects
from daily_report.effects.services import Effects
from daily_report.exit_codes import ExitCode, fail
from daily_report.jira.exceptions import JiraAuthError
from daily_report.logger import get_logger
from daily_report.notes.services import NotesInbox
from daily_report.slack.commands import _EXITS
from daily_report.slack.services import PostService


def _read_report(report: typer.FileText | None) -> Report | None:
    if report is None:
        return None
    try:
        return Report.model_validate_json(report.read())
    except ValidationError as exc:
        raise InvalidReportError(report.name, str(exc)) from exc


async def _compose(
    settings: Config,
    *,
    resolver: ConfigResolver,
    state_dir: Path,
    effects: Effects,
    report: Report | None,
    as_of: dt.datetime,
    context_files: list[Path],
    styles: list[StyleName],
    cook: bool,
    mode: Mode,
) -> Composed:
    if report is None:
        report = await CollectService.collect(
            settings,
            token=resolver.jira_token(),
            state_dir=state_dir,
            effects=effects,
            as_of=as_of,
        )
    service = ComposeService(
        inbox=NotesInbox(state_dir, effects),
        store=DraftStore(state_dir, effects),
        context=ContextLoader(settings.context.commands),
        renderer=Renderer(
            display_name=settings.display_name,
            blocked_animated=settings.emoji.blocked_animated,
        ),
        zone=settings.zone,
        cook=(
            CookService(CookRunner(settings.cook.command, settings.cook.timeout_s))
            if cook
            else None
        ),
    )
    return await service.compose(
        report,
        context_files=context_files,
        styles=styles,
        now=dt.datetime.now(tz=dt.UTC),
        mode=mode,
    )


def _show(composed: Composed, out: Path | None) -> None:
    """Log what the preview must mention and write the `--out` files."""
    draft = composed.draft
    log = get_logger()
    for warning in draft.warnings:
        log.warning("jira issue skipped", **warning.model_dump())
    for dropped in draft.dropped:
        log.warning("context dropped", **dropped.model_dump())
    if draft.render_notice:
        log.warning("style replaced", notice=draft.render_notice)
    if out:
        out.mkdir(parents=True, exist_ok=True)
        for name, payload in composed.payloads.items():
            (out / f"{name}.json").write_text(payload.as_json, encoding="utf-8")


def _loop(
    draft: Draft,
    settings: Config,
    *,
    resolver: ConfigResolver,
    state_dir: Path,
    config_path: Path,
    effects: Effects,
    style: StyleName | None,
    gallery: bool,
) -> PreviewLoop:
    return PreviewLoop(
        draft,
        settings=settings,
        state_dir=state_dir,
        config_path=config_path,
        effects=effects,
        service=lambda: PostService.from_config(
            settings,
            token=resolver.slack_token(),
            state_dir=state_dir,
            effects=effects,
        ),
        style=style,
        gallery=gallery,
    )


def _run(flow: Coroutine[None, None, None]) -> None:
    """Run `flow` once, mapping every feature error to its exit code."""
    try:
        asyncio.run(flow)
    except ConfigError as exc:
        fail(exc, ExitCode.CONFIG)
    except JiraAuthError as exc:
        fail(exc, ExitCode.JIRA_AUTH)
    except SearchFailedError as exc:
        fail(exc, ExitCode.JIRA_SEARCH)
    except tuple(_EXITS) as exc:
        fail(exc, _EXITS[type(exc)])


def compose(
    ctx: typer.Context,
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    as_of: AsOfOption = None,
    report: Annotated[
        typer.FileText | None,
        typer.Option(help="Collector report JSON, or - for stdin. Default: collect now."),
    ] = None,
    context: Annotated[
        list[Path] | None,
        typer.Option(
            "--context",
            exists=True,
            dir_okay=False,
            help="Context JSON file from another skill (repeatable).",
        ),
    ] = None,
    style: Annotated[
        StyleName | None,
        typer.Option(help="Render only this style. Default: render.style, else all seven."),
    ] = None,
    mode: Annotated[
        Mode,
        typer.Option(help="team: concise report for your team. self: every detail for you."),
    ] = "team",
    out: Annotated[
        Path | None,
        typer.Option(
            file_okay=False,
            help="Write <style>.json payloads here (written even under --dry-run).",
        ),
    ] = None,
    cook: Annotated[
        bool,
        typer.Option("--cook", help="Let cook.command polish the wording (runs even in dry-run)."),
    ] = False,
    dry_run: DryRunOption = False,
) -> None:
    """Compose the standup draft, preview it and (in a terminal) prompt to post, edit or cancel."""
    effects = command_effects(ctx, dry_run=dry_run)
    resolver = ConfigResolver(os.environ)
    try:
        settings = resolver.load(config)
        root = resolver.state_dir(state_dir, settings)
        given = _read_report(report)
        if cook and not settings.cook.command:
            raise CookUnsetError
    except (ConfigError, InvalidReportError) as exc:
        fail(exc, ExitCode.CONFIG)
    chosen = style or settings.render.style

    async def flow() -> None:
        composed = await _compose(
            settings,
            resolver=resolver,
            state_dir=root,
            effects=effects,
            report=given,
            as_of=as_of or dt.datetime.now(tz=dt.UTC),
            context_files=context or [],
            styles=[chosen] if chosen else list(STYLE_NAMES),
            cook=cook,
            mode=mode,
        )
        _show(composed, out)
        loop = _loop(
            composed.draft,
            settings,
            resolver=resolver,
            state_dir=root,
            config_path=config or Path(os.environ["DAILY_REPORT_CONFIG"]),
            effects=effects,
            style=chosen,
            gallery=False,
        )
        await loop.run()

    _run(flow())


def preview(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Draft to preview, as printed by compose.")],
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    gallery: Annotated[
        bool,
        typer.Option("--gallery", help="Show all seven styles even when render.style is set."),
    ] = False,
    mode: Annotated[
        Mode | None,
        typer.Option(help="Switch the draft to this mode (saved). Default: the draft's own."),
    ] = None,
    dry_run: DryRunOption = False,
) -> None:
    """Preview a saved draft and (in a terminal) prompt to post, edit or cancel."""
    effects = command_effects(ctx, dry_run=dry_run)
    resolver = ConfigResolver(os.environ)
    try:
        settings = resolver.load(config)
        root = resolver.state_dir(state_dir, settings)
        draft = DraftStore(root, effects).load(run_id)
    except (ConfigError, InvalidDraftError) as exc:
        fail(exc, ExitCode.CONFIG)
    loop = _loop(
        draft,
        settings,
        resolver=resolver,
        state_dir=root,
        config_path=config or Path(os.environ["DAILY_REPORT_CONFIG"]),
        effects=effects,
        style=settings.render.style,
        gallery=gallery,
    )

    async def flow() -> None:
        if mode and mode != draft.mode:
            loop.draft = draft.model_copy(update={"mode": mode})
            await DraftStore(root, effects).save(loop.draft)
        await loop.run()

    _run(flow())


draft_app = typer.Typer(help="Work on a saved draft.", no_args_is_help=True)


@draft_app.command("edit")
def draft_edit(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Draft to edit, as printed by compose.")],
    *,
    from_file: Annotated[
        Path,
        typer.Option("--from", exists=True, dir_okay=False, help="Edit YAML to apply."),
    ],
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Apply an edited YAML (headline, summaries, hidden, notes) to a saved draft."""
    effects = command_effects(ctx, dry_run=dry_run)
    resolver = ConfigResolver(os.environ)
    try:
        settings = resolver.load(config)
        store = DraftStore(resolver.state_dir(state_dir, settings), effects)
        edited = apply_edit(store.load(run_id), from_file.read_text(encoding="utf-8"))
    except (ConfigError, InvalidDraftError, InvalidEditError) as exc:
        fail(exc, ExitCode.CONFIG)
    asyncio.run(store.save(edited))
