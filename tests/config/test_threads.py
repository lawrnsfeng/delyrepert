import pytest
from pydantic import ValidationError

from daily_report.config.exceptions import UnknownThreadError
from daily_report.config.models import ParentMatch, SlackConfig, ThreadSpec

WEEKLY = {"parent_match": {"text_contains": "Weekly"}, "lookback_days": 7}


def slack(**extra: object) -> SlackConfig:
    """Return a `[slack]` table with the default pattern plus `extra`."""
    base = {"channel": "C0STANDUP", "parent_match": {"user_id": "U0BOT"}}
    return SlackConfig.model_validate(base | extra)


def test_a_bot_id_alone_is_a_rule() -> None:
    """A bot parent can be matched by its bot id only."""
    assert ParentMatch(bot_id="B1").rules() == {"bot_id": "B1"}


def test_a_match_needs_at_least_one_rule() -> None:
    """An empty parent_match would match every message."""
    with pytest.raises(ValidationError, match="set user_id, bot_id, username, text_contains"):
        ParentMatch()


def test_text_regex_must_compile() -> None:
    """A broken regex fails at load time, not at post time."""
    with pytest.raises(ValidationError, match="text_regex is not a valid regex"):
        ParentMatch(text_regex="(unclosed")


@pytest.mark.parametrize("name", ["default", "Bad Name", "1st"])
def test_thread_names_are_slugs_and_not_default(name: str) -> None:
    """`default` is the top-level pattern; other names must be lowercase slugs."""
    with pytest.raises(ValidationError, match=f"thread name '{name}' must be lowercase"):
        slack(threads={name: WEEKLY})


def test_lookback_is_capped_at_seven_days() -> None:
    """History is one page, so the window stays within a week."""
    with pytest.raises(ValidationError, match="lookback_days"):
        slack(lookback_days=8)


def test_spec_resolves_default_and_named_threads() -> None:
    """A named thread inherits slack.channel unless it sets its own."""
    config = slack(
        lookback_days=1,
        threads={"weekly": WEEKLY, "ops": {**WEEKLY, "channel": "C0OPS"}},
    )

    assert config.spec(None) == config.spec("default")
    assert config.spec(None) == ThreadSpec("default", "C0STANDUP", ParentMatch(user_id="U0BOT"), 1)
    assert config.spec("weekly") == ThreadSpec(
        "weekly",
        "C0STANDUP",
        ParentMatch(text_contains="Weekly"),
        7,
    )
    assert config.spec("ops").channel == "C0OPS"


def test_an_unknown_thread_names_the_known_ones() -> None:
    """The error lists what the owner can pick."""
    with pytest.raises(UnknownThreadError) as caught:
        slack(threads={"weekly": WEEKLY}).spec("nope")

    assert str(caught.value) == "unknown thread nope; known: default, weekly"
