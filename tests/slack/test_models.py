import pytest

from daily_report.config.exceptions import BadPermalinkError
from daily_report.slack.models import SlackMessage, parse_permalink

LINK = "https://acme.slack.com/archives/C0STANDUP/p1762074000000100"


def test_searchable_reads_text_blocks_and_attachments() -> None:
    """Bot parents keep their words in attachments and blocks, not in `text`."""
    message = SlackMessage.model_validate(
        {
            "ts": "1.0",
            "text": "",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": "Block words"}},
                {"type": "divider"},
            ],
            "attachments": [
                {"pretext": "Pre", "text": "", "fields": [{"title": "Field", "value": "skip"}]},
            ],
        },
    )

    assert message.searchable == "Block words\nPre\nField"
    assert message.first_line == "Block words"


def test_first_line_skips_blank_lines_and_clips_at_80() -> None:
    """The summary shown to the owner stays one short line."""
    long = SlackMessage(ts="1.0", text="\n  \n" + "x" * 81)

    assert long.first_line == "x" * 80 + "…"
    assert SlackMessage(ts="1.0", text="x" * 80).first_line == "x" * 80
    assert SlackMessage(ts="1.0").first_line == ""


@pytest.mark.parametrize(
    ("fields", "author"),
    [
        ({"username": "standup-bot", "bot_id": "B1", "user": "U1"}, "standup-bot"),
        ({"bot_id": "B1", "user": "U1"}, "B1"),
        ({"user": "U1"}, "U1"),
        ({}, "unknown"),
    ],
)
def test_author_prefers_username_then_bot_then_user(fields: dict[str, str], author: str) -> None:
    """The sender shown for a matched parent."""
    assert SlackMessage.model_validate({"ts": "1.0", **fields}).author == author


def test_a_permalink_names_channel_and_ts() -> None:
    """The last six digits are the microseconds; surrounding spaces are ignored."""
    assert parse_permalink(f"  {LINK} ") == ("C0STANDUP", "1762074000.000100")


def test_a_reply_link_pins_its_thread_root() -> None:
    """A link to a reply carries the root in `thread_ts`."""
    reply = LINK.replace("000100", "000900") + "?thread_ts=1762074000.000100&cid=C0STANDUP"

    assert parse_permalink(reply) == ("C0STANDUP", "1762074000.000100")


@pytest.mark.parametrize(
    "value",
    ["not-a-link", "https://example.com/archives/C0STANDUP/p1762074000000100", LINK[:-4]],
)
def test_anything_else_is_not_a_permalink(value: str) -> None:
    """Only Slack message links are accepted."""
    with pytest.raises(BadPermalinkError, match="not a Slack message link"):
        parse_permalink(value)
