"""Generate the daily-report systemd user units and drive `systemctl --user` on them."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from daily_report.effects.models import Intent
from daily_report.effects.services import Effects
from daily_report.exc import TemplatedError

TIMER = "daily-report.timer"
RUN_SERVICE = "daily-report-run.service"
SERVE_SERVICE = "daily-report-serve.service"
STARTED_UNITS = (TIMER, SERVE_SERVICE)


class InstallerError(TemplatedError):
    """Base for `service` errors."""


@dataclass
class ExecutableNotFoundError(InstallerError):
    """The units cannot name an absolute `daily-report` executable."""

    TEMPLATE = "`daily-report` is not on PATH, so the units cannot run it"


@dataclass
class InvalidScheduleError(InstallerError):
    """`systemd-analyze calendar` rejects the configured `schedule`."""

    TEMPLATE = "invalid schedule {schedule!r} in the config file: {reason}"
    schedule: str
    reason: str


@dataclass
class LingerOffError(InstallerError):
    """User units would stop at logout because linger is off."""

    TEMPLATE = (
        "linger is off for {user}, so the units would stop at logout: "
        "run `loginctl enable-linger {user}` and install again"
    )
    user: str


@dataclass
class CommandFailedError(InstallerError):
    """A `systemctl` call exited non-zero."""

    TEMPLATE = "`{command}` failed ({code}): {reason}"
    command: str
    code: int
    reason: str


@dataclass(frozen=True)
class Completed:
    """The outcome of one external command."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner(Protocol):
    """Runs an external command and returns its outcome."""

    async def __call__(self, argv: Sequence[str]) -> Completed:
        """Run `argv` to completion."""


class SubprocessRunner:
    """Runs commands as real subprocesses."""

    async def __call__(self, argv: Sequence[str]) -> Completed:
        """Run `argv`; a missing binary is exit 127, like a shell."""
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return Completed(127, stderr=str(exc))
        stdout, stderr = await process.communicate()
        return Completed(
            process.returncode or 0,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )


class UnitsStatus(BaseModel):
    """What `service status` adds to the run status."""

    timer_next: str | None
    serve_state: str


@dataclass(frozen=True)
class UnitSources:
    """Absolute paths and the schedule the units are generated from."""

    executable: Path
    config: Path
    state_dir: Path
    secrets_file: Path
    schedule: str


def _quoted(value: object) -> str:
    """Quote `value` for a unit file, escaping systemd's `%` specifiers."""
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{text}"'


def _unit_body(sources: UnitSources, command: str) -> str:
    executable = _quoted(sources.executable).replace("$", "$$")
    config = _quoted(sources.config).replace("$", "$$")
    state_dir = _quoted(f"DAILY_REPORT_STATE_DIR={sources.state_dir}")
    secrets = _quoted(f"DAILY_REPORT_SECRETS_FILE={sources.secrets_file}")
    return (
        f"EnvironmentFile={str(sources.secrets_file).replace('%', '%%')}\n"
        f"Environment={state_dir} {secrets}\n"
        f"ExecStart={executable} {command} --config {config}\n"
    )


def render_units(sources: UnitSources) -> dict[str, str]:
    """Return the text of each unit, keyed by unit name."""
    return {
        TIMER: (
            "[Unit]\n"
            "Description=daily-report: draft the standup on schedule\n\n"
            "[Timer]\n"
            f"OnCalendar={sources.schedule}\n"
            "Persistent=true\n"
            f"Unit={RUN_SERVICE}\n\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        ),
        RUN_SERVICE: (
            "[Unit]\n"
            "Description=daily-report: draft the standup and hold it for approval\n\n"
            "[Service]\n"
            "Type=oneshot\n" + _unit_body(sources, "run --scheduled")
        ),
        SERVE_SERVICE: (
            "[Unit]\n"
            "Description=daily-report: Slack listener for approvals and /standup\n\n"
            "[Service]\n" + _unit_body(sources, "serve") + "Restart=on-failure\n\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        ),
    }


class Installer:
    """Installs, starts, stops and inspects the daily-report user units."""

    def __init__(self, runner: Runner, effects: Effects) -> None:
        """Run read-only probes through `runner`; route every mutation through `effects`."""
        self._runner = runner
        self._effects = effects

    async def install(self, sources: UnitSources, *, user: str) -> dict[Path, str]:
        """Write the units to `<state_dir>/systemd/`, link them and enable the timer and serve.

        Return the unit texts keyed by path. Raise before any write when linger is off or
        the schedule is invalid.
        """
        await self._check_linger(user)
        await self._check_schedule(sources.schedule)
        folder = sources.state_dir / "systemd"
        units = {folder / name: text for name, text in render_units(sources).items()}
        for path, text in units.items():
            await self._write(path, text)
        for path in units:
            await self._systemctl("link", str(path))
        for name in STARTED_UNITS:
            await self._systemctl("enable", "--now", name)
        return units

    async def start(self) -> None:
        """Start the timer and the serve unit."""
        for name in STARTED_UNITS:
            await self._systemctl("start", name)

    async def stop(self) -> None:
        """Stop the timer and the serve unit."""
        for name in STARTED_UNITS:
            await self._systemctl("stop", name)

    async def status(self) -> UnitsStatus:
        """Read the timer's next elapse and the serve unit's state."""
        timer_next = await self._show(TIMER, "NextElapseUSecRealtime")
        serve_state = await self._show(SERVE_SERVICE, "ActiveState")
        return UnitsStatus(timer_next=timer_next or None, serve_state=serve_state or "inactive")

    async def _check_linger(self, user: str) -> None:
        # loginctl fails for a user with no session and no linger; both mean "off".
        shown = await self._runner(["loginctl", "show-user", user, "--property=Linger", "--value"])
        if shown.returncode or shown.stdout.strip() != "yes":
            raise LingerOffError(user)

    async def _check_schedule(self, schedule: str) -> None:
        checked = await self._runner(["systemd-analyze", "calendar", schedule])
        if checked.returncode:
            raise InvalidScheduleError(schedule, checked.stderr.strip())

    async def _show(self, unit: str, prop: str) -> str:
        shown = await self._runner(
            ["systemctl", "--user", "show", unit, f"--property={prop}", "--value"],
        )
        return shown.stdout.strip()

    async def _write(self, path: Path, text: str) -> None:
        def blocking_write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        async def write() -> None:
            await asyncio.to_thread(blocking_write)

        await self._effects.perform(Intent("write", str(path)), write)

    async def _systemctl(self, *args: str) -> None:
        argv = ["systemctl", "--user", *args]

        async def run() -> None:
            done = await self._runner(argv)
            if done.returncode:
                raise CommandFailedError(" ".join(argv), done.returncode, done.stderr.strip())

        await self._effects.perform(Intent("run", " ".join(argv)), run)
