import json
from typing import Any

from daily_report.slack.fake import FakeSlack, RecordedRequest

RUN_ID = "20261102-090500"


def dm_requests(fake: FakeSlack) -> list[RecordedRequest]:
    """Return the chat.postMessage calls that went to the owner's DM."""
    return [r for r in fake.called("chat.postMessage") if str(r.body["channel"]).startswith("D0")]


def standup_posts(fake: FakeSlack) -> list[RecordedRequest]:
    """Return the chat.postMessage calls that went to the standup channel."""
    return [r for r in fake.called("chat.postMessage") if r.body["channel"] == "C0STANDUP"]


def replies(fake: FakeSlack) -> list[str]:
    """Return the text of every response_url reply, in order."""
    return [str(r.body["text"]) for r in fake.called("/response_url")]


def action_ids(blocks: list[dict[str, Any]]) -> list[str]:
    """Return every interactive element's action_id in `blocks`."""
    return [
        element["action_id"]
        for block in blocks
        if block["type"] == "actions"
        for element in block["elements"]
    ]


def initial(blocks: list[dict[str, Any]], action_id: str) -> str | None:
    """Return the preset value of a select, or `None` when there is no such select."""
    for block in blocks:
        for element in block.get("elements", []):
            if element.get("action_id") == action_id:
                return str(element["initial_option"]["value"])
    return None


def as_text(value: object) -> str:
    """Return `value` as JSON text for substring checks."""
    return json.dumps(value, ensure_ascii=False)
