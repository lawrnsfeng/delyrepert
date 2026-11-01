import asyncio
import datetime as dt
import os
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from daily_report.collect.commands import AsOfOption, ConfigOption, StateDirOption
from daily_report.collect.exceptions import SearchFailedError
from daily_report.collect.models import Report
from daily_report.collect.services import CollectService
from daily_report.compose.exceptions import InvalidReportError
from daily_report.compose.models import STYLE_NAMES, StyleName
from daily_report.compose.render import Renderer
from daily_report.compose.services import Composed, ComposeService, DraftStore
from daily_report.config.exceptions import ConfigError
from daily_report.config.models import Config
from daily_report.config.services import ConfigResolver
from daily_report.context.services import ContextLoader
from daily_report.effects.commands import DryRunOption, command_effects
from daily_report.effects.services import Effects
from daily_report.exit_codes import ExitCode, fail
from daily_report.jira.exceptions import JiraAuthError
from daily_report.logger import get_logger
from daily_report.notes.services import NotesInbox


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
    )
    return await service.compose(
        report,
        context_files=context_files,
        styles=styles,
        now=dt.datetime.now(tz=dt.UTC),
    )


def _show(composed: Composed, out: Path | None) -> None:
    """Log what the preview must mention, write `--out` files and print the Builder links."""
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
    typer.echo(f"draft {draft.run_id}")
    for name, payload in composed.payloads.items():
        if out:
            (out / f"{name}.json").write_text(payload.as_json, encoding="utf-8")
        typer.echo(payload.builder_url)


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
    out: Annotated[
        Path | None,
        typer.Option(
            file_okay=False,
            help="Write <style>.json payloads here (written even under --dry-run).",
        ),
    ] = None,
    dry_run: DryRunOption = False,
) -> None:
    """Compose the standup draft and print a Block Kit Builder link per style."""
    effects = command_effects(ctx, dry_run=dry_run)
    resolver = ConfigResolver(os.environ)
    try:
        settings = resolver.load(config)
        root = resolver.state_dir(state_dir, settings)
        given = _read_report(report)
    except (ConfigError, InvalidReportError) as exc:
        fail(exc, ExitCode.CONFIG)
    chosen = style or settings.render.style
    try:
        composed = asyncio.run(
            _compose(
                settings,
                resolver=resolver,
                state_dir=root,
                effects=effects,
                report=given,
                as_of=as_of or dt.datetime.now(tz=dt.UTC),
                context_files=context or [],
                styles=[chosen] if chosen else list(STYLE_NAMES),
            ),
        )
    except ConfigError as exc:
        fail(exc, ExitCode.CONFIG)
    except JiraAuthError as exc:
        fail(exc, ExitCode.JIRA_AUTH)
    except SearchFailedError as exc:
        fail(exc, ExitCode.JIRA_SEARCH)
    _show(composed, out)
