from pathlib import Path

import pytest

from tests.collect.helpers import Collector


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """Return the state dir under test (not created up front)."""
    return tmp_path / "state"


@pytest.fixture
def collector(jira_url: str, state_dir: Path) -> Collector:
    """Collector bound to the fake Jira and `state_dir`."""
    return Collector(jira_url, state_dir)
