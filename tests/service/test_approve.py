import json

import pytest

from daily_report.slack.fake import FakeSlack
from tests.service.conftest import RUN_ID, Clock, ServiceEnv


def approve_args(*extra: str) -> list[str]:
    """Return the arguments of an approve of the test run."""
    return [RUN_ID, *extra]


def status_of(env: ServiceEnv) -> dict[str, object]:
    """Return the run record as a dict."""
    return json.loads(env.record())


@pytest.fixture
def pending(env: ServiceEnv) -> ServiceEnv:
    """Return an env holding a pending run."""
    assert env.cli("run").exit_code == 0
    return env


def test_ac5_approve_posts_once_and_records_where(
    pending: ServiceEnv,
    fake_slack: FakeSlack,
) -> None:
    """Posted: one chat.postMessage, the record holds channel, ts and permalink."""
    result = pending.cli("approve", *approve_args("--style", "ytb", "--target", "channel"))

    assert result.exit_code == 0, result.output
    assert len(fake_slack.called("chat.postMessage")) == 1
    record = status_of(pending)
    assert record["status"] == "posted"
    posted = record["posted"]
    assert isinstance(posted, dict)
    assert posted["channel"] == "C0STANDUP"
    assert result.stdout.strip() == posted["permalink"]


def test_ac5_flags_default_to_the_config(pending: ServiceEnv, fake_slack: FakeSlack) -> None:
    """Without flags the style is render.style and the target post.target."""
    text = pending.config.read_text(encoding="utf-8")
    pending.config.write_text(
        'render = { style = "bluf" }\npost = { target = "channel" }\n' + text,
        encoding="utf-8",
    )

    result = pending.cli("approve", *approve_args())

    assert result.exit_code == 0, result.output
    (request,) = fake_slack.called("chat.postMessage")
    assert "thread_ts" not in request.body


def test_ac5_no_style_exits_10(pending: ServiceEnv, fake_slack: FakeSlack) -> None:
    """Neither --style nor render.style: the owner must choose."""
    result = pending.cli("approve", *approve_args())

    assert result.exit_code == 10
    assert "render.style unset" in result.output
    assert fake_slack.requests == []


def test_ac5_no_parent_thread_exits_10_and_stays_pending(pending: ServiceEnv) -> None:
    """No standup thread today: exit 10, the run stays pending with the error."""
    result = pending.cli("approve", *approve_args("--style", "ytb"))

    assert result.exit_code == 10
    record = status_of(pending)
    assert record["status"] == "pending"
    assert "no standup thread found" in str(record["error"])


def test_ac5_slack_error_exits_9_and_stays_pending(
    pending: ServiceEnv,
    fake_slack: FakeSlack,
) -> None:
    """not_in_channel: exit 9, the run stays pending with the error."""
    fake_slack.script("chat.postMessage", "not_in_channel")

    result = pending.cli("approve", *approve_args("--style", "ytb", "--target", "channel"))

    assert result.exit_code == 9
    record = status_of(pending)
    assert record["status"] == "pending"
    assert "not_in_channel" in str(record["error"])


def test_ac5_a_missing_draft_exits_2_and_stays_pending(pending: ServiceEnv) -> None:
    """A run whose draft is gone cannot be posted."""
    (pending.state / "drafts" / f"{RUN_ID}.json").unlink()

    result = pending.cli("approve", *approve_args("--style", "ytb", "--target", "channel"))

    assert result.exit_code == 2
    assert status_of(pending)["status"] == "pending"


@pytest.mark.parametrize("run_id", ["20200101-000000", "../escape"])
def test_ac5_unknown_run_exits_8_without_a_call(
    pending: ServiceEnv,
    fake_slack: FakeSlack,
    run_id: str,
) -> None:
    """An unknown id exits 8 naming its status."""
    result = pending.cli("approve", run_id, "--style", "ytb")

    assert result.exit_code == 8
    assert f"run {run_id} is unknown, not pending" in result.output
    assert fake_slack.requests == []


def test_ac5_posted_run_exits_8_without_a_second_call(
    pending: ServiceEnv,
    fake_slack: FakeSlack,
) -> None:
    """Approving twice posts once."""
    args = approve_args("--style", "ytb", "--target", "channel")
    assert pending.cli("approve", *args).exit_code == 0

    result = pending.cli("approve", *args)

    assert result.exit_code == 8
    assert f"run {RUN_ID} is posted, not pending" in result.output
    assert len(fake_slack.called("chat.postMessage")) == 1


def test_ac6_approve_after_the_cutoff_expires_the_draft(
    pending: ServiceEnv,
    clock: Clock,
    fake_slack: FakeSlack,
) -> None:
    """Created 09:05, approved 12:01 with cutoff 12:00: exit 8 and expired."""
    clock.now = clock.now.replace(hour=12, minute=1)

    result = pending.cli("approve", *approve_args("--style", "ytb", "--target", "channel"))

    assert result.exit_code == 8
    assert f"draft {RUN_ID} expired at 12:00" in result.output
    assert status_of(pending)["status"] == "expired"
    assert fake_slack.requests == []


def test_ac6_a_draft_made_after_the_cutoff_is_approvable(
    env: ServiceEnv,
    clock: Clock,
) -> None:
    """Only the next run expires a draft created after the cutoff."""
    clock.now = clock.now.replace(hour=13, minute=0)
    assert env.cli("run").exit_code == 0
    clock.now = clock.now.replace(hour=14)

    result = env.cli("approve", "20261102-130000", "--style", "ytb", "--target", "channel")

    assert result.exit_code == 0, result.output
