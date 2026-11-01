"""In-process fake of the Jira Cloud REST v3 endpoints the collector calls.

Serves REST v3 JSON from a fixtures directory (`myself.json`, `field.json`, `issues.json`,
`changelogs.json`, `comments.json`), pages with `nextPageToken`, records every request and
replays scripted error responses. Shared by the tests and the demo.
"""

import json
from dataclasses import dataclass, field
from importlib.resources.abc import Traversable
from typing import Any, Self

from aiohttp import web
from aiohttp.typedefs import Handler

type Json = dict[str, Any]


@dataclass
class JiraFixtures:
    """The data the fake serves, one attribute per fixture file."""

    myself: Json
    fields: list[Json]
    issues: list[Json]
    changelogs: list[Json]
    comments: dict[str, list[Json]]

    @classmethod
    def load(cls, root: Traversable) -> Self:
        """Read every fixture file from `root`."""
        names = ("myself", "field", "issues", "changelogs", "comments")
        data = {
            n: json.loads(root.joinpath(f"{n}.json").read_text(encoding="utf-8")) for n in names
        }
        return cls(
            myself=data["myself"],
            fields=data["field"],
            issues=data["issues"],
            changelogs=data["changelogs"],
            comments=data["comments"],
        )


@dataclass(frozen=True)
class RecordedRequest:
    """One request the fake received."""

    method: str
    path: str
    query: dict[str, str]
    body: Json | None


@dataclass(frozen=True)
class ScriptedResponse:
    """An error response replayed instead of the fixture data."""

    status: int
    retry_after: int | None = None


@dataclass
class FakeJira:
    """The fake Jira server state; `build_app` turns it into an `aiohttp.web` app."""

    fixtures: JiraFixtures
    page_size: int = 50
    requests: list[RecordedRequest] = field(default_factory=list)
    _scripted: dict[str, list[ScriptedResponse]] = field(default_factory=dict)

    def script(
        self,
        path: str,
        status: int,
        *,
        times: int = 1,
        retry_after: int | None = None,
    ) -> None:
        """Answer the next `times` requests to `path` with `status` (and `Retry-After`)."""
        self._scripted.setdefault(path, []).extend([ScriptedResponse(status, retry_after)] * times)

    def build_app(self) -> web.Application:
        """Return the `aiohttp.web` application serving this state."""
        app = web.Application(middlewares=[self._record])
        app.router.add_get("/rest/api/3/myself", self._myself)
        app.router.add_get("/rest/api/3/field", self._field)
        app.router.add_post("/rest/api/3/search/jql", self._search)
        app.router.add_post("/rest/api/3/changelog/bulkfetch", self._bulkfetch)
        app.router.add_get("/rest/api/3/issue/{key}/comment", self._comments)
        return app

    @web.middleware
    async def _record(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        body = await request.json() if request.can_read_body else None
        self.requests.append(
            RecordedRequest(request.method, request.path, dict(request.query), body),
        )
        queue = self._scripted.get(request.path)
        if not queue:
            return await handler(request)
        scripted = queue.pop(0)
        headers = (
            {} if scripted.retry_after is None else {"Retry-After": str(scripted.retry_after)}
        )
        error = {"errorMessages": [f"scripted HTTP {scripted.status}"]}
        return web.json_response(error, status=scripted.status, headers=headers)

    async def _myself(self, _: web.Request) -> web.Response:
        return web.json_response(self.fixtures.myself)

    async def _field(self, _: web.Request) -> web.Response:
        return web.json_response(self.fixtures.fields)

    async def _search(self, request: web.Request) -> web.Response:
        body = await request.json()
        return web.json_response(self._page("issues", self.fixtures.issues, body))

    async def _bulkfetch(self, request: web.Request) -> web.Response:
        body = await request.json()
        wanted = set(body["issueIdsOrKeys"])
        logs = [log for log in self.fixtures.changelogs if log["issueId"] in wanted]
        return web.json_response(self._page("issueChangeLogs", logs, body))

    async def _comments(self, request: web.Request) -> web.Response:
        comments = self.fixtures.comments.get(request.match_info["key"], [])
        # The client always asks for `orderBy=-created`, so the fake always answers newest first.
        newest_first = sorted(comments, key=lambda comment: comment["created"], reverse=True)
        return web.json_response({"startAt": 0, "total": len(comments), "comments": newest_first})

    def _page(self, name: str, items: list[Json], body: Json) -> Json:
        start = int(body.get("nextPageToken") or 0)
        end = start + self.page_size
        page: Json = {name: items[start:end], "isLast": end >= len(items)}
        if end < len(items):
            page["nextPageToken"] = str(end)
        return page
