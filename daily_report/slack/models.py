from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from daily_report.compose.models import Draft, StyleName, Target


class SlackMessage(BaseModel):
    """One `conversations.history` message, reduced to what the parent match reads."""

    ts: str
    user: str | None = None
    text: str = ""


@dataclass(frozen=True)
class PostRequest:
    """The one `chat.postMessage` a run sends, resolved but not yet sent."""

    draft: Draft
    style: StyleName
    target: Target
    channel: str
    thread_ts: str | None
    body: dict[str, Any]

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
