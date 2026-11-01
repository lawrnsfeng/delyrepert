import json

from daily_report.compose.models import STYLE_NAMES
from daily_report.compose.render import Renderer
from daily_report.jira.fake import FakeJira
from daily_report.slack.fake import FakeSlack
from daily_report.slack_app.blocks import preview_body
from tests.compose.helpers import r1_draft
from tests.service.conftest import RUN_ID, Clock, ServiceEnv
from tests.slack_app.helpers import action_ids, as_text, dm_requests, initial, standup_posts


def test_ac1_run_and_serve_without_an_owner_exit_2(env: ServiceEnv) -> None:
    """No owner_slack_user_id: both commands exit 2 naming it."""
    text = env.config.read_text(encoding="utf-8")
    env.config.write_text(text.replace('owner_slack_user_id = "U0OWNER"\n', ""), encoding="utf-8")

    for command in ("run", "serve"):
        result = env.cli(command)

        assert result.exit_code == 2
        assert "owner_slack_user_id" in result.output
    assert not (env.state / "runs").exists()


def test_ac2_pending_scheduled_run_sends_one_dm(env: ServiceEnv, fake_slack: FakeSlack) -> None:
    """One conversations.open, one DM with three buttons, nothing in the channel, dm stored."""
    assert env.cli("run", "--scheduled").exit_code == 0

    (opened,) = fake_slack.called("conversations.open")
    assert opened.body["users"] == "U0OWNER"
    (dm,) = dm_requests(fake_slack)
    assert action_ids(dm.body["blocks"])[-3:] == [
        "standup_post",
        "standup_edit_notes",
        "standup_skip",
    ]
    assert standup_posts(fake_slack) == []
    record = json.loads(env.record())
    assert record["dm"]["channel"] == "D0U0OWNER"
    assert record["dm"]["ts"]


def test_ac2_a_manual_run_sends_no_dm(env: ServiceEnv, fake_slack: FakeSlack) -> None:
    """The owner at the terminal gets no DM."""
    assert env.cli("run").exit_code == 0

    assert fake_slack.called("conversations.open") == []


def test_ac2_a_lost_dm_is_logged_and_the_run_stays_pending(
    env: ServiceEnv,
    fake_slack: FakeSlack,
) -> None:
    """Slack refusing the DM leaves exit 0 and a pending record without dm."""
    fake_slack.script("conversations.open", "missing_scope")

    result = env.cli("run", "--scheduled")

    assert result.exit_code == 0
    assert "approval DM failed" in result.stderr
    record = json.loads(env.record())
    assert record["status"] == "pending"
    assert record["dm"] is None


def test_ac3_dm_shows_the_gallery_while_render_style_is_unset(
    env: ServiceEnv,
    fake_slack: FakeSlack,
) -> None:
    """Seven styles, a style select preset to bluf, a target select preset to post.target."""
    env.config.write_text(
        'post = { target = "channel" }\n' + env.config.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert env.cli("run", "--scheduled").exit_code == 0

    blocks = dm_requests(fake_slack)[0].body["blocks"]
    text = as_text(blocks)
    assert all(f"*{style}*" in text for style in STYLE_NAMES)
    assert initial(blocks, "standup_style") == "bluf"
    assert initial(blocks, "standup_target") == "channel"


def test_ac3_dm_shows_only_the_chosen_style(env: ServiceEnv, fake_slack: FakeSlack) -> None:
    """With render.style set there is one style and no style select."""
    env.config.write_text(
        'render = { style = "ytb" }\n' + env.config.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert env.cli("run", "--scheduled").exit_code == 0

    blocks = dm_requests(fake_slack)[0].body["blocks"]
    assert "*bluf*" not in as_text(blocks)
    assert initial(blocks, "standup_style") is None
    assert initial(blocks, "standup_target") == "thread"


def test_ac13_a_failed_scheduled_run_sends_a_failure_dm(
    env: ServiceEnv,
    fake_slack: FakeSlack,
    fake_jira: FakeJira,
) -> None:
    """Jira 401: one DM `❌ Run <id> failed: …` and the exit stays 3."""
    fake_jira.script("/rest/api/3/search/jql", 401)

    result = env.cli("run", "--scheduled")

    assert result.exit_code == 3
    (dm,) = dm_requests(fake_slack)
    assert str(dm.body["text"]).startswith(f"❌ Run {RUN_ID} failed: Jira rejected credentials")


def test_ac13_a_lost_failure_dm_is_logged_and_the_exit_stays_3(
    env: ServiceEnv,
    fake_slack: FakeSlack,
    fake_jira: FakeJira,
) -> None:
    """The DM call answering HTTP 500: logged, exit 3."""
    fake_jira.script("/rest/api/3/search/jql", 401)
    fake_slack.script("chat.postMessage", "fatal_error", status=500)

    result = env.cli("run", "--scheduled")

    assert result.exit_code == 3
    assert "failure DM failed" in result.stderr


def test_ac13_a_failed_manual_run_sends_no_dm(
    env: ServiceEnv,
    fake_slack: FakeSlack,
    fake_jira: FakeJira,
) -> None:
    """The terminal already shows the error."""
    fake_jira.script("/rest/api/3/search/jql", 401)

    assert env.cli("run").exit_code == 3

    assert fake_slack.called("conversations.open") == []


def test_ac15_dry_run_scheduled_run_prints_the_dm_and_writes_nothing(
    env: ServiceEnv,
    fake_slack: FakeSlack,
    clock: Clock,
) -> None:
    """Both Slack writes are only reported."""
    del clock

    result = env.cli("run", "--scheduled", dry_run=True)

    assert result.exit_code == 0, result.output
    assert "would conversations.open U0OWNER" in result.stderr
    assert "would chat.postMessage {" in result.stderr
    assert "standup_post" in result.stderr
    assert [r.method for r in fake_slack.requests if r.method != "auth.test"] == []


def test_ac3_a_long_single_style_is_shortened_to_fit_slack() -> None:
    """Past 44 blocks the DM cuts the draft, says so, and keeps the three buttons."""
    base = r1_draft()
    items = [base.items[0].model_copy(update={"id": f"PAY-{n}"}) for n in range(1, 20)]
    draft = base.model_copy(update={"items": items})

    body = preview_body(
        draft,
        Renderer(display_name=None, blocked_animated=None),
        chosen="cards",
        target="thread",
    )

    blocks = body["blocks"]
    assert len(blocks) <= 50
    assert "shortened" in as_text(blocks)
    assert action_ids(blocks)[-3:] == ["standup_post", "standup_edit_notes", "standup_skip"]
