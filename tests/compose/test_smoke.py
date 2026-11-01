import json
from pathlib import Path

import pytest

from daily_report.compose.models import STYLE_NAMES
from tests.collect.test_smoke import run_installed
from tests.compose.helpers import R1_AS_OF, links


@pytest.mark.asyncio
async def test_ac13_dry_run_compose_subprocess_smoke(
    tmp_path: Path,
    config_file: Path,
    state_dir: Path,
) -> None:
    """The installed command composes from the fake Jira under --dry-run and writes --out."""
    out = tmp_path / "out"

    code, stdout = await run_installed(
        "--dry-run",
        "compose",
        "--config",
        str(config_file),
        "--state-dir",
        str(state_dir),
        "--as-of",
        R1_AS_OF,
        "--out",
        str(out),
    )

    assert code == 0
    for style in STYLE_NAMES:
        payload = json.loads((out / f"{style}.json").read_text(encoding="utf-8"))
        assert isinstance(payload["blocks"], list)
    assert len(links(stdout)) == 7
    assert not (state_dir / "drafts").exists()
