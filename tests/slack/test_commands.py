import json

import pytest

from daily_report.compose.models import Draft
from daily_report.slack.fake import FakeSlack
from tests.slack.helpers import OLD_TS, PARENT_TS, RUN_ID, TOKEN, SlackEnv


def posted_draft(env: SlackEnv) -> Draft:
    """Read R1's draft back."""
    return Draft.model_validate_json(env.draft_file.read_text(encoding="utf-8"))


def body_text(body: dict[str, object]) -> str:
    """Return the blocks of a posted body as one string."""
    return json.dumps(body["blocks"], ensure_ascii=False)


@pytest.mark.parametrize("match", ['user_id = "U0BOT"', 'text_contains = "Daily standup"'])
def test_ac1_post_replies_in_todays_thread(
    env: SlackEnv,
    fake_slack: FakeSlack,
    match: str,
) -> None:
    """One chat.postMessage into the parent's thread, recorded in the draft."""
    env.write_config(match=match)

    result = env.post("--style", "bluf", "--yes")

    assert result.exit_code == 0, result.output
    (request,) = fake_slack.called("chat.postMessage")
    assert request.body["channel"] == "C0STANDUP"
    assert request.body["thread_ts"] == PARENT_TS
    assert "reply_broadcast" not in request.body
    assert request.authorization == f"Bearer {TOKEN}"
    posted = posted_draft(env).posted
    assert posted is not None
    assert posted.channel == "C0STANDUP"
    assert posted.permalink.startswith("https://fake.slack.com/archives/C0STANDUP/p")
    assert result.stdout.strip() == posted.permalink


def test_ac2_broadcast_sets_reply_broadcast(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """thread_broadcast sends thread_ts plus reply_broadcast."""
    result = env.post("--style", "bluf", "--target", "thread_broadcast", "--yes")

    assert result.exit_code == 0, result.output
    (request,) = fake_slack.called("chat.postMessage")
    assert request.body["thread_ts"] == PARENT_TS
    assert request.body["reply_broadcast"] is True


def test_ac2_channel_target_posts_top_level_without_history(
    env: SlackEnv,
    fake_slack: FakeSlack,
) -> None:
    """Channel sends no thread_ts and never reads the history."""
    result = env.post("--style", "bluf", "--target", "channel", "--yes")

    assert result.exit_code == 0, result.output
    (request,) = fake_slack.called("chat.postMessage")
    assert "thread_ts" not in request.body
    assert fake_slack.called("conversations.history") == []


def test_ac2_post_target_comes_from_config(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """Without --target, post.target decides."""
    env.write_config(extra='\n[post]\ntarget = "channel"\n')

    assert env.post("--style", "bluf", "--yes").exit_code == 0

    assert "thread_ts" not in fake_slack.called("chat.postMessage")[0].body


def test_ac3_no_parent_today_exits_10(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """Only an older parent: exit 10 and no post."""
    fake_slack.history = [{"ts": OLD_TS, "user": "U0BOT", "text": "Daily standup"}]

    result = env.post("--style", "bluf", "--target", "thread", "--yes")

    assert result.exit_code == 10
    assert (
        "no standup thread found today in #C0STANDUP; rerun with --target channel" in result.stderr
    )
    assert fake_slack.called("chat.postMessage") == []


def test_ac3_parent_of_another_user_is_not_a_match(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """A message that fails the user_id rule is not the parent."""
    fake_slack.history = [{"ts": PARENT_TS, "user": "U0OTHER", "text": "Daily standup"}]

    assert env.post("--style", "bluf", "--yes").exit_code == 10


def test_ac4_animated_emoji_used_when_listed(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """The blocker line carries :alert-anim: when emoji.list has it."""
    env.post("--style", "bluf", "--yes")

    text = body_text(fake_slack.called("chat.postMessage")[0].body)
    assert ":alert-anim:" in text


def test_ac4_plain_emoji_when_not_listed(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """Without the emoji in emoji.list the blocker line shows ⛔."""
    fake_slack.emoji = {}

    env.post("--style", "bluf", "--yes")

    text = body_text(fake_slack.called("chat.postMessage")[0].body)
    assert ":alert-anim:" not in text
    assert "⛔" in text


def test_ac4_missing_emoji_scope_warns_and_posts(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """emoji.list missing_scope: warn, use ⛔ and still post."""
    fake_slack.script("emoji.list", "missing_scope")

    result = env.post("--style", "bluf", "--yes")

    assert result.exit_code == 0, result.output
    assert "emoji:read missing; using ⛔" in result.stderr
    text = body_text(fake_slack.called("chat.postMessage")[0].body)
    assert ":alert-anim:" not in text
    assert "⛔" in text


def test_ac4_other_emoji_error_exits_9(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """An emoji.list failure other than missing_scope is a Slack error."""
    fake_slack.script("emoji.list", "invalid_auth")

    result = env.post("--style", "bluf", "--yes")

    assert result.exit_code == 9
    assert "Slack emoji.list failed: invalid_auth" in result.stderr


def test_ac4_no_animated_emoji_configured_skips_the_probe(
    env: SlackEnv,
    fake_slack: FakeSlack,
) -> None:
    """Without emoji.blocked_animated there is no emoji.list call."""
    env.write_config(animated=False)

    assert env.post("--style", "bluf", "--yes").exit_code == 0

    assert fake_slack.called("emoji.list") == []


def test_ac5_post_consumes_the_notes(env: SlackEnv) -> None:
    """After ok the inbox is empty and the draft still holds both notes."""
    assert len(env.inbox.read_text(encoding="utf-8").splitlines()) == 2

    assert env.post("--style", "bluf", "--yes").exit_code == 0

    assert env.inbox.read_text(encoding="utf-8") == ""
    assert len(posted_draft(env).notes) == 2


def test_ac5_invalid_blocks_keeps_inbox_and_draft(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """A rejected post exits 9 and changes nothing on disk."""
    fake_slack.script("chat.postMessage", "invalid_blocks")
    before = env.draft_file.read_bytes()

    result = env.post("--style", "bluf", "--yes")

    assert result.exit_code == 9
    assert "Slack chat.postMessage failed: invalid_blocks" in result.stderr
    assert len(env.inbox.read_text(encoding="utf-8").splitlines()) == 2
    assert env.draft_file.read_bytes() == before
    assert posted_draft(env).posted is None


def test_ac6_second_post_exits_8_without_requests(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """A posted draft is refused before any Slack request."""
    assert env.post("--style", "bluf", "--yes").exit_code == 0
    posted = posted_draft(env).posted
    assert posted is not None
    permalink = posted.permalink
    fake_slack.requests.clear()

    result = env.post("--style", "bluf", "--yes")

    assert result.exit_code == 8
    assert f"draft {RUN_ID} already posted {permalink}" in result.stderr
    assert fake_slack.requests == []


def test_ac7_rate_limit_exhausted_exits_9(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """Four 429 answers in a row exit 9 after 4 attempts."""
    fake_slack.script("chat.postMessage", "ratelimited", times=4, status=429, retry_after=0)

    result = env.post("--style", "bluf", "--yes")

    assert result.exit_code == 9
    assert "Slack chat.postMessage failed: HTTP 429 after 4 attempts" in result.stderr
    assert len(fake_slack.called("chat.postMessage")) == 4


def test_ac8_not_in_channel_names_the_channel(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """not_in_channel exits 9 and names the channel."""
    fake_slack.script("chat.postMessage", "not_in_channel")

    result = env.post("--style", "bluf", "--yes")

    assert result.exit_code == 9
    assert "Slack chat.postMessage failed: not_in_channel (C0STANDUP)" in result.stderr


def test_ac9_style_unset_exits_10_without_requests(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """No --style and no render.style: the owner must choose."""
    result = env.post("--yes")

    assert result.exit_code == 10
    assert "render.style unset: pass --style or pick one in preview" in result.stderr
    assert fake_slack.requests == []


def test_ac9_config_style_is_the_default(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """render.style stands in for --style."""
    env.write_config(extra='\n[render]\nstyle = "compact"\n')

    assert env.post("--yes").exit_code == 0

    assert len(fake_slack.called("chat.postMessage")) == 1


def test_ac10_token_unset_exits_2_without_requests(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """No SLACK_USER_TOKEN: exit 2 before any request."""
    result = env.post("--style", "bluf", "--yes", token=None)

    assert result.exit_code == 2
    assert "SLACK_USER_TOKEN is not set" in result.stderr
    assert fake_slack.requests == []


def test_ac10_bot_token_exits_2(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """auth.test with a bot_id: the post must be as the owner."""
    fake_slack.bot_id = "B0BOT"

    result = env.post("--style", "bluf", "--yes")

    assert result.exit_code == 2
    assert "SLACK_USER_TOKEN is a bot token; posts must be as the owner" in result.stderr
    assert fake_slack.called("chat.postMessage") == []


def test_ac13_dry_run_prints_target_and_body_and_sends_nothing(
    env: SlackEnv,
    fake_slack: FakeSlack,
) -> None:
    """Dry-run reads Slack, prints the target and the exact body, writes nothing."""
    before = env.draft_file.read_bytes()
    inbox = env.inbox.read_bytes()

    result = env.post("--style", "bluf", "--yes", dry_run=True)

    assert result.exit_code == 0, result.output
    methods = [request.method for request in fake_slack.requests]
    assert methods == ["auth.test", "conversations.history", "emoji.list"]
    first, _, rest = result.stdout.partition("\n")
    assert first == f"target: thread C0STANDUP thread_ts={PARENT_TS}"
    assert ":alert-anim:" in rest
    assert env.draft_file.read_bytes() == before
    assert env.inbox.read_bytes() == inbox
    printed = json.loads(rest)
    assert env.post("--style", "bluf", "--yes").exit_code == 0
    sent = fake_slack.called("chat.postMessage")[0].body
    assert printed == sent
    assert "[dry-run] would post chat.postMessage to C0STANDUP (thread 1762074000.000100)" in (
        result.stderr
    )


def test_ac13_dry_run_leaves_files_untouched(env: SlackEnv) -> None:
    """Dry-run keeps the inbox and the draft byte-identical."""
    before = (env.draft_file.read_bytes(), env.inbox.read_bytes())

    result = env.post("--style", "bluf", "--yes", dry_run=True)

    assert result.exit_code == 0
    assert (env.draft_file.read_bytes(), env.inbox.read_bytes()) == before
    assert "chat.getPermalink" not in result.stderr


def test_ac13_dry_run_without_parent_exits_10(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """Dry-run with only an old parent fails like a real run."""
    fake_slack.history = [{"ts": OLD_TS, "user": "U0BOT", "text": "Daily standup"}]

    result = env.post("--style", "bluf", "--yes", dry_run=True)

    assert result.exit_code == 10
    assert fake_slack.called("chat.postMessage") == []


def test_ac1_without_yes_the_owner_is_asked(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """Answering no aborts without a post; answering yes posts."""
    declined = env.post("--style", "bluf", stdin="n\n")

    assert declined.exit_code == 1
    assert f"target: thread C0STANDUP thread_ts={PARENT_TS}" in declined.stdout
    assert fake_slack.called("chat.postMessage") == []

    accepted = env.post("--style", "bluf", stdin="y\n")

    assert accepted.exit_code == 0
    assert len(fake_slack.called("chat.postMessage")) == 1


def test_post_unknown_draft_exits_2(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """A run id with no draft file, or not shaped like one, exits 2."""
    missing = env.post("--style", "bluf", "--yes", run_id="20200101-000000")
    malformed = env.post("--style", "bluf", "--yes", run_id="../x")

    assert missing.exit_code == malformed.exit_code == 2
    assert "cannot read draft 20200101-000000" in missing.stderr
    assert fake_slack.requests == []


def test_post_without_slack_table_exits_2(env: SlackEnv) -> None:
    """A config with no [slack] table is a config error."""
    env.write_config(slack=False)

    result = env.post("--style", "bluf", "--yes")

    assert result.exit_code == 2
    assert "no [slack] table" in result.stderr


def test_post_unreachable_slack_exits_9(env: SlackEnv) -> None:
    """A refused connection is a Slack error, not a traceback."""
    env.api_url = "http://127.0.0.1:9/api/"
    env.write_config()

    result = env.post("--style", "bluf", "--yes")

    assert result.exit_code == 9
    assert "Slack auth.test failed: unreachable" in result.stderr


def test_post_parent_match_without_a_rule_exits_2(env: SlackEnv, fake_slack: FakeSlack) -> None:
    """slack.parent_match must set user_id or text_contains."""
    env.write_config(match="")

    result = env.post("--style", "bluf", "--yes")

    assert result.exit_code == 2
    assert "set user_id or text_contains" in result.stderr
    assert fake_slack.requests == []
