from pathlib import Path

from daily_report.config.models import Config
from daily_report.config.services import ConfigResolver


def test_ac3_state_dir_resolves_flag_then_env_then_config(tmp_path: Path) -> None:
    """The --state-dir flag beats DAILY_REPORT_STATE_DIR, which beats config state_dir."""
    flag, env, configured = tmp_path / "flag", tmp_path / "env", tmp_path / "config"
    config = Config.model_validate(
        {"state_dir": str(configured), "jira": {"base_url": "https://x", "email": "o@x"}},
    )
    with_env = ConfigResolver({"DAILY_REPORT_STATE_DIR": str(env)})

    assert with_env.state_dir(flag, config) == flag
    assert with_env.state_dir(None, config) == env
    assert ConfigResolver({}).state_dir(None, config) == configured
