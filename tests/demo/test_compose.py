import json
import shutil
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from daily_report.cli import app
from daily_report.slack.fake import FakeSlack
from tests.demo.helpers import (
    FIXTURES,
    GOLDEN,
    LINK,
    STYLE_FILES,
    DemoEnv,
    out_names,
)


def assert_goldens(out: Path) -> None:
    """Every style file equals its golden byte for byte."""
    assert out_names(out) == STYLE_FILES
    for name in STYLE_FILES:
        assert (out / name).read_bytes() == (GOLDEN / name).read_bytes()


def test_ac3_demo_without_token_writes_the_golden_styles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No token: seven style files equal the goldens, seven links, a fresh state dir."""
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    env = DemoEnv(tmp_path)

    result = env.run(config=False)

    assert result.exit_code == 0, result.output
    assert_goldens(env.out)
    assert len([line for line in result.stdout.splitlines() if line.startswith(LINK)]) == 7
    (state,) = scratch.iterdir()
    assert f"state dir: {state}" in result.stdout.splitlines()
    assert (state / "snapshots/2026-11-03.json").is_file()
    assert len(list((state / "drafts").iterdir())) == 1
    for name in STYLE_FILES:
        text = (env.out / name).read_text(encoding="utf-8")
        assert "📝" in text
        assert "demo-skill" in text
        assert "DEMO-103" in text
        assert "⛔" in text


def test_ac3_style_writes_only_that_file(tmp_path: Path) -> None:
    """`--style ytb` writes ytb.json alone."""
    env = DemoEnv(tmp_path)

    result = env.run("--style", "ytb", config=False)

    assert result.exit_code == 0, result.output
    assert out_names(env.out) == ["ytb.json"]
    assert (env.out / "ytb.json").read_bytes() == (GOLDEN / "ytb.json").read_bytes()


def test_ac3_no_slack_call_without_a_token(
    tmp_path: Path,
    slack_url: str,
    fake_slack: FakeSlack,
) -> None:
    """A config naming a live workspace still makes no Slack call without a token."""
    env = DemoEnv(tmp_path, slack_url)
    env.write_live_config()

    result = env.run()

    assert result.exit_code == 0, result.output
    assert fake_slack.requests == []


@pytest.mark.parametrize("dry_run", [True, False])
def test_ac8_state_dir_and_fixtures_overrides(tmp_path: Path, *, dry_run: bool) -> None:
    """`--state-dir` and `--fixtures-dir` are honoured; dry-run touches neither."""
    fixtures = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, fixtures)
    note = fixtures / "notes/pending.jsonl"
    note.write_text(note.read_text("utf-8").replace("dentist", "garden"), encoding="utf-8")
    state = tmp_path / "state"
    env = DemoEnv(tmp_path)

    result = env.run(
        "--state-dir",
        str(state),
        "--fixtures-dir",
        str(fixtures),
        "--style",
        "bluf",
        dry_run=dry_run,
        config=False,
    )

    assert result.exit_code == 0, result.output
    assert "garden" in (env.out / "bluf.json").read_text(encoding="utf-8")
    assert state.exists() is not dry_run
    assert (state / "snapshots/2026-11-03.json").is_file() is not dry_run


def test_ac8_fixtures_and_state_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env vars point at fixtures and a state dir; a missing fixtures dir exits 2."""
    state = tmp_path / "state"
    monkeypatch.setenv("DAILY_REPORT_DEMO_FIXTURES", str(FIXTURES))
    monkeypatch.setenv("DAILY_REPORT_DEMO_STATE_DIR", str(state))
    env = DemoEnv(tmp_path)

    assert env.run(config=False).exit_code == 0
    assert (state / "snapshots/2026-11-03.json").is_file()

    monkeypatch.setenv("DAILY_REPORT_DEMO_FIXTURES", str(tmp_path / "missing"))
    result = env.run(config=False)
    assert result.exit_code == 2
    assert "demo fixtures dir" in result.stderr


def test_ac8_state_dir_from_config(tmp_path: Path) -> None:
    """`demo.state_dir` in the config is the state dir."""
    state = tmp_path / "from-config"
    env = DemoEnv(tmp_path)
    env.write_config(f'[demo]\nstate_dir = "{state}"\n')

    result = env.run("--style", "bluf")

    assert result.exit_code == 0, result.output
    assert (state / "snapshots/2026-11-03.json").is_file()


def test_ac8_dry_run_writes_only_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run: no temp state dir, only `--out`, stderr names the snapshot."""
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    env = DemoEnv(tmp_path)

    result = env.run(dry_run=True, config=False)

    assert result.exit_code == 0, result.output
    assert "[dry-run] would write snapshots/2026-11-03.json" in result.stderr
    assert list(scratch.iterdir()) == []
    assert "state dir:" not in result.stdout
    assert_goldens(env.out)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out", "tmp"]


def test_ac8_config_display_name_reaches_the_header(tmp_path: Path) -> None:
    """A config's display_name prefixes the headline."""
    env = DemoEnv(tmp_path)
    env.write_config()
    text = env.config.read_text("utf-8").replace("[jira]", 'display_name = "Dana"\n[jira]', 1)
    env.config.write_text(text, encoding="utf-8")

    result = env.run("--style", "bluf")

    assert result.exit_code == 0, result.output
    header = json.loads((env.out / "bluf.json").read_text("utf-8"))["blocks"][0]
    assert header["text"]["text"] == "Dana · Standup · Tue 3 Nov"


def test_ac3_without_out_only_prints_the_links(tmp_path: Path) -> None:
    """No `--out`: links on stdout and no payload files anywhere."""
    result = CliRunner().invoke(
        app,
        ["--dry-run", "demo", "--style", "bluf", "--state-dir", str(tmp_path / "state")],
    )

    assert result.exit_code == 0, result.output
    assert [line[: len(LINK)] for line in result.stdout.splitlines()] == [LINK]
    assert list(tmp_path.iterdir()) == []
