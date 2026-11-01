import asyncio
from pathlib import Path

from daily_report.config.models import ContextCommand
from daily_report.context.models import ContextResult, Dropped


class ContextLoader:
    """Reads `--context` files and runs `context.commands`; a failing source never blocks."""

    def __init__(self, commands: list[ContextCommand]) -> None:
        """Run `commands` on every load."""
        self._commands = commands

    async def load(self, files: list[Path]) -> ContextResult:
        """Return the context from `files`, then from each command, in order."""
        result = ContextResult()
        for path in files:
            result += ContextResult.parse(str(path), path.read_text(encoding="utf-8"))
        for command in self._commands:
            result += await self._run(command)
        return result

    async def _run(self, command: ContextCommand) -> ContextResult:
        # shortcut: no timeout on context commands; add one if a slow skill ever stalls compose.
        try:
            process = await asyncio.create_subprocess_exec(
                *command.argv,
                stdout=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            reason = f"command {command.name} failed: {exc.strerror}"
            return ContextResult(dropped=[Dropped(source=command.name, reason=reason)])
        stdout, _ = await process.communicate()
        if process.returncode:
            reason = f"command {command.name} exited {process.returncode}"
            return ContextResult(dropped=[Dropped(source=command.name, reason=reason)])
        return ContextResult.parse(command.name, stdout.decode())
