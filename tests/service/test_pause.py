import json

import pytest

from tests.service.conftest import Clock, ServiceEnv


def test_ac9_pause_until_a_past_day_exits_2(env: ServiceEnv, clock: Clock) -> None:
    """Pausing until yesterday is refused and writes nothing."""
    clock.now = clock.now.replace(day=1)

    result = env.cli("pause", "--until", "2026-10-31", secrets=False)

    assert result.exit_code == 2
    assert "--until 2026-10-31 is before today (2026-11-01)" in result.output
    assert not (env.state / "paused.json").exists()


def test_ac9_resume_deletes_the_pause(env: ServiceEnv) -> None:
    """Resume removes paused.json, and is a no-op when nothing is paused."""
    assert env.cli("pause", secrets=False).exit_code == 0
    assert (env.state / "paused.json").is_file()

    assert env.cli("resume", secrets=False).exit_code == 0
    assert not (env.state / "paused.json").exists()
    assert env.cli("resume", secrets=False).exit_code == 0


def test_ac10_status_json_before_any_run(env: ServiceEnv) -> None:
    """No pause and no run: both are null."""
    result = env.cli("status", "--json", secrets=False)

    assert json.loads(result.stdout) == {
        "paused": None,
        "last_run": None,
        "timer_next": None,
        "serve_state": "inactive",
    }


def test_ac10_status_json_after_pause_and_run(env: ServiceEnv) -> None:
    """The pause's until and the last run's id and status."""
    env.cli("pause", "--until", "2026-11-10", secrets=False)
    env.cli("run")

    result = env.cli("status", "--json", secrets=False)

    assert json.loads(result.stdout) == {
        "paused": {"until": "2026-11-10"},
        "last_run": {"id": "20261102-090500", "status": "pending"},
        "timer_next": None,
        "serve_state": "inactive",
    }


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((), "not paused\nlast run: none\ntimer_next: none\nserve_state: inactive\n"),
        (
            ("--until", "2026-11-10"),
            "paused until 2026-11-10\nlast run: none\ntimer_next: none\nserve_state: inactive\n",
        ),
        (("--until",), None),
    ],
)
def test_ac10_status_text(env: ServiceEnv, args: tuple[str, ...], expected: str | None) -> None:
    """The plain status says whether runs are paused."""
    if args:
        env.cli("pause", *args, secrets=False)
    if expected is None:  # a bare pause
        env.cli("pause", secrets=False)
        expected = "paused\nlast run: none\ntimer_next: none\nserve_state: inactive\n"

    assert env.cli("status", secrets=False).stdout == expected


def test_ac10_status_text_names_the_last_run(env: ServiceEnv) -> None:
    """The plain status names the last run."""
    env.cli("run")

    assert "last run: 20261102-090500 pending" in env.cli("status", secrets=False).stdout
