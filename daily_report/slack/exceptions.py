from dataclasses import dataclass

from daily_report.exc import TemplatedError


class SlackError(TemplatedError):
    """Base for Slack posting errors."""


@dataclass
class SlackCallError(SlackError):
    """A Slack Web API call failed (exit 9); `error` is Slack's code, `detail` adds context."""

    TEMPLATE = "Slack {method} failed: {error}{detail}"
    method: str
    error: str
    detail: str = ""


@dataclass
class BotTokenError(SlackError):
    """`SLACK_USER_TOKEN` belongs to a bot, so a post would not be in the owner's name."""

    TEMPLATE = "SLACK_USER_TOKEN is a bot token; posts must be as the owner"


@dataclass
class NoParentError(SlackError):
    """No message in the channel since local midnight matches `slack.parent_match`."""

    TEMPLATE = "no standup thread found today in #{channel}; rerun with --target channel"
    channel: str


@dataclass
class AlreadyPostedError(SlackError):
    """The draft was posted before; a run posts exactly once."""

    TEMPLATE = "draft {run_id} already posted {permalink}"
    run_id: str
    permalink: str


@dataclass
class StyleUnsetError(SlackError):
    """Neither `--style` nor `render.style` names a style."""

    TEMPLATE = "render.style unset: pass --style or pick one in preview"
