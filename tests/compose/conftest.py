from pathlib import Path

import pytest

from daily_report.jira.fake import FakeJira
from tests.compose.helpers import r1_baseline, r1_jira
from tests.slack.conftest import env, fake_slack, slack_url, today  # noqa: F401 - fixtures


@pytest.fixture
def fake_jira() -> FakeJira:
    """Fake Jira serving R1's tickets."""
    return FakeJira(r1_jira())


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """Return a state dir holding R1's Friday snapshot."""
    state = tmp_path / "state"
    r1_baseline(state)
    return state
