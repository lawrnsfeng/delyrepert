from typing import Annotated

import typer

from daily_report.effects.services import DryRunEffects, Effects

DryRunOption = Annotated[
    bool,
    typer.Option(
        "--dry-run",
        help="Report side effects on stderr instead of performing them (or DAILY_REPORT_DRY_RUN).",
    ),
]


def command_effects(ctx: typer.Context, *, dry_run: bool) -> Effects:
    """Return the effects gate for a subcommand, honouring its own `--dry-run`."""
    effects: Effects = DryRunEffects() if dry_run else ctx.obj
    return effects
