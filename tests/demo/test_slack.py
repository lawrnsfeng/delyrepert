import json
from pathlib import Path

import pytest

from daily_report.slack.fake import FakeSlack
from tests.demo.helpers import TOKEN, DemoEnv

STYLES = [
    f"{mode} {style}"
    for mode in ("team", "self")
    for style in ("bluf", "ytb", "table", "markdown", "compact", "cards", "richlist")
]


@pytest.fixture
def env(tmp_path: Path, slack_url: str) -> DemoEnv:
    """Return a demo env whose config names the fake Slack and the demo workspace."""
    demo_env = DemoEnv(tmp_path, slack_url)
    demo_env.write_live_config()
    return demo_env


@pytest.mark.parametrize("dry_run", [True, False])
def test_ac5_foreign_workspace_is_refused(
    env: DemoEnv,
    fake_slack: FakeSlack,
    *,
    dry_run: bool,
) -> None:
    """A token from another team posts nothing and exits 5."""
    fake_slack.team_id = "T0REAL"

    result = env.run(token=TOKEN, stdin="y\n", dry_run=dry_run)

    assert result.exit_code == 5
    assert "refusing: token team T0REAL is not demo.slack_team_id T0DEMO" in result.stderr
    assert fake_slack.called("chat.postMessage") == []


def test_ac5_matching_workspace_dry_run_posts_nothing(env: DemoEnv, fake_slack: FakeSlack) -> None:
    """Dry-run in the demo workspace reports the post and writes nothing to Slack."""
    result = env.run(token=TOKEN, dry_run=True)

    assert result.exit_code == 0, result.output
    assert "[dry-run] would post parent + 14 replies to #C0DEMO" in result.stderr
    assert fake_slack.called("chat.postMessage") == []
    assert len(fake_slack.called("auth.test")) == 1


def test_ac6_posts_parent_then_one_reply_per_style(env: DemoEnv, fake_slack: FakeSlack) -> None:
    """`y` posts the parent, the finder locates it and 14 replies thread under it."""
    result = env.run(token=TOKEN, stdin="y\n")

    assert result.exit_code == 0, result.output
    assert "Post 14 replies to #C0DEMO?" in result.stdout
    parent, *replies = fake_slack.called("chat.postMessage")
    assert parent.body == {
        "channel": "C0DEMO",
        "text": "🗓️ Daily standup — 2026-11-03 (demo)",
    }
    assert len(fake_slack.called("conversations.history")) == 1
    thread_ts = f"{fake_slack.first_ts:.6f}"
    assert len(replies) == 14
    for number, (reply, name) in enumerate(zip(replies, STYLES, strict=True), 1):
        assert reply.body["channel"] == "C0DEMO"
        assert reply.body["thread_ts"] == thread_ts
        assert "reply_broadcast" not in reply.body
        label = reply.body["blocks"][0]["elements"][0]["text"]
        assert label == f"Style {number}/14 — {name}"
    golden = json.loads((env.out / "team" / "bluf.json").read_text("utf-8"))
    assert replies[0].body["blocks"][1:] == golden["blocks"]


def test_ac6_one_style_is_labelled_one_of_one(env: DemoEnv, fake_slack: FakeSlack) -> None:
    """`--style ytb --mode self` posts a single reply."""
    result = env.run("--style", "ytb", "--mode", "self", token=TOKEN, stdin="y\n")

    assert result.exit_code == 0, result.output
    assert "Post 1 replies to #C0DEMO?" in result.stdout
    (reply,) = fake_slack.called("chat.postMessage")[1:]
    assert reply.body["blocks"][0]["elements"][0]["text"] == "Style 1/1 — self ytb"


def test_ac7_no_answer_posts_nothing(env: DemoEnv, fake_slack: FakeSlack) -> None:
    """`n` leaves Slack untouched and exits 0."""
    result = env.run(token=TOKEN, stdin="n\n")

    assert result.exit_code == 0, result.output
    assert fake_slack.called("chat.postMessage") == []


def test_ac7_broadcast_marks_every_reply(env: DemoEnv, fake_slack: FakeSlack) -> None:
    """`--broadcast` sets `reply_broadcast` on the replies, not on the parent."""
    result = env.run("--broadcast", token=TOKEN, stdin="y\n")

    assert result.exit_code == 0, result.output
    parent, *replies = fake_slack.called("chat.postMessage")
    assert "reply_broadcast" not in parent.body
    assert [reply.body["reply_broadcast"] for reply in replies] == [True] * 14


@pytest.mark.parametrize("key", ["channel", "slack_team_id"])
def test_ac7_live_run_needs_the_demo_keys(env: DemoEnv, fake_slack: FakeSlack, key: str) -> None:
    """A token without `demo.channel` (or the team id) exits 2 naming the key."""
    value = {"channel": "C0DEMO", "slack_team_id": "T0DEMO"}[key]
    lines = [line for line in env.config.read_text("utf-8").splitlines() if value not in line]
    env.config.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = env.run(token=TOKEN, stdin="y\n")

    assert result.exit_code == 2
    assert f"demo.{key}" in result.stderr
    assert fake_slack.requests == []


def test_ac7_parent_text_must_match_the_seeded_parent(env: DemoEnv, fake_slack: FakeSlack) -> None:
    """A `demo.parent_text` the seeded parent lacks exits 2 before any post."""
    env.config.write_text(
        env.config.read_text("utf-8").replace("[demo]", '[demo]\nparent_text = "nowhere"'),
        encoding="utf-8",
    )

    result = env.run(token=TOKEN, stdin="y\n")

    assert result.exit_code == 2
    assert "demo.parent_text 'nowhere'" in result.stderr
    assert fake_slack.called("chat.postMessage") == []


def test_ac7_slack_errors_exit_9(env: DemoEnv, fake_slack: FakeSlack) -> None:
    """A Slack API error maps to exit 9."""
    fake_slack.script("chat.postMessage", "not_in_channel")

    result = env.run(token=TOKEN, stdin="y\n")

    assert result.exit_code == 9
    assert "not_in_channel" in result.stderr
