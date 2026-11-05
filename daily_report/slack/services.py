import datetime as dt
import functools
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Self

import aiohttp
from pydantic import ValidationError
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse

from daily_report.compose.models import Draft, Posted, StyleName, Target
from daily_report.compose.render import Renderer
from daily_report.compose.services import DraftStore
from daily_report.config.exceptions import InvalidPinError, SlackUnsetError
from daily_report.config.models import Config, SlackConfig, ThreadSpec
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
from daily_report.slack.models import (
    Located,
    PostRequest,
    ResolvedThread,
    SlackMessage,
    ThreadPin,
    parse_permalink,
)

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
        # shortcut: one page of up to 999 messages, which is why lookback_days is capped at 7;
        # follow cursors if a standup channel ever gets busier than that.
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


def _slack_config(settings: Config) -> SlackConfig:
    """Return the `[slack]` table; `SlackUnsetError` without one."""
    if settings.slack is None:
        raise SlackUnsetError
    return settings.slack


def _slack_client(slack: SlackConfig, token: str | None, retry: RetryPolicy | None) -> SlackClient:
    """Return a client for the configured Slack API."""
    return SlackClient(AsyncWebClient(token=token, base_url=slack.api_url), retry or RetryPolicy())


class ParentFinder:
    """Finds a thread pattern's parent message: the latest match in its lookback window."""

    def __init__(self, client: SlackClient, spec: ThreadSpec, zone: dt.tzinfo) -> None:
        """Match messages with `spec`; the window starts at midnight in `zone`, minus lookback."""
        self._client = client
        self._spec = spec
        self._zone = zone

    async def find(self, now: dt.datetime) -> SlackMessage:
        """Return the newest match; raise `NoParentError` when nothing matches."""
        midnight = now.astimezone(self._zone).replace(hour=0, minute=0, second=0, microsecond=0)
        start = midnight - dt.timedelta(days=self._spec.lookback_days)
        messages = await self._client.history(self._spec.channel, oldest=start.timestamp())
        found = [message for message in messages if self._matches(message)]
        if not found:
            raise NoParentError(self._spec.channel, self._spec.name, start.date().isoformat())
        return max(found, key=lambda message: float(message.ts))

    def _matches(self, message: SlackMessage) -> bool:
        rule = self._spec.match
        return bool(
            (rule.user_id is None or message.user == rule.user_id)
            and (rule.bot_id is None or message.bot_id == rule.bot_id)
            and (rule.username is None or message.username == rule.username)
            and (rule.text_contains is None or rule.text_contains in message.searchable)
            and (rule.text_regex is None or re.search(rule.text_regex, message.searchable)),
        )


class PinStore:
    """`<state_dir>/thread_pin.json`; writes go through Effects."""

    def __init__(self, state_dir: Path, effects: Effects) -> None:
        """Keep the thread pin under `state_dir`."""
        self._path = state_dir / "thread_pin.json"
        self._effects = effects

    def current(self) -> ThreadPin | None:
        """Return the pin, or `None` when there is none."""
        if not self._path.is_file():
            return None
        try:
            return ThreadPin.model_validate_json(self._path.read_bytes())
        except ValidationError as exc:
            raise InvalidPinError(str(self._path)) from exc

    async def set(self, pin: ThreadPin) -> None:
        """Write the pin."""
        text = pin.model_dump_json(indent=2) + "\n"

        async def write() -> None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(text, encoding="utf-8")

        await self._effects.perform(Intent("write", "thread_pin.json"), write)

    async def clear(self) -> None:
        """Delete the pin."""

        async def delete() -> None:
            self._path.unlink(missing_ok=True)

        await self._effects.perform(Intent("delete", "thread_pin.json"), delete)


class ThreadService:
    """Finds, shows and pins the thread the next post replies in."""

    def __init__(
        self,
        *,
        client: SlackClient,
        slack: SlackConfig,
        zone: dt.tzinfo,
        pins: PinStore,
    ) -> None:
        """Look `slack`'s patterns up through `client`; "today" is in `zone`; pins in `pins`."""
        self._client = client
        self._slack = slack
        self._zone = zone
        self._pins = pins

    @classmethod
    def from_config(
        cls,
        settings: Config,
        *,
        token: str | None,
        state_dir: Path,
        effects: Effects,
        retry: RetryPolicy | None = None,
    ) -> Self:
        """Build the service; `token` may be `None` for work that makes no Slack call."""
        slack = _slack_config(settings)
        return cls(
            client=_slack_client(slack, token, retry),
            slack=slack,
            zone=settings.zone,
            pins=PinStore(state_dir, effects),
        )

    def spec(self, name: str | None) -> ThreadSpec:
        """Return the pattern `name`; `UnknownThreadError` for a name not in the config."""
        return self._slack.spec(name)

    async def locate(self, name: str | None, now: dt.datetime | None = None) -> Located:
        """Return today's pin (only when no name is given), else the pattern's parent."""
        now = now or utc_now()
        pin = self._pins.current() if name is None else None
        if pin and pin.day == self._today(now):
            return Located(pin.channel, pin.ts, pinned=True, source="pin", message=None)
        spec = self.spec(name)
        message = await ParentFinder(self._client, spec, self._zone).find(now)
        return Located(spec.channel, message.ts, pinned=False, source=spec.name, message=message)

    async def show(self, name: str | None, now: dt.datetime | None = None) -> ResolvedThread:
        """Locate the thread and return it with its permalink."""
        located = await self.locate(name, now)
        pin = self._pins.current() if located.pinned else None
        permalink = (
            pin.permalink if pin else await self._client.permalink(located.channel, located.ts)
        )
        return ResolvedThread(located, permalink)

    async def pin_permalink(self, value: str, now: dt.datetime | None = None) -> ResolvedThread:
        """Pin the thread a Slack message link points at, for today; no Slack call."""
        channel, ts = parse_permalink(value)
        link = value.strip()
        day = self._today(now or utc_now())
        await self._pins.set(
            ThreadPin(channel=channel, ts=ts, permalink=link, source="permalink", day=day),
        )
        return ResolvedThread(Located(channel, ts, pinned=True, source="pin", message=None), link)

    async def pin_named(self, name: str | None, now: dt.datetime | None = None) -> ResolvedThread:
        """Pin the parent the pattern `name` finds now, for today (an existing pin is ignored)."""
        now = now or utc_now()
        spec = self.spec(name)
        resolved = await self.show(spec.name, now)
        pin = ThreadPin(
            channel=resolved.located.channel,
            ts=resolved.located.ts,
            permalink=resolved.permalink,
            source=spec.name,
            day=self._today(now),
        )
        await self._pins.set(pin)
        return resolved

    async def clear_pin(self) -> bool:
        """Delete the pin; return whether there was one."""
        if self._pins.current() is None:
            return False
        await self._pins.clear()
        return True

    def _today(self, now: dt.datetime) -> str:
        return now.astimezone(self._zone).date().isoformat()


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
        threads: ThreadService,
        thread: str | None,
        emoji: EmojiProbe,
        effects: Effects,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        """Post with these collaborators; `thread` names the pattern (`None`: pin or default)."""
        self._client = client
        self._store = store
        self._inbox = inbox
        self._renderer = renderer
        self._threads = threads
        self._thread = thread
        self._emoji = emoji
        self._effects = effects
        self._now = now

    @classmethod
    def from_config(  # noqa: PLR0913 - the config plus the post's own choices
        cls,
        settings: Config,
        *,
        token: str,
        state_dir: Path,
        effects: Effects,
        retry: RetryPolicy | None = None,
        thread: str | None = None,
    ) -> Self:
        """Build the service from the config file, the user token and the state dir.

        An unknown `thread` raises `UnknownThreadError` here, before any Slack call.
        """
        slack = _slack_config(settings)
        client = _slack_client(slack, token, retry)
        threads = ThreadService(
            client=client,
            slack=slack,
            zone=settings.zone,
            pins=PinStore(state_dir, effects),
        )
        threads.spec(thread)
        return cls(
            client=client,
            store=DraftStore(state_dir, effects),
            inbox=NotesInbox(state_dir, effects),
            renderer=Renderer(
                display_name=settings.display_name,
                blocked_animated=settings.emoji.blocked_animated,
            ),
            threads=threads,
            thread=thread,
            emoji=EmojiProbe(client, settings.emoji.blocked_animated),
            effects=effects,
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
        if target == "channel":  # a pin never applies to a top-level post
            channel, thread_ts, pinned = self._threads.spec(self._thread).channel, None, False
        else:
            located = await self._threads.locate(self._thread, (self._now or utc_now)())
            channel, thread_ts, pinned = located.channel, located.ts, located.pinned
        payload = self._renderer.render(
            draft,
            style,
            animated_available=await self._emoji.available(),
        )
        body: dict[str, object] = {"channel": channel}
        if thread_ts:
            body["thread_ts"] = thread_ts
        if target == "thread_broadcast":
            body["reply_broadcast"] = True
        body |= payload.model_dump(mode="json")
        return PostRequest(draft, style, target, channel, thread_ts, body, pinned)

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
        if request.pinned:
            await self._threads.clear_pin()
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
