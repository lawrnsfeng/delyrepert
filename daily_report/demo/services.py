import datetime as dt
import functools
import shutil
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from aiohttp.test_utils import TestServer

from daily_report.collect.services import CollectService, SnapshotStore
from daily_report.compose.models import Draft, Mode, SlackPayload, StyleName
from daily_report.compose.render import Renderer, context
from daily_report.compose.services import DraftStore
from daily_report.config.models import CookConfig, JiraConfig, ParentMatch, ThreadSpec
from daily_report.context.services import ContextLoader
from daily_report.cook.services import CookRunner, CookService
from daily_report.demo.exceptions import (
    DemoParentTextError,
    WorkspaceMismatchError,
)
from daily_report.effects.models import Intent
from daily_report.effects.services import Effects
from daily_report.jira.fake import FakeJira, JiraFixtures
from daily_report.jira.services import JiraClient
from daily_report.logger import get_logger
from daily_report.notes.services import NotesInbox
from daily_report.retry import RetryPolicy
from daily_report.slack import services as slack_services
from daily_report.slack.services import ParentFinder, SlackClient

DEMO_AS_OF = dt.datetime(2026, 11, 3, 9, tzinfo=dt.UTC)
_EMAIL = "owner@example.com"
_JIRA_URL = "https://demo.atlassian.net"  # item links only; the client talks to the fake


@asynccontextmanager
async def _serve_jira(root: Path) -> AsyncGenerator[str]:
    """Serve the fixture Jira on a free local port and yield its base URL."""
    server = TestServer(FakeJira(JiraFixtures.load(root)).build_app())
    await server.start_server()
    try:
        yield str(server.make_url("")).rstrip("/")
    finally:
        await server.close()


Rendered = dict[tuple[Mode, StyleName], SlackPayload]


@dataclass(frozen=True)
class DemoComposed:
    """The saved demo draft (in the first mode) and its payload per mode and style."""

    draft: Draft
    payloads: Rendered


class DemoService:
    """Runs collect → compose from the fixtures; every write goes through Effects."""

    def __init__(self, *, fixtures: Path, effects: Effects, renderer: Renderer) -> None:
        """Read fixtures from `fixtures`; render with `renderer`."""
        self._fixtures = fixtures
        self._effects = effects
        self._renderer = renderer

    async def build(
        self,
        *,
        as_of: dt.datetime,
        modes: list[Mode],
        styles: list[StyleName],
        state_dir: Path | None,
        cook_config: CookConfig | None,
    ) -> tuple[DemoComposed, Path | None]:
        """Return the composed draft and the state dir seeded for it (`None` under dry-run)."""
        seeded = await self._effects.perform(
            Intent("copy", "demo state fixtures into a fresh state dir"),
            functools.partial(self._seed, state_dir),
        )
        root = seeded or self._fixtures / "state"
        async with (
            _serve_jira(self._fixtures / "jira") as url,
            JiraClient.connect(
                base_url=url,
                email=_EMAIL,
                token="demo",  # noqa: S106 - the fake accepts any token
                retry=RetryPolicy(),
            ) as client,
        ):
            service = CollectService(
                client=client,
                store=SnapshotStore(root, self._effects),
                jira=JiraConfig(base_url=_JIRA_URL, email=_EMAIL),
                zone=dt.UTC,
            )
            report = await service.run(as_of)
        # The fixture notes are read in place and never consumed, so the inbox path is pinned.
        inbox = NotesInbox(root, self._effects)
        inbox.path = self._fixtures / "notes" / "pending.jsonl"
        files = sorted((self._fixtures / "context").glob("*.json"))
        draft = Draft.build(
            report,
            inbox.pending(),
            await ContextLoader([]).load(files),
            created_at=as_of,
            zone=dt.UTC,
            mode=modes[0],
        )
        if cook_config:
            draft = await self._cook(draft, cook_config)
        payloads = {
            (mode, style): self._renderer.render(
                draft.model_copy(update={"mode": mode}),
                style,
                animated_available=False,
            )
            for mode in modes
            for style in styles
        }
        notices = [payload.notice for payload in payloads.values() if payload.notice]
        draft = draft.model_copy(update={"render_notice": " · ".join(notices) or None})
        await DraftStore(root, self._effects).save(draft)
        return DemoComposed(draft, payloads), seeded

    async def _seed(self, state_dir: Path | None) -> Path:
        target = state_dir or Path(tempfile.mkdtemp(prefix="daily-report-demo-"))
        shutil.copytree(self._fixtures / "state", target, dirs_exist_ok=True)
        return target

    async def _cook(self, draft: Draft, config: CookConfig) -> Draft:
        cooked = await CookService(CookRunner(config.command, config.timeout_s)).polish(draft)
        if cooked.cook_error:
            get_logger().warning(f"cook rejected: {cooked.cook_error}")
        return cooked


class DemoPoster:
    """Posts the demo into the test workspace: a seeded parent, then one reply per style."""

    def __init__(  # noqa: PLR0913 - the demo workspace's coordinates
        self,
        client: SlackClient,
        *,
        team_id: str,
        channel: str,
        parent_text: str,
        day: dt.date,
        effects: Effects,
    ) -> None:
        """Post into `channel` once the token's team is `team_id`; the parent is dated `day`."""
        self._client = client
        self._team_id = team_id
        self._channel = channel
        self._parent_text = parent_text
        self._seeded = f"🗓️ Daily standup — {day.isoformat()} (demo)"
        self._effects = effects

    @property
    def channel(self) -> str:
        """Return the demo channel ID."""
        return self._channel

    async def check(self) -> None:
        """Refuse a foreign workspace, or a `parent_text` the seeded parent cannot match."""
        actual = str((await self._client.auth_test())["team_id"])
        if actual != self._team_id:
            raise WorkspaceMismatchError(actual, self._team_id)
        if self._parent_text not in self._seeded:
            raise DemoParentTextError(self._parent_text, self._seeded)

    async def post(self, payloads: Rendered, *, broadcast: bool) -> None:
        """Post the parent, find it with the real finder and reply once per mode and style."""
        await self._effects.perform(
            Intent("post", f"parent + {len(payloads)} replies to #{self._channel}"),
            functools.partial(self._post, payloads, broadcast=broadcast),
        )

    async def _post(self, payloads: Rendered, *, broadcast: bool) -> None:
        await self._client.post_message({"channel": self._channel, "text": self._seeded})
        spec = ThreadSpec("demo", self._channel, ParentMatch(text_contains=self._parent_text), 0)
        finder = ParentFinder(self._client, spec, dt.UTC)
        thread_ts = (await finder.find(slack_services.utc_now())).ts
        for number, ((mode, style), payload) in enumerate(payloads.items(), 1):
            body: dict[str, object] = {"channel": self._channel, "thread_ts": thread_ts}
            if broadcast:
                body["reply_broadcast"] = True
            body |= payload.model_dump(mode="json")
            label = context(f"Style {number}/{len(payloads)} — {mode} {style}")
            body["blocks"] = [label, *payload.blocks]
            await self._client.post_message(body)
