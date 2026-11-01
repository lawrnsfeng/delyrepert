import asyncio
import datetime as dt
import os
from typing import Annotated

import typer

from daily_report.collect.commands import ConfigOption, StateDirOption
from daily_report.config.exceptions import ConfigError
from daily_report.config.services import ConfigResolver
from daily_report.effects.commands import DryRunOption, command_effects
from daily_report.exit_codes import ExitCode, fail
from daily_report.notes.exceptions import NotesError, NoteUsageError
from daily_report.notes.models import NoteGroup
from daily_report.notes.services import NotesInbox


def note(
    ctx: typer.Context,
    args: Annotated[
        list[str],
        typer.Argument(metavar="TEXT | list | rm N", show_default=False),
    ],
    *,
    group: Annotated[
        NoteGroup,
        typer.Option(help="Where the note shows in the standup."),
    ] = "today",
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Add a note to the next standup (`note TEXT`), list them (`note list`) or drop one."""
    effects = command_effects(ctx, dry_run=dry_run)
    resolver = ConfigResolver(os.environ)
    try:
        inbox = NotesInbox(resolver.state_dir(state_dir, resolver.load(config)), effects)
    except ConfigError as exc:
        fail(exc, ExitCode.CONFIG)
    try:
        match args:
            case ["list"]:
                for number, pending in enumerate(inbox.pending(), 1):
                    typer.echo(f"{number}. [{pending.group}] {pending.text}")
            case ["rm", number] if number.isdigit():
                asyncio.run(inbox.remove(int(number)))
            case [text]:
                asyncio.run(inbox.add(text, group=group, at=dt.datetime.now(tz=dt.UTC)))
            case _:
                raise NoteUsageError(" ".join(args))
    except NotesError as exc:
        fail(exc, ExitCode.CONFIG)
