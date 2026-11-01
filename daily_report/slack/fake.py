"""In-process fake of the Slack Web API methods `post` calls.

Serves `auth.test`, `conversations.history`, `emoji.list`, `chat.postMessage` and
`chat.getPermalink` under `/api/<method>`, records every request and replays scripted errors.
Shared by the tests and the demo.
"""

from dataclasses import dataclass, field
from typing import Any

from aiohttp import web
from aiohttp.typedefs import Handler

type Json = dict[str, Any]

API_PREFIX = "/api/"


@dataclass(frozen=True)
class RecordedRequest:
    """One request the fake received."""

    method: str
    authorization: str
    body: Json


@dataclass(frozen=True)
class ScriptedResponse:
    """An error response replayed instead of the normal answer."""

    error: str
    status: int = 200
    retry_after: int | None = None


@dataclass
class FakeSlack:
    """The fake Slack server state; `build_app` turns it into an `aiohttp.web` app."""

    history: list[Json] = field(default_factory=list)
    emoji: dict[str, str] = field(default_factory=dict)
    user_id: str = "U0OWNER"
    team_id: str = "T0DEMO"
    bot_id: str | None = None
    first_ts: float = 1762077600.0002
    requests: list[RecordedRequest] = field(default_factory=list)
    _scripted: dict[str, list[ScriptedResponse]] = field(default_factory=dict)

    def script(
        self,
        method: str,
        error: str,
        *,
        times: int = 1,
        status: int = 200,
        retry_after: int | None = None,
    ) -> None:
        """Answer the next `times` calls to `method` with `error` (`not_in_channel`, ...).

        Use `status=429` with `retry_after` for a rate limit.
        """
        self._scripted.setdefault(method, []).extend(
            [ScriptedResponse(error, status, retry_after)] * times,
        )

    def called(self, method: str) -> list[RecordedRequest]:
        """Return the recorded requests to `method`, in order."""
        return [request for request in self.requests if request.method == method]

    def build_app(self) -> web.Application:
        """Return the `aiohttp.web` application serving this state."""
        app = web.Application(middlewares=[self._answer])
        app.router.add_route("*", API_PREFIX + "auth.test", self._auth_test)
        app.router.add_route("*", API_PREFIX + "conversations.history", self._history)
        app.router.add_route("*", API_PREFIX + "emoji.list", self._emoji)
        app.router.add_route("*", API_PREFIX + "chat.postMessage", self._post_message)
        app.router.add_route("*", API_PREFIX + "chat.getPermalink", self._permalink)
        return app

    @web.middleware
    async def _answer(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        method = request.path.removeprefix(API_PREFIX)
        if request.method == "GET":  # the SDK reads with GET; Slack accepts both verbs
            body: Json = dict(request.query)
        elif request.content_type == "application/json":
            body = await request.json()
        else:
            body = {key: str(value) for key, value in (await request.post()).items()}
        authorization = request.headers.get("Authorization", "")
        self.requests.append(RecordedRequest(method, authorization, body))
        queue = self._scripted.get(method)
        if not queue:
            return await handler(request)
        scripted = queue.pop(0)
        headers = (
            {} if scripted.retry_after is None else {"Retry-After": str(scripted.retry_after)}
        )
        reply = {"ok": False, "error": scripted.error}
        return web.json_response(reply, status=scripted.status, headers=headers)

    async def _auth_test(self, _: web.Request) -> web.Response:
        reply: Json = {"ok": True, "user_id": self.user_id, "team_id": self.team_id}
        if self.bot_id:
            reply["bot_id"] = self.bot_id
        return web.json_response(reply)

    async def _history(self, _: web.Request) -> web.Response:
        oldest = float(self.requests[-1].body["oldest"])
        found = [m for m in self.history if float(m["ts"]) >= oldest]
        newest_first = sorted(found, key=lambda m: float(m["ts"]), reverse=True)
        return web.json_response({"ok": True, "messages": newest_first})

    async def _emoji(self, _: web.Request) -> web.Response:
        return web.json_response({"ok": True, "emoji": self.emoji})

    async def _post_message(self, _: web.Request) -> web.Response:
        body = self.requests[-1].body
        ts = f"{self.first_ts + len(self.called('chat.postMessage')) - 1:.6f}"
        if "thread_ts" not in body:  # a top-level post becomes channel history
            message = {"ts": ts, "user": self.user_id, "text": body.get("text", "")}
            self.history.append(message)
        return web.json_response({"ok": True, "channel": body["channel"], "ts": ts})

    async def _permalink(self, _: web.Request) -> web.Response:
        body = self.requests[-1].body
        stamp = body["message_ts"].replace(".", "")
        link = f"https://fake.slack.com/archives/{body['channel']}/p{stamp}"
        return web.json_response({"ok": True, "permalink": link})
