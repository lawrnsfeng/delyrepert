import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

from pydantic import BaseModel

from daily_report.compose.models import Draft, StyleName, Target
from daily_report.config.exceptions import BadPermalinkError

_PERMALINK = re.compile(
    r"^https://[A-Za-z0-9.-]+\.slack\.com/archives/([CGD][A-Z0-9]+)/p(\d{16,})(?:\?(.*))?$",
)
_TEXT_KEYS = frozenset({"text", "fallback", "pretext", "title"})
_FIRST_LINE_MAX = 80


def _strings(node: object) -> list[str]:
    """Return every non-empty string under a `_TEXT_KEYS` key in `node`, depth first."""
    if isinstance(node, list):
        return [found for item in node for found in _strings(item)]
    if not isinstance(node, dict):
        return []
    found: list[str] = []
    for key, value in node.items():
        if key in _TEXT_KEYS and isinstance(value, str):
            if value:
                found.append(value)
        else:
            found += _strings(value)
    return found


class SlackMessage(BaseModel):
    """One `conversations.history` message, reduced to what the parent match reads."""

    ts: str
    user: str | None = None
    bot_id: str | None = None
    username: str | None = None
    text: str = ""
    blocks: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []

    @property
    def searchable(self) -> str:
        """Return `text` plus the words in blocks and attachments, one per line."""
        parts = [self.text] if self.text else []
        return "\n".join(parts + _strings(self.blocks) + _strings(self.attachments))

    @property
    def first_line(self) -> str:
        """Return the first non-empty line of `searchable`, clipped to 80 characters."""
        line = next((line for line in self.searchable.splitlines() if line.strip()), "")
        return line if len(line) <= _FIRST_LINE_MAX else line[:_FIRST_LINE_MAX] + "…"

    @property
    def author(self) -> str:
        """Return who sent it: the username, bot id or user id."""
        return self.username or self.bot_id or self.user or "unknown"


class ThreadPin(BaseModel):
    """`thread_pin.json`: the thread the next post replies in, set for one day."""

    channel: str
    ts: str
    permalink: str
    source: str  # "permalink" or the thread name it was resolved from
    day: str  # ISO date, config zone, when pinned


@dataclass(frozen=True)
class Located:
    """Where a reply goes: the pin's thread or a pattern's matched parent."""

    channel: str
    ts: str
    pinned: bool
    source: str  # "pin", "default" or the thread name
    message: SlackMessage | None


@dataclass(frozen=True)
class ResolvedThread:
    """A located thread plus its permalink."""

    located: Located
    permalink: str


@dataclass(frozen=True)
class PostRequest:
    """The one `chat.postMessage` a run sends, resolved but not yet sent."""

    draft: Draft
    style: StyleName
    target: Target
    channel: str
    thread_ts: str | None
    body: dict[str, Any]
    pinned: bool = False

    @property
    def target_line(self) -> str:
        """Return `target: thread C0STANDUP thread_ts=1762074000.000100`."""
        thread = f" thread_ts={self.thread_ts}" if self.thread_ts else ""
        return f"target: {self.target} {self.channel}{thread}"

    @property
    def intent_target(self) -> str:
        """Return `chat.postMessage to C0STANDUP (thread 1762074000.000100)`."""
        thread = f" (thread {self.thread_ts})" if self.thread_ts else ""
        return f"chat.postMessage to {self.channel}{thread}"


def parse_permalink(value: str) -> tuple[str, str]:
    """Return the `(channel, ts)` a Slack message link points at; a reply link: its root."""
    found = _PERMALINK.match(value.strip())
    if found is None:
        raise BadPermalinkError(value)
    channel, digits, query = found.groups()
    roots = parse_qs(query or "").get("thread_ts")
    return channel, roots[0] if roots else f"{digits[:-6]}.{digits[-6:]}"
