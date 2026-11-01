import datetime as dt
import functools
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Self

import aiohttp
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse

from daily_report.compose.models import Draft, Posted, StyleName, Target
from daily_report.compose.render import Renderer
from daily_report.compose.services import DraftStore
from daily_report.config.exceptions import SlackUnsetError
from daily_report.config.models import Config, ParentMatch
from daily_report.effects.models import Intent
from daily_report.effects.services import Effects
from daily_report.logger import get_logger
from daily_report.notes.services import NotesInbox
from daily_report.retry import RetriesExhaustedError, RetryableError, RetryPolicy
from daily_report.slack.exceptions import (
    AlreadyPostedError,
    BotTokenError,
    NoParentError,
    SlackCallError,
)
from daily_report.slack.models import PostRequest, SlackMessage

_TOO_MANY_REQUESTS = 429
_SERVER_ERROR = 500
_CHANNEL_ERRORS = frozenset({"not_in_channel", "channel_not_found", "is_archived"})
_HISTORY_LIMIT = 999


def utc_now() -> dt.datetime:
    """Return the current instant (tests replace it to pin "today")."""
    return dt.datetime.now(tz=dt.UTC)


class SlackClient:
    """The Slack Web API calls daily-report makes, with collector D9 retries and error mapping."""

    def __init__(self, web: AsyncWebClient, retry: RetryPolicy) -> None:
        """Call Slack through `web`, retrying 429 and 5xx with `retry`."""
        self._web = web
        self._retry = retry

    async def auth_test(self) -> AsyncSlackResponse:
        """Return who the token belongs to."""
        return await self._call("auth.test", self._web.auth_test)

    async def history(self, channel: str, *, oldest: float) -> list[SlackMessage]:
        """Return the channel's top-level messages posted after `oldest` (epoch seconds)."""
        # shortcut: one page of up to 999 messages since midnight; follow cursors if a
        # standup channel ever gets busier than that in a day.
        request = functools.partial(
            self._web.conversations_history,
            channel=channel,
            oldest=f"{oldest:.6f}",
            limit=_HISTORY_LIMIT,
        )
        response = await self._call("conversations.history", request, channel=channel)
        return [SlackMessage.model_validate(message) for message in response["messages"]]

    async def emoji_names(self) -> set[str]:
        """Return the workspace's custom emoji names."""
        return set((await self._call("emoji.list", self._web.emoji_list))["emoji"])

    async def post_message(self, body: dict[str, object]) -> tuple[str, str]:
        """Send `body` with `chat.postMessage`; return the message's channel and ts."""
        request = functools.partial(self._web.api_call, "chat.postMessage", json=body)
        response = await self._call("chat.postMessage", request, channel=str(body["channel"]))
        return response["channel"], response["ts"]

    async def permalink(self, channel: str, ts: str) -> str:
        """Return the message's permalink."""
        request = functools.partial(self._web.chat_getPermalink, channel=channel, message_ts=ts)
        return (await self._call("chat.getPermalink", request, channel=channel))["permalink"]

    async def open_dm(self, user: str) -> str:
        """Open (or find) the bot's DM with `user`; return its channel ID."""
        request = functools.partial(self._web.conversations_open, users=user)
        return (await self._call("conversations.open", request))["channel"]["id"]

    async def update_message(self, channel: str, ts: str, body: dict[str, object]) -> None:
        """Replace the text and blocks of the message `ts` in `channel` with `body`."""
        request = functools.partial(
            self._web.api_call,
            "chat.update",
            json={"channel": channel, "ts": ts, **body},
        )
        await self._call("chat.update", request, channel=channel)

    async def open_view(self, trigger_id: str, view: dict[str, object]) -> None:
        """Open the modal `view` for the interaction `trigger_id`."""
        request = functools.partial(self._web.views_open, trigger_id=trigger_id, view=view)
        await self._call("views.open", request)

    async def _call(
        self,
        method: str,
        request: Callable[[], Awaitable[AsyncSlackResponse]],
        *,
        channel: str = "",
    ) -> AsyncSlackResponse:
        async def attempt() -> AsyncSlackResponse:
            try:
                return await request()
            except SlackApiError as exc:
                response = exc.response
                status = response.status_code
                if status == _TOO_MANY_REQUESTS or status >= _SERVER_ERROR:
                    retry_after = response.headers.get("Retry-After")
                    raise RetryableError.from_response(status, retry_after) from exc
                error = str(response.get("error", f"HTTP {status}"))
                detail = f" ({channel})" if error in _CHANNEL_ERRORS else ""
                raise SlackCallError(method, error, detail) from exc
            except aiohttp.ClientError as exc:
                raise SlackCallError(method, f"unreachable ({exc})") from exc

        try:
            return await self._retry.run(attempt)
        except RetriesExhaustedError as exc:
            raise SlackCallError(method, str(exc)) from exc


class ParentFinder:
    """Finds today's standup parent message: the latest match since local midnight."""

    def __init__(self, client: SlackClient, match: ParentMatch, zone: dt.tzinfo) -> None:
        """Match messages with `match`; "today" starts at midnight in `zone`."""
        self._client = client
        self._match = match
        self._zone = zone

    async def find(self, channel: str, now: dt.datetime) -> str:
        """Return the parent's ts; raise `NoParentError` when nothing matches today."""
        midnight = now.astimezone(self._zone).replace(hour=0, minute=0, second=0, microsecond=0)
        messages = await self._client.history(channel, oldest=midnight.timestamp())
        found = [message.ts for message in messages if self._matches(message)]
        if not found:
            raise NoParentError(channel)
        return max(found, key=float)

    def _matches(self, message: SlackMessage) -> bool:
        rule = self._match
        return (rule.user_id is None or message.user == rule.user_id) and (
            rule.text_contains is None or rule.text_contains in message.text
        )


class EmojiProbe:
    """Checks whether the configured animated blocker emoji exists in the workspace."""

    def __init__(self, client: SlackClient, name: str | None) -> None:
        """Probe for custom emoji `name` (no probe when it is unset)."""
        self._client = client
        self._name = name

    async def available(self) -> bool:
        """Return whether `emoji.list` lists the emoji; without `emoji:read`, warn and say no."""
        if self._name is None:
            return False
        try:
            names = await self._client.emoji_names()
        except SlackCallError as exc:
            if exc.error != "missing_scope":
                raise
            get_logger().warning("emoji:read missing; using ⛔")
            return False
        return self._name in names


class PostService:
    """Posts a saved draft to Slack exactly once, as the owner."""

    def __init__(  # noqa: PLR0913 - the collaborators a post needs
        self,
        *,
        client: SlackClient,
        store: DraftStore,
        inbox: NotesInbox,
        renderer: Renderer,
        parents: ParentFinder,
        emoji: EmojiProbe,
        effects: Effects,
        channel: str,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        """Post with these collaborators into `channel`."""
        self._client = client
        self._store = store
        self._inbox = inbox
        self._renderer = renderer
        self._parents = parents
        self._emoji = emoji
        self._effects = effects
        self._channel = channel
        self._now = now

    @classmethod
    def from_config(
        cls,
        settings: Config,
        *,
        token: str,
        state_dir: Path,
        effects: Effects,
        retry: RetryPolicy | None = None,
    ) -> Self:
        """Build the service from the config file, the user token and the state dir."""
        slack = settings.slack
        if slack is None:
            raise SlackUnsetError
        client = SlackClient(
            AsyncWebClient(token=token, base_url=slack.api_url),
            retry or RetryPolicy(),
        )
        return cls(
            client=client,
            store=DraftStore(state_dir, effects),
            inbox=NotesInbox(state_dir, effects),
            renderer=Renderer(
                display_name=settings.display_name,
                blocked_animated=settings.emoji.blocked_animated,
            ),
            parents=ParentFinder(client, slack.parent_match, settings.zone),
            emoji=EmojiProbe(client, settings.emoji.blocked_animated),
            effects=effects,
            channel=slack.channel,
        )

    async def post(self, run_id: str, *, style: StyleName, target: Target) -> Posted | None:
        """Post the draft of `run_id`; return where it landed (`None` under dry-run)."""
        return await self.send(await self.prepare(run_id, style=style, target=target))

    async def prepare(
        self,
        run_id: str,
        *,
        style: StyleName,
        target: Target,
        draft: Draft | None = None,
    ) -> PostRequest:
        """Check the draft and the token, find the parent and render the request body.

        `draft` stands in for the saved file (a dry-run preview never saves its edits).
        """
        draft = draft or self._store.load(run_id)
        if draft.posted:
            raise AlreadyPostedError(run_id, draft.posted.permalink)
        if (await self._client.auth_test()).get("bot_id"):
            raise BotTokenError
        thread_ts = (
            None
            if target == "channel"
            else await self._parents.find(self._channel, (self._now or utc_now)())
        )
        payload = self._renderer.render(
            draft,
            style,
            animated_available=await self._emoji.available(),
        )
        body: dict[str, object] = {"channel": self._channel}
        if thread_ts:
            body["thread_ts"] = thread_ts
        if target == "thread_broadcast":
            body["reply_broadcast"] = True
        body |= payload.model_dump(mode="json")
        return PostRequest(draft, style, target, self._channel, thread_ts, body)

    async def send(self, request: PostRequest) -> Posted | None:
        """Send `request`, then record `posted` in the draft and consume its notes."""
        posted = await self._effects.perform(
            Intent("post", request.intent_target),
            functools.partial(self._deliver, request),
        )
        if posted is None:
            return None
        update = {"posted": posted, "style": request.style, "target": request.target}
        await self._store.save(request.draft.model_copy(update=update))
        await self._inbox.consume(request.draft.notes)
        return posted

    async def _deliver(self, request: PostRequest) -> Posted:
        channel, ts = await self._client.post_message(request.body)
        try:
            permalink = await self._client.permalink(channel, ts)
        except SlackCallError as exc:
            # The message is out: record it anyway so a rerun cannot post it twice.
            get_logger().warning("posted without a permalink", reason=str(exc))
            permalink = ""
        return Posted(channel=channel, ts=ts, permalink=permalink)
