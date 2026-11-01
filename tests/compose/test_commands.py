import json
from pathlib import Path

import pytest

from daily_report.collect.models import CollectWarning
from daily_report.compose.models import STYLE_NAMES, Draft
from daily_report.jira.fake import FakeJira
from tests.compose.helpers import (
    FIXTURES,
    R1_AS_OF,
    draft_files,
    links,
    report,
    report_item,
    run_cli,
)

R1_REPORT = str(FIXTURES / "r1.json")
NOTE_LINE = '{"at":"2026-11-01T18:00:00Z","text":"Out of office","group":"today"}\n'


def compose_args(config: Path, state_dir: Path, *more: str) -> list[str]:
    """Return `compose --config … --state-dir … --as-of <R1> <more>`."""
    return [
        "compose",
        "--config",
        str(config),
        "--state-dir",
        str(state_dir),
        "--as-of",
        R1_AS_OF,
        *more,
    ]


def out_names(out: Path) -> list[str]:
    """Return the file names under `out`."""
    return sorted(path.name for path in out.iterdir())


def test_ac11_compose_writes_seven_styles_and_the_draft(
    tmp_path: Path,
    config_file: Path,
    state_dir: Path,
) -> None:
    """From the fake Jira, compose writes 7 payloads, 7 Builder links and the draft."""
    out = tmp_path / "out"

    result = run_cli(*compose_args(config_file, state_dir, "--out", str(out)))

    assert result.exit_code == 0, result.output
    assert out_names(out) == sorted(f"{style}.json" for style in STYLE_NAMES)
    assert len(links(result.stdout)) == 7
    (draft_file,) = draft_files(state_dir)
    draft = Draft.model_validate_json(draft_file.read_text(encoding="utf-8"))
    assert result.stdout.splitlines()[0] == f"draft {draft.run_id}"
    assert [item.id for item in draft.items][:3] == ["PAY-412", "PAY-398", "PAY-405"]
    bluf = json.loads((out / "bluf.json").read_text(encoding="utf-8"))
    assert "PAY-412" in bluf["text"]


def test_ac11_render_style_renders_only_that_style(
    tmp_path: Path,
    config_file: Path,
    state_dir: Path,
) -> None:
    """With `render.style = "ytb"`, only ytb is written."""
    config = config_file.read_text(encoding="utf-8")
    config_file.write_text(f'{config}\n[render]\nstyle = "ytb"\n', encoding="utf-8")
    out = tmp_path / "out"

    result = run_cli(*compose_args(config_file, state_dir, "--out", str(out)))

    assert result.exit_code == 0, result.output
    assert out_names(out) == ["ytb.json"]
    assert len(links(result.stdout)) == 1


def test_style_flag_overrides_render_style(tmp_path: Path, config_file: Path) -> None:
    """`--style` renders only that style, and without `--out` only the link is printed."""
    state = tmp_path / "state"

    result = run_cli(*compose_args(config_file, state, "--report", R1_REPORT, "--style", "table"))

    assert result.exit_code == 0, result.output
    assert len(links(result.stdout)) == 1
    assert len(draft_files(state)) == 1


def test_ac12_files_land_only_in_state_and_out(
    tmp_path: Path,
    config_file: Path,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With empty HOME and cwd, compose writes under the state dir and --out only."""
    home, cwd, out = tmp_path / "home", tmp_path / "cwd", tmp_path / "out"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(cwd)

    result = run_cli(*compose_args(config_file, state_dir, "--out", str(out)))

    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "config.toml",
        "cwd",
        "home",
        "out",
        "state",
    ]
    assert sorted(p.name for p in state_dir.iterdir()) == ["drafts", "snapshots"]
    assert list(home.iterdir()) == []
    assert list(cwd.iterdir()) == []


def test_ac12_no_state_dir_exits_2(tmp_path: Path, config_file: Path) -> None:
    """With no state dir from any source, compose exits 2 and writes nothing."""
    result = run_cli("compose", "--config", str(config_file), "--report", R1_REPORT)

    assert result.exit_code == 2
    assert "--state-dir" in result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.toml"]


@pytest.mark.parametrize(
    ("args", "env"),
    [
        (["--dry-run", "compose"], {}),
        (["compose", "--dry-run"], {}),
        (["compose"], {"DAILY_REPORT_DRY_RUN": "1"}),
    ],
)
def test_ac15_dry_run_compose_still_writes_out(
    tmp_path: Path,
    config_file: Path,
    args: list[str],
    env: dict[str, str],
) -> None:
    """Dry-run reports the draft write, keeps the inbox, and still writes the --out files."""
    state, out = tmp_path / "state", tmp_path / "out"
    inbox = state / "notes" / "pending.jsonl"
    inbox.parent.mkdir(parents=True)
    inbox.write_text(NOTE_LINE, encoding="utf-8")
    rest = compose_args(config_file, state, "--report", R1_REPORT, "--out", str(out))[1:]

    result = run_cli(*args, *rest, env=env)

    assert result.exit_code == 0, result.output
    run_id = result.stdout.splitlines()[0].removeprefix("draft ")
    assert f"[dry-run] would write {state}/drafts/{run_id}.json" in result.stderr
    assert len(out_names(out)) == 7
    assert inbox.read_text(encoding="utf-8") == NOTE_LINE
    assert not (state / "drafts").exists()
    assert "Out of office" in (out / "bluf.json").read_text(encoding="utf-8")


def test_report_from_stdin(tmp_path: Path, config_file: Path) -> None:
    """`--report -` reads the report JSON from stdin and needs no Jira token."""
    stdin = (FIXTURES / "r1.json").read_text(encoding="utf-8")

    result = run_cli(
        *compose_args(config_file, tmp_path / "state", "--report", "-"),
        env={"JIRA_API_TOKEN": ""},
        stdin=stdin,
    )

    assert result.exit_code == 0, result.output
    assert len(links(result.stdout)) == 7


def test_invalid_report_exits_2(tmp_path: Path, config_file: Path) -> None:
    """A report file that is not a collector report exits 2."""
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")

    result = run_cli(*compose_args(config_file, tmp_path / "state", "--report", str(bad)))

    assert result.exit_code == 2
    assert result.stderr.startswith(f"invalid report {bad}:")


def test_missing_token_exits_2_before_any_request(
    config_file: Path,
    state_dir: Path,
    fake_jira: FakeJira,
) -> None:
    """Without --report, compose collects and so needs JIRA_API_TOKEN."""
    result = run_cli(*compose_args(config_file, state_dir), env={"JIRA_API_TOKEN": ""})

    assert result.exit_code == 2
    assert "JIRA_API_TOKEN" in result.stderr
    assert fake_jira.requests == []


@pytest.mark.parametrize(("status", "code"), [(401, 3), (400, 4)])
def test_collector_exit_codes_are_kept(
    config_file: Path,
    state_dir: Path,
    fake_jira: FakeJira,
    status: int,
    code: int,
) -> None:
    """Rejected credentials exit 3 and a failed search exits 4, as in collect."""
    fake_jira.script("/rest/api/3/search/jql", status)

    result = run_cli(*compose_args(config_file, state_dir))

    assert result.exit_code == code
    assert not (state_dir / "drafts").exists()


def test_preview_warnings_go_to_stderr(tmp_path: Path, config_file: Path) -> None:
    """Report warnings, dropped context and a render notice are logged, never posted."""
    warning = CollectWarning(key="PAY-9", stage="comments", reason="HTTP 500")
    source = report([report_item(f"PAY-{n}", "in_progress") for n in range(1, 61)])
    source = source.model_copy(update={"warnings": [warning]})
    path = tmp_path / "report.json"
    path.write_text(source.model_dump_json(), encoding="utf-8")
    context = tmp_path / "skill.json"
    context.write_text('{"source": "demo-skill", "items": [{"group": "later"}]}', encoding="utf-8")
    args = ["--report", str(path), "--context", str(context), "--style", "cards"]

    result = run_cli(*compose_args(config_file, tmp_path / "state", *args))

    assert result.exit_code == 0, result.output
    assert "jira issue skipped" in result.stderr
    assert "context dropped" in result.stderr
    assert "cards exceeds 50 blocks; showing compact" in result.stderr
