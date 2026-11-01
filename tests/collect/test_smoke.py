import asyncio
import os
import shutil
from pathlib import Path

import pytest

from daily_report.collect.models import Report
from tests.collect.helpers import AS_OF

PROJECT = Path(__file__).parents[2]


async def run_installed(*args: str) -> tuple[int, str]:
    """Run `uv run daily-report <args>` as a subprocess; return exit code and stdout."""
    uv = shutil.which("uv")
    assert uv is not None
    process = await asyncio.create_subprocess_exec(
        uv,
        "run",
        "--project",
        str(PROJECT),
        "daily-report",
        *args,
        stdout=asyncio.subprocess.PIPE,
        env={**os.environ, "JIRA_API_TOKEN": "token"},
    )
    stdout, _ = await process.communicate()
    assert process.returncode is not None
    return process.returncode, stdout.decode()


def collect_args(tmp_path: Path, config_file: Path) -> list[str]:
    """Return the smoke run's collect arguments."""
    state = tmp_path / "state"
    return ["collect", "--config", str(config_file), "--state-dir", str(state), "--as-of", AS_OF]


@pytest.mark.asyncio
async def test_ac23_collect_subprocess_smoke(tmp_path: Path, config_file: Path) -> None:
    """The installed command collects from the fake Jira and writes today's snapshot."""
    code, stdout = await run_installed(*collect_args(tmp_path, config_file))

    assert code == 0
    Report.model_validate_json(stdout)
    assert (tmp_path / "state" / "snapshots" / "2026-11-03.json").is_file()


@pytest.mark.asyncio
async def test_ac31_dry_run_subprocess_smoke(tmp_path: Path, config_file: Path) -> None:
    """The installed command under --dry-run prints the report and writes no snapshot."""
    code, stdout = await run_installed("--dry-run", *collect_args(tmp_path, config_file))

    assert code == 0
    Report.model_validate_json(stdout)
    assert not (tmp_path / "state" / "snapshots").exists()
