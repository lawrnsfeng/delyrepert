import asyncio
import os
import shutil
from pathlib import Path

import pytest

from tests.demo.helpers import GOLDEN, LINK, STYLE_FILES, out_names

PROJECT = Path(__file__).parents[2]


async def run_demo(cwd: Path, home: Path, *args: str) -> tuple[int, str, str]:
    """Run `uv run daily-report <args>` with an empty cwd and HOME; return code, stdout, stderr."""
    uv = shutil.which("uv")
    assert uv is not None
    env = {k: v for k, v in os.environ.items() if not k.startswith("DAILY_REPORT")}
    process = await asyncio.create_subprocess_exec(
        uv,
        "run",
        "--project",
        str(PROJECT),
        "daily-report",
        *args,
        cwd=cwd,
        env={**env, "HOME": str(home)},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode is not None
    return process.returncode, stdout.decode(), stderr.decode()


@pytest.mark.asyncio
async def test_ac3_demo_subprocess_smoke(tmp_path: Path) -> None:
    """`daily-report demo --out` from empty dirs writes the goldens and a temp state dir."""
    cwd, home, out = tmp_path / "cwd", tmp_path / "home", tmp_path / "out"
    cwd.mkdir()
    home.mkdir()

    code, stdout, stderr = await run_demo(cwd, home, "demo", "--out", str(out))

    assert code == 0, stderr
    assert out_names(out) == STYLE_FILES
    for name in STYLE_FILES:
        assert (out / name).read_bytes() == (GOLDEN / name).read_bytes()
    lines = stdout.splitlines()
    assert len([line for line in lines if line.startswith(LINK)]) == 7
    state = Path(next(line for line in lines if line.startswith("state dir: ")).split(": ")[1])
    assert (state / "snapshots/2026-11-03.json").is_file()
    shutil.rmtree(state)
    assert list(cwd.iterdir()) == []
    assert {path.name for path in home.iterdir()} <= {".cache"}  # uv's own cache


@pytest.mark.asyncio
async def test_ac8_demo_dry_run_subprocess_smoke(tmp_path: Path) -> None:
    """`daily-report --dry-run demo` writes only `--out` and reports the snapshot."""
    cwd, home, out = tmp_path / "cwd", tmp_path / "home", tmp_path / "out"
    cwd.mkdir()
    home.mkdir()

    code, stdout, stderr = await run_demo(cwd, home, "--dry-run", "demo", "--out", str(out))

    assert code == 0, stderr
    assert "[dry-run] would write snapshots/2026-11-03.json" in stderr
    assert "state dir:" not in stdout
    assert out_names(out) == STYLE_FILES
    for name in STYLE_FILES:
        assert (out / name).read_bytes() == (GOLDEN / name).read_bytes()
    assert list(cwd.iterdir()) == []
    assert {path.name for path in home.iterdir()} <= {".cache"}  # uv's own cache
