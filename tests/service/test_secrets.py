import json

import pytest

from daily_report.slack.fake import FakeSlack
from tests.service.conftest import RUN_ID, SECRETS, ServiceEnv


def test_ac1_run_without_state_dir_names_the_three_sources(env: ServiceEnv) -> None:
    """No state dir source: exit 2, nothing written."""
    result = env.cli("run", state=False)

    assert result.exit_code == 2
    for source in ("--state-dir", "DAILY_REPORT_STATE_DIR", "state_dir"):
        assert source in result.output
    assert not (env.state / "runs").exists()


def test_ac1_run_without_secrets_file_names_the_three_sources(env: ServiceEnv) -> None:
    """No secrets file source: exit 2, nothing written."""
    result = env.cli("run", secrets=False)

    assert result.exit_code == 2
    for source in ("--secrets-file", "DAILY_REPORT_SECRETS_FILE", "secrets_file"):
        assert source in result.output
    assert not (env.state / "runs").exists()


def test_ac1_secrets_file_resolves_from_env_then_config(
    env: ServiceEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env var and the config key each name the secrets file."""
    monkeypatch.setenv("DAILY_REPORT_SECRETS_FILE", str(env.secrets))
    assert env.cli("run", secrets=False).exit_code == 0

    monkeypatch.delenv("DAILY_REPORT_SECRETS_FILE")
    env.config.write_text(
        f'secrets_file = "{env.secrets}"\n' + env.config.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert env.cli("run", "--scheduled", secrets=False).exit_code in {0, 7}


def test_ac2_loose_mode_exits_6(env: ServiceEnv) -> None:
    """A 0644 secrets file is refused."""
    env.secrets.chmod(0o644)

    result = env.cli("run")

    assert result.exit_code == 6
    assert f"secrets file {env.secrets} must be 0600" in result.output
    assert not (env.state / "runs").exists()


def test_ac2_missing_variable_names_only_that_variable(env: ServiceEnv) -> None:
    """A 0600 file without JIRA_API_TOKEN exits 6 naming only it."""
    lines = [f"{name}={value}" for name, value in SECRETS.items() if name != "JIRA_API_TOKEN"]
    env.secrets.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = env.cli("run")

    assert result.exit_code == 6
    assert "JIRA_API_TOKEN" in result.output
    assert "SLACK" not in result.output


def test_ac2_unreadable_secrets_file_exits_6(env: ServiceEnv) -> None:
    """A missing secrets file exits 6 too, for approve as well."""
    env.secrets.unlink()

    assert env.cli("run").exit_code == 6
    assert env.cli("approve", RUN_ID).exit_code == 6


def test_ac3_no_secret_value_reaches_output(env: ServiceEnv, fake_slack: FakeSlack) -> None:
    """After run and approve, no secret value shows in stdout or stderr."""
    outputs = [
        env.cli("run", "--scheduled"),
        env.cli("approve", RUN_ID, "--style", "ytb", "--target", "channel"),
    ]

    assert [result.exit_code for result in outputs] == [0, 0]
    shown = "".join(result.output for result in outputs) + json.dumps(env.record())
    assert not [value for value in SECRETS.values() if value in shown]
    dm, request = fake_slack.called("chat.postMessage")
    assert dm.authorization == "Bearer s3cr3t-bot"
    assert request.authorization == "Bearer s3cr3t-user"
