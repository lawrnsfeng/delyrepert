import json
from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import Result

from daily_report.compose import preview as preview_module
from daily_report.compose.models import STYLE_NAMES, Draft
from daily_report.compose.render import Renderer
from daily_report.slack.fake import FakeSlack
from tests.compose.helpers import FIXTURES, draft_files, draft_of, links, report, run_cli
from tests.compose.stubs import stub, toml_list
from tests.slack.helpers import RUN_ID, TOKEN, SlackEnv, write_draft

R1_REPORT = str(FIXTURES / "r1.json")
HEADER = "# daily-report test config\n"
EDIT_PAY_398 = """
    import sys
    from pathlib import Path
    import yaml
    path = Path(sys.argv[1])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for item in data["items"]:
        if item["id"] == "PAY-398":
            item["summary"] = "2 review comments to address"
        if item["id"] == "PAY-405":
            item["hidden"] = True
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
"""


@pytest.fixture(autouse=True)
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend stdin is a terminal and no editor is set."""
    monkeypatch.setattr(preview_module, "is_interactive", lambda: True)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)


def preview(
    env: SlackEnv,
    stdin: str,
    *args: str,
    run_id: str = RUN_ID,
    vars_: dict[str, str | None] | None = None,
) -> Result:
    """Run `daily-report [args] preview <run_id>` with `stdin` typed at the prompt."""
    return run_cli(
        *args,
        *("preview", run_id),
        *("--config", str(env.config), "--state-dir", str(env.state)),
        env={"SLACK_USER_TOKEN": TOKEN, **(vars_ or {})},
        stdin=stdin,
    )


def last_preview(stdout: str) -> str:
    """Return the text of the last preview shown."""
    return "draft " + stdout.rsplit("draft ", 1)[1]


def saved(env: SlackEnv) -> Draft:
    """Read R1's draft back."""
    return Draft.model_validate_json(env.draft_file.read_text(encoding="utf-8"))


def test_ac4_first_post_picks_a_style_and_saves_it(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """Gallery, pick 1, post once, save bluf; the next preview shows only bluf."""
    env.write_config(extra="")
    before = env.config.read_text(encoding="utf-8")
    env.config.write_text(HEADER + before, encoding="utf-8")
    before = env.config.read_text(encoding="utf-8")

    result = preview(env, "p\n1\ny\n")

    assert result.exit_code == 0, result.output
    for number, name in enumerate(STYLE_NAMES, 1):
        assert f"Style {number}/7 — {name}" in result.stdout
    assert len(links(result.stdout)) == 7
    (request,) = fake_slack.called("chat.postMessage")
    assert request.body["thread_ts"]
    after = env.config.read_text(encoding="utf-8")
    assert after == before + '\n[render]\nstyle = "bluf"\n'
    again = preview(env, "c\n")
    assert len(links(again.stdout)) == 1
    assert "Style 1/" not in again.stdout


def test_ac4_declining_the_save_leaves_the_config(env: SlackEnv) -> None:
    """Answering n keeps render.style unset."""
    before = env.config.read_bytes()

    result = preview(env, "p\n3\nn\n")

    assert result.exit_code == 0, result.output
    assert env.config.read_bytes() == before


def test_style_key_picks_and_shows_one_style(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """`s` switches the preview to the picked style, and `p` posts it without asking again."""
    result = preview(env, "x\ns\n9\n2\np\nn\n")

    assert result.exit_code == 0, result.output
    assert "choose one of p, e, s, t, c" in result.stdout
    assert last_preview(result.stdout).count("block-kit-builder") == 1
    (request,) = fake_slack.called("chat.postMessage")
    assert request.body["text"]
    assert not any(n.startswith("Style 1/") for n in last_preview(result.stdout).splitlines())


def test_gallery_flag_shows_every_style_even_when_one_is_set(env: SlackEnv) -> None:
    """`preview --gallery` re-opens the seven styles."""
    env.write_config(extra='\n[render]\nstyle = "ytb"\n')

    result = run_cli(
        "preview",
        RUN_ID,
        "--gallery",
        *("--config", str(env.config), "--state-dir", str(env.state)),
        stdin="c\n",
    )

    assert len(links(result.stdout)) == 7


def test_ac5_editor_rewords_and_hides(env: SlackEnv, tmp_path: Path) -> None:
    """The edit lands in the re-render, the counts line, the fallback text and the draft."""
    editor = stub(tmp_path, EDIT_PAY_398)

    result = preview(env, "e\nc\n", vars_={"EDITOR": editor})

    assert result.exit_code == 0, result.output
    final = last_preview(result.stdout)
    assert "Refund webhook retries — 2 review comments to address" in final
    assert "Settlement report totals" not in final
    assert "✅ 1 done" in final
    draft = saved(env)
    assert {i.id: i.hidden for i in draft.items}["PAY-405"]
    assert "PAY-405" not in json.dumps(
        Renderer(display_name=None, blocked_animated=None)
        .render(draft, "bluf", animated_available=False)
        .model_dump(),
    )


@pytest.mark.parametrize("idle_rounds", [0, 1])
def test_ac6_invalid_edit_reopens_with_the_error(
    env: SlackEnv,
    tmp_path: Path,
    idle_rounds: int,
) -> None:
    """An unknown item reopens the editor with `# error:` on top, once, until it is fixed."""
    log = tmp_path / "log.txt"
    editor = stub(
        tmp_path,
        f"""
        import sys
        from pathlib import Path
        path, log = Path(sys.argv[1]), Path({str(log)!r})
        text = path.read_text(encoding="utf-8")
        runs = log.read_text(encoding="utf-8").count("\\n") if log.exists() else 0
        with log.open("a", encoding="utf-8") as out:
            out.write(text.splitlines()[0] + "|" + str(text.count("# error:")) + "\\n")
        if runs == 0:
            path.write_text(text.replace("id: PAY-398", "id: PAY-999"), encoding="utf-8")
        elif runs > {idle_rounds}:
            path.write_text(text.replace("PAY-999", "PAY-398"), encoding="utf-8")
        """,
    )

    result = preview(env, "e\nc\n", vars_={"EDITOR": editor})

    assert result.exit_code == 0, result.output
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 + idle_rounds
    assert not lines[0].startswith("# error")
    assert all(line == "# error: unknown item PAY-999|1" for line in lines[1:])


def test_ac7_no_editor_prints_the_path(env: SlackEnv) -> None:
    """With VISUAL and EDITOR unset, the owner is told which file to edit."""
    result = preview(env, "e\n\nc\n")

    assert result.exit_code == 0, result.output
    path = env.state / "drafts" / f"{RUN_ID}.edit.yaml"
    assert f"edit {path} then press Enter" in result.stdout
    assert not path.exists()


def test_ac8_no_parent_offers_a_top_level_post(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """No thread today: y posts top-level, N posts nothing and exits 0."""
    env.write_config(extra='\n[render]\nstyle = "bluf"\n')
    fake_slack.history = []

    declined = preview(env, "p\nN\n")

    assert declined.exit_code == 0, declined.output
    assert "no standup thread found today in #C0STANDUP" in declined.stdout
    assert "Post top-level in #C0STANDUP instead? [y/N]" in declined.stdout
    assert fake_slack.called("chat.postMessage") == []

    accepted = preview(env, "p\ny\n")

    assert accepted.exit_code == 0, accepted.output
    (request,) = fake_slack.called("chat.postMessage")
    assert "thread_ts" not in request.body


def test_ac8_target_key_cycles(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """`t` walks thread, thread_broadcast, channel and back; `p` uses the current one."""
    env.write_config(extra='\n[render]\nstyle = "bluf"\n')

    result = preview(env, "t\nt\nt\nt\nt\np\n")

    assert result.exit_code == 0, result.output
    targets = [line for line in result.stdout.splitlines() if line.startswith("target: ")]
    assert targets == [f"target: {t}" for t in ("thread_broadcast", "channel", "thread")] + [
        "target: thread_broadcast",
        "target: channel",
    ]
    (request,) = fake_slack.called("chat.postMessage")
    assert "thread_ts" not in request.body


def test_ac9_cancel_makes_no_slack_request_and_keeps_the_inbox(
    env: SlackEnv,
    fake_slack: FakeSlack,
) -> None:
    """`c` posts nothing, touches no note and exits 0."""
    inbox = env.inbox.read_bytes()

    result = preview(env, "c\n")

    assert result.exit_code == 0, result.output
    assert fake_slack.requests == []
    assert env.inbox.read_bytes() == inbox
    assert saved(env).posted is None


def test_ac11_no_terminal_prints_how_to_post(
    env: SlackEnv,
    fake_slack: FakeSlack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a terminal compose writes the draft, shows the preview and the post command."""
    monkeypatch.setattr(preview_module, "is_interactive", lambda: False)
    state = env.root / "fresh"

    result = run_cli(
        "compose",
        *("--config", str(env.config), "--state-dir", str(state)),
        *("--report", R1_REPORT, "--style", "ytb"),
    )

    assert result.exit_code == 0, result.output
    (path,) = draft_files(state)
    assert f"post with: daily-report post {path.stem} --style ytb --yes" in result.stdout
    assert "Needs attention" in result.stdout
    assert fake_slack.requests == []


def test_ac11_no_terminal_without_style_suggests_bluf(
    env: SlackEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no style chosen yet, the hint names bluf."""
    monkeypatch.setattr(preview_module, "is_interactive", lambda: False)

    result = preview(env, "")

    assert f"post with: daily-report post {RUN_ID} --style bluf --yes" in result.stdout


def test_ac13_dry_run_preview_loop_changes_nothing(
    env: SlackEnv,
    fake_slack: FakeSlack,
    tmp_path: Path,
) -> None:
    """Edit, post and save under --dry-run print the effects and write nothing."""
    touched, seen = tmp_path / "cooked", tmp_path / "editor-path"
    cook = stub(tmp_path, f"from pathlib import Path\nPath({str(touched)!r}).touch()\n", "cook.py")
    env.write_config(extra=f"\n[cook]\ncommand = {toml_list(cook.split(' ', 1))}\n")
    editor = stub(
        tmp_path,
        f"import sys\nfrom pathlib import Path\nPath({str(seen)!r}).write_text(sys.argv[1])\n"
        + dedent(EDIT_PAY_398),
        "editor.py",
    )
    state, config = env.root / "fresh", env.config
    before = config.read_bytes()
    argv = [
        "--dry-run",
        "compose",
        *("--config", str(config), "--state-dir", str(state)),
        *("--report", R1_REPORT),
    ]

    result = run_cli(
        *argv,
        env={"SLACK_USER_TOKEN": TOKEN, "EDITOR": editor},
        stdin="e\np\n1\ny\n",
    )

    assert result.exit_code == 0, result.output
    assert not Path(seen.read_text(encoding="utf-8")).is_relative_to(state)
    assert "2 review comments to address" in result.stdout.rsplit("target: ", 1)[1]
    assert "[dry-run] would post chat.postMessage to C0STANDUP" in result.stderr
    assert f'[dry-run] would write render.style = "bluf" to {config}' in result.stderr
    assert config.read_bytes() == before
    assert not state.exists()
    assert not touched.exists()
    assert fake_slack.called("chat.postMessage") == []

    cooked = run_cli(*argv, "--cook", env={"SLACK_USER_TOKEN": TOKEN}, stdin="c\n")

    assert cooked.exit_code == 0, cooked.output
    assert touched.exists()


def test_post_failure_exits_9(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """A Slack error from `p` maps to exit 9 and keeps the draft unposted."""
    env.write_config(extra='\n[render]\nstyle = "bluf"\n')
    fake_slack.script("chat.postMessage", "invalid_blocks")

    result = preview(env, "p\n")

    assert result.exit_code == 9
    assert saved(env).posted is None


def test_posting_again_exits_8(env: SlackEnv) -> None:
    """A posted draft cannot be posted from the preview."""
    write_draft(
        env.state,
        [],
        posted={"channel": "C0STANDUP", "ts": "1.1", "permalink": "https://x.example/p1"},
    )
    env.write_config(extra='\n[render]\nstyle = "bluf"\n')

    result = preview(env, "p\n")

    assert result.exit_code == 8


def test_post_without_slack_token_exits_2(env: SlackEnv) -> None:
    """The token is read only when posting; its absence is a config error."""
    env.write_config(extra='\n[render]\nstyle = "bluf"\n')

    result = preview(env, "p\n", vars_={"SLACK_USER_TOKEN": None})

    assert result.exit_code == 2


def test_preview_unknown_run_exits_2(env: SlackEnv) -> None:
    """A run id with no draft is a usage error."""
    result = preview(env, "", run_id="20200101-000000")

    assert result.exit_code == 2


def test_preview_without_state_dir_exits_2(env: SlackEnv) -> None:
    """No state dir from any source: exit 2."""
    result = run_cli("preview", RUN_ID, "--config", str(env.config))

    assert result.exit_code == 2


def test_preview_text_of_an_empty_day_has_no_counts() -> None:
    """A draft with no items prints just the header."""
    text = preview_module.preview_text(draft_of(report([])), "Dana")

    assert text.splitlines()[0].startswith("Dana · Standup")
    assert len(text.splitlines()) == 1
