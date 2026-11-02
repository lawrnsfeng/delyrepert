"""The approval DM: sends the preview, edits it as the run moves on, reports failures."""

import functools
import json
from pathlib import Path
from typing import Self

from slack_sdk.web.async_client import AsyncWebClient

from daily_report.compose.models import Draft, SlackPayload, StyleName, Target
from daily_report.compose.render import Renderer
from daily_report.compose.services import Composed
from daily_report.config.exceptions import SlackUnsetError
from daily_report.config.models import Config
from daily_report.effects.models import Intent
from daily_report.effects.services import Effects
from daily_report.logger import get_logger
from daily_report.retry import RetryPolicy
from daily_report.service.models import DmRef, RunRecord
from daily_report.service.services import RunStore
from daily_report.slack.exceptions import SlackError
from daily_report.slack.services import SlackClient
from daily_report.slack_app.blocks import Json, final_body, notes_modal, preview_body


class DmService:
    """Talks to the owner in a DM as the bot; every Slack write goes through Effects."""

    def __init__(  # noqa: PLR0913 - the collaborators a DM needs
        self,
        *,
        client: SlackClient,
        owner: str,
        runs: RunStore,
        renderer: Renderer,
        effects: Effects,
        chosen: StyleName | None,
        target: Target,
    ) -> None:
        """Send to `owner`; `chosen` is `render.style` and `target` is `post.target`."""
        self._client = client
        self._owner = owner
        self._runs = runs
        self._renderer = renderer
        self._effects = effects
        self._chosen = chosen
        self._target = target

    @classmethod
    def from_config(
        cls,
        settings: Config,
        *,
        bot_token: str,
        owner: str,
        state_dir: Path,
        effects: Effects,
    ) -> Self:
        """Build the service from the config file and the bot token."""
        slack = settings.slack
        if slack is None:
            raise SlackUnsetError
        # shortcut: one attempt, no retry sleeps; a lost DM is logged and `preview` resends it.
        client = SlackClient(
            AsyncWebClient(token=bot_token, base_url=slack.api_url),
            RetryPolicy(attempts=1),
        )
        return cls(
            client=client,
            owner=owner,
            runs=RunStore(state_dir, effects),
            renderer=Renderer(
                display_name=settings.display_name,
                blocked_animated=settings.emoji.blocked_animated,
            ),
            effects=effects,
            chosen=settings.render.style,
            target=settings.post.target,
        )

    async def pending(self, record: RunRecord, composed: Composed) -> RunRecord:
        """Send the approval DM of a run that reached `pending` (a CLI run has none)."""
        if record.trigger == "cli":
            return record
        try:
            return await self.send_preview(record, composed.draft)
        except SlackError as exc:
            get_logger().error("approval DM failed", run_id=record.run_id, reason=str(exc))
            return record

    async def failed(self, record: RunRecord) -> None:
        """DM `❌ Run <id> failed: <cause>` for a scheduled run; a lost DM is only logged."""
        if record.trigger != "scheduled":
            return
        try:
            channel = await self._open()
            await self._post({"channel": channel or "", "text": self._failure(record)})
        except SlackError as exc:
            get_logger().error("failure DM failed", run_id=record.run_id, reason=str(exc))

    async def send_report(self, payload: SlackPayload) -> None:
        """DM a rendered report as it is (a `self --dm` run has no run record)."""
        channel = await self._open()
        await self._post({"channel": channel or "", **payload.model_dump(mode="json")})

    async def send_preview(self, record: RunRecord, draft: Draft) -> RunRecord:
        """DM the preview of `draft` and remember where it went (`dm`) in the run record."""
        channel = await self._open()
        body = {"channel": channel or "", **self._preview(draft)}
        sent = await self._post(body)
        if sent is None:
            return record
        record = record.model_copy(update={"dm": DmRef(channel=sent[0], ts=sent[1])})
        await self._runs.save(record)
        return record

    async def refresh(self, record: RunRecord, draft: Draft) -> None:
        """Re-render the preview in place (the notes changed)."""
        await self._update(record, self._preview(draft))

    async def finish(self, record: RunRecord, text: str) -> None:
        """Replace the preview with one line such as `⏭ Skipped`."""
        await self._update(record, final_body(text))

    async def open_notes(self, trigger_id: str, run_id: str) -> None:
        """Open the notes modal for the click `trigger_id`."""
        await self._effects.perform(
            Intent("views.open", "standup_notes_modal"),
            functools.partial(self._client.open_view, trigger_id, notes_modal(run_id)),
        )

    def _preview(self, draft: Draft) -> Json:
        return preview_body(draft, self._renderer, chosen=self._chosen, target=self._target)

    @staticmethod
    def _failure(record: RunRecord) -> str:
        return f"❌ Run {record.run_id} failed: {record.error}"

    async def _open(self) -> str | None:
        return await self._effects.perform(
            Intent("conversations.open", self._owner),
            functools.partial(self._client.open_dm, self._owner),
        )

    async def _post(self, body: Json) -> tuple[str, str] | None:
        return await self._effects.perform(
            Intent("chat.postMessage", json.dumps(body, ensure_ascii=False)),
            functools.partial(self._client.post_message, body),
        )

    async def _update(self, record: RunRecord, body: Json) -> None:
        if record.dm is None:
            return
        dm = record.dm
        await self._effects.perform(
            Intent("chat.update", f"{dm.ts} in {dm.channel}"),
            functools.partial(self._client.update_message, dm.channel, dm.ts, body),
        )
