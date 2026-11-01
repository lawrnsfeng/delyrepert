import json
from pathlib import Path

import pytest

from tests.compose.helpers import run_cli

DENTIST = "Out 15:00\N{EN DASH}17:00 (dentist)"


@pytest.fixture
def config(tmp_path: Path) -> Path:
    """Write a config file whose state_dir is `<tmp>/state`."""
    path = tmp_path / "config.toml"
    path.write_text(
        f'state_dir = "{tmp_path / "state"}"\n\n[jira]\n'
        'base_url = "https://example.atlassian.net"\nemail = "owner@example.com"\n',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def inbox(tmp_path: Path) -> Path:
    """Return the inbox path the config resolves to."""
    return tmp_path / "state" / "notes" / "pending.jsonl"


def note(config: Path, *args: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Run `daily-report note <args> --config <config>`; return exit code, stdout, stderr."""
    result = run_cli("note", *args, "--config", str(config), env=env)
    return result.exit_code, result.stdout, result.stderr


def test_ac10_note_add_list_and_remove(config: Path, inbox: Path) -> None:
    """A note appends one line, lists as `1. [today] …`, and `rm 1` empties the inbox."""
    assert note(config, DENTIST)[0] == 0
    (line,) = inbox.read_text(encoding="utf-8").splitlines()
    stored = json.loads(line)
    assert sorted(stored) == ["at", "group", "text"]
    assert (stored["text"], stored["group"]) == (DENTIST, "today")

    assert note(config, "list")[:2] == (0, f"1. [today] {DENTIST}\n")

    assert note(config, "rm", "1")[0] == 0
    assert inbox.read_text(encoding="utf-8") == ""


def test_ac10_note_over_120_chars_exits_2(config: Path, inbox: Path) -> None:
    """A 121-character note exits 2 and appends nothing."""
    code, _, stderr = note(config, "x" * 121)

    assert code == 2
    assert stderr == "note is 121 characters; the limit is 120\n"
    assert not inbox.exists()


def test_note_group_and_removal_keep_the_other_lines(config: Path, inbox: Path) -> None:
    """`--group` is stored, and removing one note keeps the others byte for byte."""
    note(config, "first")
    note(config, "second", "--group", "attention")
    before = inbox.read_text(encoding="utf-8").splitlines(keepends=True)

    assert note(config, "rm", "1")[0] == 0

    assert inbox.read_text(encoding="utf-8") == before[1]
    assert note(config, "list")[1] == "1. [attention] second\n"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["rm", "2"], "no note 2: the inbox holds 0\n"),
        (["rm", "one"], "note takes one TEXT, `list` or `rm N`, not: rm one\n"),
        (["a", "b", "c"], "note takes one TEXT, `list` or `rm N`, not: a b c\n"),
    ],
)
def test_bad_note_arguments_exit_2(config: Path, args: list[str], message: str) -> None:
    """Unknown notes and unknown argument shapes exit 2."""
    assert note(config, *args) == (2, "", message)


def test_note_without_config_exits_2(tmp_path: Path) -> None:
    """With no config file source, `note` exits 2."""
    result = run_cli("note", "list", "--state-dir", str(tmp_path))

    assert result.exit_code == 2
    assert "DAILY_REPORT_CONFIG" in result.stderr


@pytest.mark.parametrize(
    ("before", "after", "env"),
    [
        (["--dry-run"], [], {}),
        ([], ["--dry-run"], {}),
        ([], [], {"DAILY_REPORT_DRY_RUN": "1"}),
    ],
)
def test_ac15_dry_run_note_and_rm_leave_the_inbox(
    config: Path,
    inbox: Path,
    before: list[str],
    after: list[str],
    env: dict[str, str],
) -> None:
    """Dry-run reports the append and the removal and leaves the inbox byte-identical."""
    note(config, "kept")
    original = inbox.read_bytes()

    def dry(*args: str) -> tuple[int, str]:
        result = run_cli(*before, "note", *args, *after, "--config", str(config), env=env)
        return result.exit_code, result.stderr

    assert dry("x") == (0, f"[dry-run] would append {inbox}\n")
    assert dry("rm", "1") == (0, f"[dry-run] would remove note 1 from {inbox}\n")
    assert dry("x" * 121)[0] == 2
    assert inbox.read_bytes() == original
    assert not (inbox.parent.parent / "drafts").exists()
