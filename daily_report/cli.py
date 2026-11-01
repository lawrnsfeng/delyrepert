import os

import typer

from daily_report.collect.commands import collect
from daily_report.compose.commands import compose
from daily_report.config.services import ConfigResolver
from daily_report.effects.commands import DryRunOption
from daily_report.effects.exceptions import InvalidDryRunValueError
from daily_report.effects.services import DryRunEffects, RealEffects
from daily_report.exit_codes import ExitCode
from daily_report.logger import configure_console_logging
from daily_report.notes.commands import note

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)


@app.callback()
def main(ctx: typer.Context, *, dry_run: DryRunOption = False) -> None:
    """Collect my Jira progress and compose the daily standup."""
    configure_console_logging()
    try:
        from_env = ConfigResolver(os.environ).dry_run()
    except InvalidDryRunValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(ExitCode.CONFIG) from exc
    ctx.obj = DryRunEffects() if dry_run or from_env else RealEffects()


app.command()(collect)
app.command()(compose)
app.command()(note)
