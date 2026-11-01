import getpass
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from daily_report.cli import app
from daily_report.effects.services import RealEffects
from daily_report.service import install_commands
from daily_report.service.installer import (
    Installer,
    LingerOffError,
    SubprocessRunner,
    UnitSources,
)
from tests.service.systemd import FakeSystemd

SCHEDULE = "Mon..Fri *-*-* 09:00:00"
UNITS = ("daily-report.timer", "daily-report-run.service", "daily-report-serve.service")
STARTED = ("daily-report.timer", "daily-report-serve.service")


@dataclass
class Host:
    """A temp host: config, secrets file, a `daily-report` on PATH and a faked systemd."""

    root: Path
    systemd: FakeSystemd
    config: Path
    state_dir: Path
    secrets: Path
    executable: Path

    def cli(self, *args: str) -> Result:
        """Invoke `daily-report <args>` with only the temp bin dir on PATH."""
        return CliRunner().invoke(app, list(args), env={"PATH": str(self.executable.parent)})

    def install(self, *more: str) -> Result:
        """Invoke `<more> service install` with this host's paths."""
        return self.cli(
            *more,
            "service",
            "install",
            "--config",
            str(self.config),
            "--state-dir",
            str(self.state_dir),
        )

    def unit_dir(self) -> Path:
        """Return `<state_dir>/systemd`."""
        return self.state_dir / "systemd"


def write_config(path: Path, *, secrets: Path | None, schedule: str = SCHEDULE) -> None:
    """Write a minimal config file."""
    lines = [f'schedule = "{schedule}"']
    if secrets:
        lines.append(f'secrets_file = "{secrets}"')
    lines += ["[jira]", 'base_url = "https://jira.invalid"', 'email = "owner@example.com"']
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Host:
    """Return a temp host whose commands go to a `FakeSystemd`."""
    systemd = FakeSystemd()
    monkeypatch.setattr(install_commands, "SubprocessRunner", lambda: systemd)
    executable = tmp_path / "bin" / "daily-report"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    secrets = tmp_path / "secrets.env"
    config = tmp_path / "config.toml"
    write_config(config, secrets=secrets)
    return Host(tmp_path, systemd, config, tmp_path / "state", secrets, executable)


def unit_texts(host: Host) -> dict[str, str]:
    """Return the installed unit texts keyed by name."""
    return {path.name: path.read_text(encoding="utf-8") for path in host.unit_dir().iterdir()}


def test_ac1_install_writes_links_and_enables_the_units(host: Host) -> None:
    """Install writes the 3 units with absolute paths, links them and enables two."""
    result = host.install()

    assert result.exit_code == 0, result.output
    units = unit_texts(host)
    assert sorted(units) == sorted(UNITS)
    timer = units["daily-report.timer"]
    assert f"OnCalendar={SCHEDULE}\n" in timer
    assert "Persistent=true\n" in timer
    assert "Unit=daily-report-run.service\n" in timer
    run, serve = units["daily-report-run.service"], units["daily-report-serve.service"]
    assert "Type=oneshot\n" in run
    assert "Restart=on-failure\n" in serve
    for text, command in ((run, "run --scheduled"), (serve, "serve")):
        assert f'ExecStart="{host.executable}" {command} --config "{host.config}"\n' in text
        assert f"EnvironmentFile={host.secrets}\n" in text
    paths = re.findall(r"(?<=[=\"])/[^\s\"]+", "".join(units.values()))
    assert paths
    assert all(path.startswith(str(host.root)) for path in paths)
    assert host.systemd.commands("systemctl") == [
        *(f"systemctl --user link {host.unit_dir() / name}" for name in UNITS),
        *(f"systemctl --user enable --now {name}" for name in STARTED),
    ]


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="systemd-analyze not installed")
async def test_ac2_units_pass_systemd_analyze_verify(host: Host) -> None:
    """The generated units pass `systemd-analyze verify`, even with a space and a `%`."""
    secrets = host.root / "my 100% secrets.env"
    secrets.write_text("", encoding="utf-8")
    sources = UnitSources(host.executable, host.config, host.state_dir, secrets, SCHEDULE)
    units = await Installer(host.systemd, RealEffects()).install(sources, user="owner")

    verified = await SubprocessRunner()(["systemd-analyze", "--user", "verify", *map(str, units)])

    assert verified.returncode == 0, verified.stderr


def test_ac3_linger_off_exits_5_with_the_hint(host: Host) -> None:
    """Linger off exits 5 with the enable-linger hint, writes no unit, calls no systemctl."""
    host.systemd.linger = "no"

    result = host.install()

    assert result.exit_code == 5
    assert f"loginctl enable-linger {getpass.getuser()}" in result.stderr
    assert not host.unit_dir().exists()
    assert host.systemd.commands("systemctl") == []


async def test_ac3_loginctl_failure_counts_as_linger_off(host: Host) -> None:
    """A user loginctl cannot show (no session, no linger) is treated as linger off."""
    host.systemd.linger_code = 1
    sources = UnitSources(host.executable, host.config, host.state_dir, host.secrets, SCHEDULE)

    with pytest.raises(LingerOffError, match="loginctl enable-linger owner"):
        await Installer(host.systemd, RealEffects()).install(sources, user="owner")


def test_ac4_bad_schedule_exits_2_naming_schedule(host: Host) -> None:
    """`systemd-analyze calendar` rejecting `schedule` exits 2 and writes no unit."""
    write_config(host.config, secrets=host.secrets, schedule="Mon 25:00")
    host.systemd.calendar_error = "Failed to parse calendar specification 'Mon 25:00'"

    result = host.install()

    assert result.exit_code == 2
    assert "invalid schedule 'Mon 25:00'" in result.stderr
    assert "Failed to parse calendar specification" in result.stderr
    assert ["systemd-analyze", "calendar", "Mon 25:00"] in host.systemd.calls
    assert not host.unit_dir().exists()


def test_ac5_stop_then_start_both_units_without_rewriting(host: Host) -> None:
    """Stop and start call systemctl for the timer and serve, leaving the units untouched."""
    assert host.install().exit_code == 0
    before = {path: path.stat().st_mtime_ns for path in host.unit_dir().iterdir()}
    host.systemd.calls.clear()

    stopped = host.cli("service", "stop")
    started = host.cli("service", "start")

    assert (stopped.exit_code, started.exit_code) == (0, 0)
    assert host.systemd.commands("systemctl") == [
        *(f"systemctl --user stop {name}" for name in STARTED),
        *(f"systemctl --user start {name}" for name in STARTED),
    ]
    assert {path: path.stat().st_mtime_ns for path in host.unit_dir().iterdir()} == before


def test_ac6_service_status_json_shows_timer_next_and_serve_state(host: Host) -> None:
    """`service status --json` reads NextElapseUSecRealtime and ActiveState."""
    host.systemd.shown = {
        "NextElapseUSecRealtime": "Tue 2026-11-03 09:00:00 GMT",
        "ActiveState": "failed",
    }

    result = host.cli(
        "service",
        "status",
        "--json",
        "--config",
        str(host.config),
        "--state-dir",
        str(host.state_dir),
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "paused": None,
        "last_run": None,
        "timer_next": "Tue 2026-11-03 09:00:00 GMT",
        "serve_state": "failed",
    }


def test_ac6_service_status_before_install(host: Host) -> None:
    """With no units loaded, the timer has no next run and serve is inactive."""
    result = host.cli(
        "service",
        "status",
        "--config",
        str(host.config),
        "--state-dir",
        str(host.state_dir),
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == (
        "not paused\nlast run: none\ntimer_next: none\nserve_state: inactive\n"
    )


@pytest.mark.parametrize("flags", [("--dry-run",), ()])
def test_ac8_dry_run_install_prints_units_and_writes_nothing(
    host: Host,
    flags: tuple[str, ...],
) -> None:
    """Dry-run prints the units AC1 writes and every intent, running only the read probes."""
    env_dry_run = {} if flags else {"DAILY_REPORT_DRY_RUN": "1"}
    args = ["service", "install", "--config", str(host.config), "--state-dir", str(host.state_dir)]
    path = str(host.executable.parent)

    result = CliRunner().invoke(app, [*flags, *args], env={"PATH": path, **env_dry_run})

    assert result.exit_code == 0, result.output
    assert not host.state_dir.exists()
    assert host.systemd.commands("systemctl") == []
    assert [call[0:2] for call in host.systemd.calls] == [
        ["loginctl", "show-user"],
        ["systemd-analyze", "calendar"],
    ]
    folder = host.unit_dir()
    assert result.stderr.splitlines() == [
        *(f"[dry-run] would write {folder / name}" for name in UNITS),
        *(f"[dry-run] would run systemctl --user link {folder / name}" for name in UNITS),
        *(f"[dry-run] would run systemctl --user enable --now {name}" for name in STARTED),
    ]
    assert host.install().exit_code == 0
    for name, text in unit_texts(host).items():
        assert f"# {folder / name}\n{text}" in result.stdout


def test_ac8_dry_run_install_with_linger_off_exits_5(host: Host) -> None:
    """Dry-run checks linger like a real install."""
    host.systemd.linger = "no"

    result = host.install("--dry-run")

    assert result.exit_code == 5
    assert "loginctl enable-linger" in result.stderr
    assert "[dry-run]" not in result.stderr


@pytest.mark.parametrize("verb", ["start", "stop"])
def test_ac8_dry_run_start_stop_print_only_systemctl_lines(host: Host, verb: str) -> None:
    """Dry-run start/stop print the systemctl lines and run nothing."""
    result = host.cli("service", verb, "--dry-run")

    assert result.exit_code == 0, result.output
    assert result.stderr.splitlines() == [
        f"[dry-run] would run systemctl --user {verb} {name}" for name in STARTED
    ]
    assert result.stdout == ""
    assert host.systemd.calls == []


@pytest.mark.parametrize(
    ("args", "failing"),
    [
        (("install",), "systemctl --user link"),
        (("start",), "systemctl --user start"),
        (("stop",), "systemctl --user stop"),
    ],
)
def test_failing_systemctl_exits_5(host: Host, args: tuple[str, ...], failing: str) -> None:
    """A systemctl error is reported with its stderr and exits 5."""
    host.systemd.failing = failing
    extra = ("--config", str(host.config), "--state-dir", str(host.state_dir))

    result = host.cli("service", *args, *(extra if args == ("install",) else ()))

    assert result.exit_code == 5
    assert f"`{failing} " in result.stderr
    assert "Failed to connect to bus" in result.stderr


def test_install_without_secrets_file_exits_2(host: Host) -> None:
    """The units need a secrets file; with none set, install exits 2 naming its sources."""
    write_config(host.config, secrets=None)

    result = host.install()

    assert result.exit_code == 2
    assert "--secrets-file" in result.stderr
    assert host.systemd.calls == []


def test_secrets_file_flag_overrides_config(host: Host) -> None:
    """`--secrets-file` wins over the config key."""
    other = host.root / "other.env"

    result = host.cli(
        "service",
        "install",
        "--config",
        str(host.config),
        "--state-dir",
        str(host.state_dir),
        "--secrets-file",
        str(other),
    )

    assert result.exit_code == 0, result.output
    assert f"EnvironmentFile={other}\n" in unit_texts(host)["daily-report-serve.service"]


def test_install_without_executable_on_path_exits_2(host: Host) -> None:
    """With no `daily-report` on PATH the units cannot name it, so install exits 2."""
    host.executable.unlink()

    result = host.install()

    assert result.exit_code == 2
    assert "`daily-report` is not on PATH" in result.stderr
    assert host.systemd.calls == []


async def test_subprocess_runner_runs_the_command() -> None:
    """The real runner returns the exit code and output."""
    done = await SubprocessRunner()([sys.executable, "-c", "print('hi'); raise SystemExit(3)"])

    assert (done.returncode, done.stdout) == (3, "hi\n")


async def test_subprocess_runner_reports_a_missing_binary_as_127(tmp_path: Path) -> None:
    """A missing binary is exit 127, so a host without systemd reads as linger off."""
    done = await SubprocessRunner()([str(tmp_path / "loginctl")])

    assert done.returncode == 127
