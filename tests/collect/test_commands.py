import json
import shutil
import socket
from pathlib import Path

import pytest

from daily_report.collect.models import Report
from daily_report.jira.fake import FakeJira
from tests.builders import issue, write_snapshot
from tests.collect.helpers import collect_args, run_cli

DRY_RUN_LINE = "[dry-run] would write snapshots/2026-11-03.json\n"
SEARCH = "/rest/api/3/search/jql"


def request_paths(fake_jira: FakeJira) -> list[tuple[str, str, dict[str, str]]]:
    """Return the fake's recorded requests as (method, path, query)."""
    return [(r.method, r.path, r.query) for r in fake_jira.requests]


def test_ac1_missing_state_dir_exits_2_and_creates_nothing(
    tmp_path: Path,
    config_file: Path,
    fake_jira: FakeJira,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no state-dir source, collect exits 2, names all three sources, writes nothing."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    result = run_cli("collect", "--config", str(config_file))

    assert result.exit_code == 2
    for source in ("--state-dir", "DAILY_REPORT_STATE_DIR", "state_dir in the config file"):
        assert source in result.stderr
    assert list(home.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.toml", "home"]
    assert fake_jira.requests == []


def test_ac2_missing_config_exits_2(tmp_path: Path) -> None:
    """With neither --config nor DAILY_REPORT_CONFIG, collect exits 2 naming both."""
    result = run_cli("collect", "--state-dir", str(tmp_path))

    assert result.exit_code == 2
    assert "--config" in result.stderr
    assert "DAILY_REPORT_CONFIG" in result.stderr


def test_ac2_missing_token_exits_2_before_any_request(
    config_file: Path,
    state_dir: Path,
    fake_jira: FakeJira,
) -> None:
    """Without JIRA_API_TOKEN, collect exits 2 and never calls Jira."""
    result = run_cli(*collect_args(config_file, state_dir), env={"JIRA_API_TOKEN": ""})

    assert result.exit_code == 2
    assert "JIRA_API_TOKEN" in result.stderr
    assert fake_jira.requests == []


def test_ac17_rejected_credentials_exit_3(
    config_file: Path,
    state_dir: Path,
    fake_jira: FakeJira,
    jira_url: str,
) -> None:
    """A 401 on search exits 3 with the site, email and status, and writes nothing."""
    fake_jira.script(SEARCH, 401)

    result = run_cli(*collect_args(config_file, state_dir))

    assert result.exit_code == 3
    assert result.stderr == (
        f"Jira rejected credentials for owner@example.com at {jira_url} (401)\n"
    )
    assert result.stdout == ""
    assert not state_dir.exists()


def test_ac19_partial_failure_warns_and_catches_up_next_day(
    config_file: Path,
    state_dir: Path,
    fake_jira: FakeJira,
) -> None:
    """A ticket whose comments keep failing is reported later with its original timestamp."""
    baseline = write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress", "ABC-2": "To Do"})
    fake_jira.fixtures.issues.append(issue("ABC-2", "In Progress", updated="2026-11-02T16:00:00Z"))
    fake_jira.fixtures.changelogs.append(
        {
            "issueId": "10002",
            "changeHistories": [
                {
                    "id": "h2",
                    "created": "2026-11-02T16:00:00Z",
                    "items": [
                        {"field": "status", "fromString": "To Do", "toString": "In Progress"},
                    ],
                },
            ],
        },
    )
    fake_jira.script("/rest/api/3/issue/ABC-2/comment", 500, times=4, retry_after=0)

    result = run_cli(*collect_args(config_file, state_dir))

    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert [item["key"] for item in report["items"]] == ["ABC-1"]
    assert report["warnings"] == [
        {"key": "ABC-2", "stage": "comments", "reason": "HTTP 500 after 4 attempts"},
    ]
    assert "ABC-2" in result.stderr
    assert "HTTP 500 after 4 attempts" in result.stderr
    snapshot = json.loads((state_dir / "snapshots" / "2026-11-03.json").read_text("utf-8"))
    assert (
        snapshot["issues"]["ABC-2"] == json.loads(baseline.read_text("utf-8"))["issues"]["ABC-2"]
    )

    next_day = run_cli(*collect_args(config_file, state_dir, "2026-11-04T09:00:00+00:00"))

    items = {item["key"]: item for item in json.loads(next_day.stdout)["items"]}
    assert [e["at"] for e in items["ABC-2"]["events"]] == ["2026-11-02T16:00:00Z"]


def test_ac20_search_failure_exits_4(
    config_file: Path,
    state_dir: Path,
    fake_jira: FakeJira,
) -> None:
    """Search failing four times exits 4 and writes no snapshot."""
    fake_jira.script(SEARCH, 500, times=4, retry_after=0)

    result = run_cli(*collect_args(config_file, state_dir))

    assert result.exit_code == 4
    assert result.stderr == "Jira search failed: HTTP 500 after 4 attempts\n"
    assert not state_dir.exists()


def test_ac22_out_file_matches_stdout(tmp_path: Path, config_file: Path, state_dir: Path) -> None:
    """`--out` holds exactly the bytes printed on stdout, and they validate as a Report."""
    out = tmp_path / "r.json"

    result = run_cli(*collect_args(config_file, state_dir), "--out", str(out))

    assert result.exit_code == 0
    assert out.read_text(encoding="utf-8") == result.stdout
    assert Report.model_validate_json(result.stdout).schema_version == 1


def _real_then_dry(
    tmp_path: Path,
    config_file: Path,
    fake_jira: FakeJira,
    args: list[str],
    env: dict[str, str],
) -> None:
    """Run collect for real on a copy of AC4's state, then with `args`/`env`; compare."""
    dry_state = tmp_path / "dry-state"
    snapshot = write_snapshot(dry_state, "2026-11-02", {"ABC-1": "In Progress"})
    before = snapshot.read_bytes()
    real_state = tmp_path / "real-state"
    shutil.copytree(dry_state, real_state)
    real = run_cli(*collect_args(config_file, real_state))
    real_requests = request_paths(fake_jira)
    fake_jira.requests.clear()

    dry = run_cli(*args, *collect_args(config_file, dry_state)[1:], env=env)

    assert (real.exit_code, dry.exit_code) == (0, 0)
    assert dry.stdout == real.stdout
    assert request_paths(fake_jira) == real_requests
    assert dry.stderr == DRY_RUN_LINE
    assert [p.name for p in (dry_state / "snapshots").iterdir()] == ["2026-11-02.json"]
    assert snapshot.read_bytes() == before


def test_ac25_root_dry_run_matches_real_run(
    tmp_path: Path,
    config_file: Path,
    fake_jira: FakeJira,
) -> None:
    """`daily-report --dry-run collect` prints the real report and writes nothing."""
    _real_then_dry(tmp_path, config_file, fake_jira, ["--dry-run", "collect"], {})


@pytest.mark.parametrize(
    ("args", "env"),
    [
        (["collect", "--dry-run"], {}),
        (["collect"], {"DAILY_REPORT_DRY_RUN": "1"}),
        (["collect"], {"DAILY_REPORT_DRY_RUN": "TRUE"}),
    ],
)
def test_ac26_dry_run_after_subcommand_or_from_env(
    tmp_path: Path,
    config_file: Path,
    fake_jira: FakeJira,
    args: list[str],
    env: dict[str, str],
) -> None:
    """The subcommand flag and a truthy env var behave like the root flag."""
    _real_then_dry(tmp_path, config_file, fake_jira, args, env)


def test_ac26_invalid_dry_run_env_exits_2(
    config_file: Path,
    state_dir: Path,
    fake_jira: FakeJira,
) -> None:
    """An unknown DAILY_REPORT_DRY_RUN value exits 2 naming the accepted values."""
    result = run_cli(*collect_args(config_file, state_dir), env={"DAILY_REPORT_DRY_RUN": "maybe"})

    assert result.exit_code == 2
    assert "DAILY_REPORT_DRY_RUN" in result.stderr
    assert "1, true or yes" in result.stderr
    assert "0, false, no or an empty value" in result.stderr
    assert fake_jira.requests == []


def test_ac27_dry_run_still_writes_explicit_out(tmp_path: Path, config_file: Path) -> None:
    """Under dry-run a missing state dir stays missing, but --out is written."""
    state_dir = tmp_path / "new-state"
    out = tmp_path / "r.json"

    result = run_cli("--dry-run", *collect_args(config_file, state_dir), "--out", str(out))

    assert result.exit_code == 0
    assert not state_dir.exists()
    assert out.read_text(encoding="utf-8") == result.stdout
    assert result.stderr == DRY_RUN_LINE


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [("no-state-dir", 2), ("401", 3), ("search-500", 4), ("partial", 0)],
)
def test_ac28_dry_run_keeps_exit_codes(
    config_file: Path,
    state_dir: Path,
    fake_jira: FakeJira,
    case: str,
    expected_code: int,
) -> None:
    """Dry-run changes no exit code and reports the write only when one would happen."""
    write_snapshot(state_dir, "2026-11-02", {"ABC-1": "In Progress"})
    args = collect_args(config_file, state_dir)
    if case == "no-state-dir":
        args = ["collect", "--config", str(config_file)]
    if case == "401":
        fake_jira.script(SEARCH, 401)
    if case == "search-500":
        fake_jira.script(SEARCH, 500, times=4, retry_after=0)
    if case == "partial":
        fake_jira.script("/rest/api/3/issue/ABC-1/comment", 500, times=4, retry_after=0)

    result = run_cli("--dry-run", *args)

    assert result.exit_code == expected_code
    dry_run_lines = [line for line in result.stderr.splitlines() if "[dry-run]" in line]
    assert dry_run_lines == ([DRY_RUN_LINE.strip()] if case == "partial" else [])
    if case == "partial":
        assert "HTTP 500 after 4 attempts" in result.stderr
    assert [p.name for p in (state_dir / "snapshots").iterdir()] == ["2026-11-02.json"]


def test_naive_as_of_uses_config_timezone(tmp_path: Path, jira_url: str, state_dir: Path) -> None:
    """An --as-of without offset is read in the config's timezone."""
    config = tmp_path / "tokyo.toml"
    config.write_text(
        f'timezone = "Asia/Tokyo"\n[jira]\nbase_url = "{jira_url}"\nemail = "o@example.com"\n',
        encoding="utf-8",
    )

    result = run_cli(*collect_args(config, state_dir, "2026-11-03T18:00:00"))

    assert json.loads(result.stdout)["as_of"] == "2026-11-03T09:00:00Z"


def test_invalid_config_file_exits_2(tmp_path: Path, state_dir: Path) -> None:
    """A config file that does not parse exits 2 and names the file."""
    config = tmp_path / "broken.toml"
    config.write_text("state_dir = [", encoding="utf-8")

    result = run_cli(*collect_args(config, state_dir))

    assert result.exit_code == 2
    assert f"invalid config file {config}" in result.stderr


def test_unreachable_jira_exits_4(tmp_path: Path, state_dir: Path) -> None:
    """A Jira site that refuses connections fails the search with exit 4."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = tmp_path / "closed.toml"
    config.write_text(
        f'[jira]\nbase_url = "http://127.0.0.1:{port}"\nemail = "o@example.com"\n',
        encoding="utf-8",
    )

    result = run_cli(*collect_args(config, state_dir))

    assert result.exit_code == 4
    assert f"cannot reach Jira at http://127.0.0.1:{port}" in result.stderr
