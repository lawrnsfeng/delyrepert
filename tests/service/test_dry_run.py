import fcntl
import hashlib
from pathlib import Path

from daily_report.slack.fake import FakeSlack
from tests.service.conftest import RUN_ID, Clock, ServiceEnv


def tree(state: Path) -> dict[str, str]:
    """Hash every file under `state`."""
    return {
        str(path.relative_to(state)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(state.rglob("*"))
        if path.is_file()
    }


def test_ac12_dry_run_service_commands_change_nothing(
    env: ServiceEnv,
    clock: Clock,
    fake_slack: FakeSlack,
) -> None:
    """Dry-run run, approve, pause and resume print their effects and leave the tree alone."""
    assert env.cli("run").exit_code == 0
    clock.now = clock.now.replace(minute=10)
    before = tree(env.state)

    run = env.cli("run", "--scheduled", dry_run=True)

    assert run.exit_code == 0, run.output
    for line in (
        "[dry-run] would acquire run.lock",
        "[dry-run] would write runs/20261102-091000.json",
        "drafts/20261102-091000.json",
    ):
        assert line in run.stderr
    assert tree(env.state) == before

    with (env.state / "run.lock").open("w", encoding="utf-8") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        held.write(RUN_ID)
        held.flush()
        before = tree(env.state)
        assert env.cli("run", "--scheduled", dry_run=True).exit_code == 7
    assert tree(env.state) == before

    approve = env.cli("approve", RUN_ID, "--style", "ytb", "--target", "channel", dry_run=True)

    assert approve.exit_code == 0, approve.output
    assert '"channel": "C0STANDUP"' in approve.stdout
    assert fake_slack.called("chat.postMessage") == []
    assert tree(env.state) == before

    pause = env.cli("pause", "--until", "2026-11-10", dry_run=True, secrets=False)
    assert "would write paused.json" in pause.stderr
    assert not (env.state / "paused.json").exists()
    (env.state / "paused.json").write_text(
        '{"paused_at": "2026-11-02T09:00:00Z", "by": "cli"}',
        encoding="utf-8",
    )
    before = tree(env.state)
    resume = env.cli("resume", dry_run=True, secrets=False)
    assert "would delete paused.json" in resume.stderr
    assert tree(env.state) == before
