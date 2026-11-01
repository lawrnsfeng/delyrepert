from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import Self

import aiohttp
from pydantic import BaseModel

from daily_report.jira.exceptions import (
    JiraAuthError,
    JiraRetriesExhaustedError,
    JiraStatusError,
    JiraUnreachableError,
)
from daily_report.jira.models import (
    ChangeHistory,
    ChangeLogPage,
    CommentPage,
    JiraComment,
    JiraField,
    JiraFieldList,
    JiraIssue,
    JiraUser,
    SearchPage,
)
from daily_report.retry import RetriesExhaustedError, RetryableError, RetryPolicy

type JsonBody = dict[str, object]

_API = "/rest/api/3"
_AUTH_STATUSES = frozenset({401, 403})
_TOO_MANY_REQUESTS = 429
_SERVER_ERROR = 500
_CLIENT_ERROR = 400


class JiraClient:
    """Jira Cloud REST v3 client authenticated with email and API token."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        base_url: str,
        email: str,
        retry: RetryPolicy,
    ) -> None:
        """Wrap an authenticated `session`."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._email = email
        self._retry = retry

    @classmethod
    @asynccontextmanager
    async def connect(
        cls,
        *,
        base_url: str,
        email: str,
        token: str,
        retry: RetryPolicy,
    ) -> AsyncGenerator[Self]:
        """Open a session with basic auth and yield a client bound to it."""
        headers = {"Authorization": aiohttp.encode_basic_auth(email, token)}
        async with aiohttp.ClientSession(headers=headers) as session:
            yield cls(session, base_url=base_url, email=email, retry=retry)

    async def myself(self) -> JiraUser:
        """Return the authenticated user."""
        return await self._call(JiraUser, "GET", f"{_API}/myself")

    async def fields(self) -> list[JiraField]:
        """Return every field the site defines."""
        return (await self._call(JiraFieldList, "GET", f"{_API}/field")).root

    async def search(self, jql: str, fields: list[str]) -> list[JiraIssue]:
        """Return every issue matching `jql`, following `nextPageToken`."""
        body: JsonBody = {"jql": jql, "fields": fields, "maxResults": 100}
        pages = await self._paged(SearchPage, f"{_API}/search/jql", body)
        return [issue for page in pages for issue in page.issues]

    async def changelogs(self, issue_ids: list[str]) -> dict[str, list[ChangeHistory]]:
        """Return status changelog histories keyed by issue id, following `nextPageToken`."""
        # shortcut: bulkfetch takes at most 1000 issues per call, chunk if one owner ever has more
        body: JsonBody = {"issueIdsOrKeys": issue_ids, "fieldIds": ["status"], "maxResults": 1000}
        pages = await self._paged(ChangeLogPage, f"{_API}/changelog/bulkfetch", body)
        histories: dict[str, list[ChangeHistory]] = {}
        for page in pages:
            for log in page.issue_change_logs:
                histories.setdefault(log.issue_id, []).extend(log.change_histories)
        return histories

    async def comments(self, key: str) -> list[JiraComment]:
        """Return the newest comments of issue `key`, newest first."""
        # shortcut: one page of the newest 100 comments, page with startAt if a day ever has more
        params = {"orderBy": "-created", "maxResults": "100"}
        page = await self._call(CommentPage, "GET", f"{_API}/issue/{key}/comment", params=params)
        return page.comments

    async def _paged[T: SearchPage | ChangeLogPage](
        self,
        model: type[T],
        path: str,
        body: JsonBody,
    ) -> list[T]:
        pages: list[T] = []
        token: str | None = None
        while True:
            page_body = {**body, "nextPageToken": token} if token else body
            page = await self._call(model, "POST", path, body=page_body)
            pages.append(page)
            token = page.next_page_token
            if token is None:
                return pages

    async def _call[T: BaseModel](
        self,
        model: type[T],
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        body: JsonBody | None = None,
    ) -> T:
        url = f"{self._base_url}{path}"

        async def attempt() -> T:
            async with self._session.request(method, url, params=params, json=body) as resp:
                if resp.status in _AUTH_STATUSES:
                    raise JiraAuthError(self._email, self._base_url, resp.status)
                if resp.status == _TOO_MANY_REQUESTS or resp.status >= _SERVER_ERROR:
                    retry_after = resp.headers.get("Retry-After")
                    raise RetryableError.from_response(resp.status, retry_after)
                if resp.status >= _CLIENT_ERROR:
                    raise JiraStatusError(resp.status)
                return model.model_validate(await resp.json())

        try:
            return await self._retry.run(attempt)
        except RetriesExhaustedError as exc:
            raise JiraRetriesExhaustedError(exc.status, exc.attempts) from exc
        except aiohttp.ClientError as exc:
            raise JiraUnreachableError(self._base_url, str(exc)) from exc
