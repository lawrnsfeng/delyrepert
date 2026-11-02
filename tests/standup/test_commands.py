"""`standup` and `self`: the paste-ready fallback, the clipboard, `--dm` and the preview loop."""

import re
import shutil
from collections.abc import Mapping
from pathlib import Path

import pytest
from typer.testing import Result

from daily_report.compose import preview as preview_module
from daily_report.slack.fake import FakeSlack
from daily_report.standup import clipboard
from tests.compose.helpers import R1_AS_OF, links, run_cli

JIRA = "/browse/"
OWNER = "U0OWNER"
SLACK_ENV = {"SLACK_USER_TOKEN": "xoxp-owner"}


def report(
    command: str,
    config: Path,
    state: Path,
    *more: str,
    env: Mapping[str, str | None] | None = None,
    stdin: str | None = None,
) -> Result:
    """Run `daily-report <command> --config … --state-dir … --as-of <R1> <more>`."""
    return run_cli(
        *(command, "--config", str(config), "--state-dir", str(state), "--as-of", R1_AS_OF),
        *more,
        env=env,
        stdin=stdin,
    )


def with_slack(config: Path, api_url: str, *, owner: bool = True) -> None:
    """Append a `[slack]` table pointing at the fake, and the owner id."""
    text = config.read_text(encoding="utf-8")
    head = f'owner_slack_user_id = "{OWNER}"\n' if owner else ""
    tail = f'\n[slack]\nchannel = "C0STANDUP"\napi_url = "{api_url}"\n'
    tail += 'parent_match = { user_id = "U0BOT" }\n'
    config.write_text(head + text + tail, encoding="utf-8")


@pytest.mark.parametrize("command", ["standup", "self"])
def test_paste_text_needs_no_slack_and_makes_no_slack_request(
    command: str,
    config_file: Path,
    state_dir: Path,
    fake_slack: FakeSlack,
) -> None:
    """No [slack], no Slack env: the text report lists one raw URL per ticket."""
    result = report(command, config_file, state_dir)

    assert result.exit_code == 0, result.output
    urls = re.findall(r"https?://\S+/browse/[A-Z]+-\d+", result.stdout)
    assert any(url.endswith(JIRA + "PAY-412") for url in urls)
    assert len(urls) == len(set(urls)) >= 3
    assert fake_slack.requests == []


def test_self_is_detailed_and_standup_is_concise(config_file: Path, state_dir: Path) -> None:
    """`self` adds the sources footer that the team report leaves out."""
    mine = report("self", config_file, state_dir).stdout
    team = report("standup", config_file, state_dir).stdout

    assert len(mine) > len(team)


def test_html_prints_a_page_with_links(config_file: Path, state_dir: Path) -> None:
    """`--format html` prints a self-contained page whose tickets are links."""
    result = report("self", config_file, state_dir, "--format", "html")

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("<!DOCTYPE html>")
    assert re.search(rf'<a href="http\S+{JIRA}PAY-412">', result.stdout)


def test_out_writes_the_file_instead_of_stdout(
    tmp_path: Path,
    config_file: Path,
    state_dir: Path,
) -> None:
    """`--out FILE` holds the page and stdout stays empty."""
    page = tmp_path / "report.html"

    result = report("standup", config_file, state_dir, "--format", "html", "--out", str(page))

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert re.search(rf'<a href="http\S+{JIRA}PAY-412">', page.read_text(encoding="utf-8"))


def test_blocks_prints_the_json_and_the_builder_link(config_file: Path, state_dir: Path) -> None:
    """Without Slack, `--format blocks` prints one Builder link and the payload."""
    result = report("standup", config_file, state_dir, "--format", "blocks")

    assert result.exit_code == 0, result.output
    assert len(links(result.stdout)) == 1
    assert '"blocks"' in result.stdout


def test_blocks_out_writes_the_payload(tmp_path: Path, config_file: Path, state_dir: Path) -> None:
    """`--out` with blocks keeps the link on stdout and the JSON in the file."""
    payload = tmp_path / "bluf.json"

    result = report("self", config_file, state_dir, "--format", "blocks", "--out", str(payload))

    assert result.exit_code == 0, result.output
    assert len(links(result.stdout)) == 1
    assert '"blocks"' in payload.read_text(encoding="utf-8")


class Clip:
    """A stand-in clipboard program recording what it was fed."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        code: int = 0,
        found: bool = True,
    ) -> None:
        """Install the stand-in; `found=False` means no program is installed."""
        self.fed: list[tuple[list[str], str]] = []
        self._code = code
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name: f"/bin/{name}" if found else None,
        )
        monkeypatch.setattr(clipboard, "run_program", self.run)

    async def run(self, argv: list[str], text: str) -> int:
        """Record the call."""
        self.fed.append((argv, text))
        return self._code


def test_copy_feeds_the_first_program_the_text(
    monkeypatch: pytest.MonkeyPatch,
    config_file: Path,
    state_dir: Path,
) -> None:
    """`--copy` pipes the printed text to clip.exe and still prints it."""
    clip = Clip(monkeypatch)

    result = report("self", config_file, state_dir, "--copy")

    assert result.exit_code == 0, result.output
    assert clip.fed == [(["/bin/clip.exe"], result.stdout)]


def test_copy_dry_run_only_reports_the_intent(
    monkeypatch: pytest.MonkeyPatch,
    config_file: Path,
    state_dir: Path,
) -> None:
    """Dry-run says how many characters it would copy and copies nothing."""
    clip = Clip(monkeypatch)

    result = report("self", config_file, state_dir, "--copy", "--dry-run")

    assert result.exit_code == 0, result.output
    assert (
        f"[dry-run] would copy {len(result.stdout)} characters to the clipboard" in result.stderr
    )
    assert clip.fed == []


def test_copy_without_a_clipboard_program_exits_2(
    monkeypatch: pytest.MonkeyPatch,
    config_file: Path,
    state_dir: Path,
) -> None:
    """None installed: exit 2 naming them all."""
    Clip(monkeypatch, found=False)

    result = report("self", config_file, state_dir, "--copy")

    assert result.exit_code == 2
    for name in ("clip.exe", "wl-copy", "xclip", "pbcopy"):
        assert name in result.stderr


def test_copy_program_failure_exits_2(
    monkeypatch: pytest.MonkeyPatch,
    config_file: Path,
    state_dir: Path,
) -> None:
    """A clipboard program exiting non-zero is reported."""
    Clip(monkeypatch, code=1)

    result = report("self", config_file, state_dir, "--copy")

    assert result.exit_code == 2
    assert "clip.exe failed to copy (exit 1)" in result.stderr


def test_copy_html_is_refused(config_file: Path, state_dir: Path) -> None:
    """HTML source pasted into Slack shows its tags, so `--copy` needs text."""
    result = report("self", config_file, state_dir, "--format", "html", "--copy")

    assert result.exit_code == 2
    assert "--copy only copies --format text" in result.stderr


@pytest.mark.parametrize("command", ["standup", "self"])
def test_cook_needs_a_command(command: str, config_file: Path, state_dir: Path) -> None:
    """`--cook` without `cook.command` is a config error."""
    result = report(command, config_file, state_dir, "--cook")

    assert result.exit_code == 2
    assert "cook.command" in result.stderr


def test_missing_jira_token_exits_2(config_file: Path, state_dir: Path) -> None:
    """The only token paste mode needs."""
    result = report("self", config_file, state_dir, env={"JIRA_API_TOKEN": ""})

    assert result.exit_code == 2
    assert "JIRA_API_TOKEN" in result.stderr


def test_dm_sends_the_self_report_as_the_bot_to_the_owner(
    config_file: Path,
    state_dir: Path,
    slack_url: str,
    fake_slack: FakeSlack,
) -> None:
    """One conversations.open and one chat.postMessage with blocks, both with the bot token."""
    with_slack(config_file, slack_url)

    result = report("self", config_file, state_dir, "--dm", env={"SLACK_BOT_TOKEN": "xoxb-bot"})

    assert result.exit_code == 0, result.output
    (opened,) = fake_slack.called("conversations.open")
    (posted,) = fake_slack.called("chat.postMessage")
    assert opened.body["users"] == OWNER
    assert posted.body["channel"] == f"D0{OWNER}"
    assert posted.body["blocks"]
    assert posted.authorization == "Bearer xoxb-bot"
    assert "PAY-412" in result.stdout  # the paste text is still printed


def test_self_without_dm_never_touches_slack(
    config_file: Path,
    state_dir: Path,
    slack_url: str,
    fake_slack: FakeSlack,
) -> None:
    """Even fully configured, `self` stays off Slack unless asked."""
    with_slack(config_file, slack_url)

    result = report("self", config_file, state_dir, env={**SLACK_ENV, "SLACK_BOT_TOKEN": "x"})

    assert result.exit_code == 0, result.output
    assert fake_slack.requests == []


def test_dm_dry_run_reports_both_calls(
    config_file: Path,
    state_dir: Path,
    slack_url: str,
    fake_slack: FakeSlack,
) -> None:
    """Both Slack writes are only reported."""
    with_slack(config_file, slack_url)

    result = report(
        "self",
        config_file,
        state_dir,
        "--dm",
        "--dry-run",
        env={"SLACK_BOT_TOKEN": "xoxb-bot"},
    )

    assert result.exit_code == 0, result.output
    assert "[dry-run] would conversations.open" in result.stderr
    assert "[dry-run] would chat.postMessage" in result.stderr
    assert fake_slack.requests == []


def test_dm_needs_the_bot_token(config_file: Path, state_dir: Path, slack_url: str) -> None:
    """No SLACK_BOT_TOKEN: exit 2 naming it, before anything is collected."""
    with_slack(config_file, slack_url)

    result = report("self", config_file, state_dir, "--dm")

    assert result.exit_code == 2
    assert "SLACK_BOT_TOKEN" in result.stderr


def test_dm_needs_the_owner(config_file: Path, state_dir: Path, slack_url: str) -> None:
    """No owner_slack_user_id: exit 2 naming it."""
    with_slack(config_file, slack_url, owner=False)

    result = report("self", config_file, state_dir, "--dm", env={"SLACK_BOT_TOKEN": "xoxb-bot"})

    assert result.exit_code == 2
    assert "owner_slack_user_id" in result.stderr


def test_dm_without_a_slack_table_exits_2(config_file: Path, state_dir: Path) -> None:
    """The DM client needs the [slack] api_url, so the table is required."""
    config_file.write_text(
        'owner_slack_user_id = "U0OWNER"\n' + config_file.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = report("self", config_file, state_dir, "--dm", env={"SLACK_BOT_TOKEN": "xoxb-bot"})

    assert result.exit_code == 2
    assert "[slack]" in result.stderr


def test_dm_slack_error_exits_9(
    config_file: Path,
    state_dir: Path,
    slack_url: str,
    fake_slack: FakeSlack,
) -> None:
    """A Slack API error on the DM maps to exit 9."""
    with_slack(config_file, slack_url)
    fake_slack.script("conversations.open", "missing_scope")

    result = report("self", config_file, state_dir, "--dm", env={"SLACK_BOT_TOKEN": "xoxb-bot"})

    assert result.exit_code == 9
    assert "conversations.open" in result.stderr


def test_standup_with_slack_enters_the_preview_loop(
    monkeypatch: pytest.MonkeyPatch,
    config_file: Path,
    state_dir: Path,
    slack_url: str,
    fake_slack: FakeSlack,
) -> None:
    """[slack] plus a user token: the draft preview, Builder links and the post hint."""
    monkeypatch.setattr(preview_module, "is_interactive", lambda: False)
    with_slack(config_file, slack_url)

    result = report("standup", config_file, state_dir, env=SLACK_ENV)

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("draft ")
    assert len(links(result.stdout)) == 7
    assert "post with: daily-report post" in result.stdout
    assert fake_slack.called("chat.postMessage") == []


def test_standup_loop_posts_as_the_owner(
    monkeypatch: pytest.MonkeyPatch,
    config_file: Path,
    state_dir: Path,
    slack_url: str,
    fake_slack: FakeSlack,
) -> None:
    """Answering `p` in the loop posts once, with the user token."""
    monkeypatch.setattr(preview_module, "is_interactive", lambda: True)
    with_slack(config_file, slack_url)

    result = report(
        "standup",
        config_file,
        state_dir,
        "--style",
        "bluf",
        env=SLACK_ENV,
        stdin="p\nn\n",
    )

    assert result.exit_code == 0, result.output
    (posted,) = fake_slack.called("chat.postMessage")
    assert posted.authorization == "Bearer xoxp-owner"


def test_standup_text_with_slack_configured_still_pastes(
    config_file: Path,
    state_dir: Path,
    slack_url: str,
    fake_slack: FakeSlack,
) -> None:
    """An explicit `--format text` skips the loop and Slack."""
    with_slack(config_file, slack_url)

    result = report("standup", config_file, state_dir, "--format", "text", env=SLACK_ENV)

    assert result.exit_code == 0, result.output
    assert JIRA + "PAY-412" in result.stdout
    assert fake_slack.requests == []


def test_standup_with_slack_but_no_user_token_pastes(
    config_file: Path,
    state_dir: Path,
    slack_url: str,
    fake_slack: FakeSlack,
) -> None:
    """[slack] without SLACK_USER_TOKEN falls back to text instead of failing."""
    with_slack(config_file, slack_url)

    result = report("standup", config_file, state_dir)

    assert result.exit_code == 0, result.output
    assert JIRA + "PAY-412" in result.stdout
    assert fake_slack.requests == []


def test_self_with_slack_defaults_to_text(
    config_file: Path,
    state_dir: Path,
    slack_url: str,
) -> None:
    """`self` never opens the preview loop."""
    with_slack(config_file, slack_url)

    result = report("self", config_file, state_dir, env=SLACK_ENV)

    assert result.exit_code == 0, result.output
    assert not result.stdout.startswith("draft ")


async def test_run_program_feeds_stdin_and_returns_the_exit_code(tmp_path: Path) -> None:
    """The real runner pipes the text in and reports the exit code."""
    sink = tmp_path / "sink"

    ok = await clipboard.run_program(["sh", "-c", f"cat > {sink}"], "hello")
    bad = await clipboard.run_program(["sh", "-c", "cat >/dev/null; exit 3"], "x")

    assert (ok, bad) == (0, 3)
    assert sink.read_text(encoding="utf-8") == "hello"
