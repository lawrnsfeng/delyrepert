"""Copy text to the system clipboard through the Effects gate."""

import asyncio
import functools
import shutil

from daily_report.effects.models import Intent
from daily_report.effects.services import Effects
from daily_report.standup.exceptions import ClipboardFailedError, ClipboardUnavailableError

# First one installed wins: clip.exe is WSL's bridge to the Windows clipboard.
PROGRAMS: tuple[tuple[str, ...], ...] = (
    ("clip.exe",),
    ("wl-copy",),
    ("xclip", "-selection", "clipboard"),
    ("pbcopy",),
)


async def run_program(argv: list[str], text: str) -> int:
    """Feed `text` to `argv` on stdin and return its exit code (tests replace it)."""
    process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE)
    await process.communicate(text.encode())
    return process.returncode or 0


def _program() -> list[str]:
    for argv in PROGRAMS:
        found = shutil.which(argv[0])
        if found:
            return [found, *argv[1:]]
    raise ClipboardUnavailableError(", ".join(argv[0] for argv in PROGRAMS))


async def _copy(text: str) -> None:
    argv = _program()
    code = await run_program(argv, text)
    if code:
        raise ClipboardFailedError(argv[0], code)


async def copy_to_clipboard(text: str, effects: Effects) -> None:
    """Copy `text`; under dry-run only say so."""
    await effects.perform(
        Intent("copy", f"{len(text)} characters to the clipboard"),
        functools.partial(_copy, text),
    )
