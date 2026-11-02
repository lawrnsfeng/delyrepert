import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from daily_report.collect.models import Report
from daily_report.compose.exceptions import InvalidDraftError
from daily_report.compose.models import Draft, Mode, SlackPayload, StyleName
from daily_report.compose.render import Renderer
from daily_report.context.services import ContextLoader
from daily_report.cook.services import CookService
from daily_report.effects.models import Intent
from daily_report.effects.services import Effects
from daily_report.notes.services import NotesInbox

_RUN_ID = re.compile(r"\d{8}-\d{6}")


class DraftStore:
    """Reads and writes `<state_dir>/drafts/<run-id>.json`; writes go through Effects."""

    def __init__(self, state_dir: Path, effects: Effects) -> None:
        """Keep drafts under `state_dir`."""
        self._folder = state_dir / "drafts"
        self._effects = effects

    def load(self, run_id: str) -> Draft:
        """Read the draft of `run_id`; raise `InvalidDraftError` if it is missing or invalid."""
        if not _RUN_ID.fullmatch(run_id):
            raise InvalidDraftError(run_id, "not a run id (YYYYMMDD-HHMMSS)")
        try:
            return Draft.model_validate_json(self._path(run_id).read_bytes())
        except (OSError, ValidationError) as exc:
            raise InvalidDraftError(run_id, str(exc)) from exc

    async def save(self, draft: Draft) -> None:
        """Write `draft`, replacing an earlier version of the same run."""
        path = self._path(draft.run_id)
        text = draft.model_dump_json(indent=2) + "\n"

        async def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        await self._effects.perform(Intent("write", str(path)), write)

    def _path(self, run_id: str) -> Path:
        return self._folder / f"{run_id}.json"


@dataclass(frozen=True)
class Composed:
    """The saved draft and its payload per rendered style."""

    draft: Draft
    payloads: dict[StyleName, SlackPayload]


class ComposeService:
    """Builds the draft from report, notes and context, renders it and saves it."""

    def __init__(  # noqa: PLR0913 - the collaborators a compose needs
        self,
        *,
        inbox: NotesInbox,
        store: DraftStore,
        context: ContextLoader,
        renderer: Renderer,
        zone: dt.tzinfo,
        cook: CookService | None = None,
    ) -> None:
        """Compose with these collaborators; `zone` names the run and the headline day.

        `cook` (set by `--cook`) polishes the wording before the draft is rendered and saved.
        """
        self._inbox = inbox
        self._store = store
        self._context = context
        self._renderer = renderer
        self._zone = zone
        self._cook = cook

    async def compose(
        self,
        report: Report,
        *,
        context_files: list[Path],
        styles: Sequence[StyleName],
        now: dt.datetime,
        mode: Mode = "self",
    ) -> Composed:
        """Compose in `mode`, render `styles` (no animated emoji) and save the draft."""
        context = await self._context.load(context_files)
        draft = Draft.build(
            report,
            self._inbox.pending(),
            context,
            created_at=now,
            zone=self._zone,
            mode=mode,
        )
        if self._cook:
            draft = await self._cook.polish(draft)
        payloads = {
            style: self._renderer.render(draft, style, animated_available=False)
            for style in styles
        }
        notices = [payload.notice for payload in payloads.values() if payload.notice]
        draft = draft.model_copy(update={"render_notice": " · ".join(notices) or None})
        await self._store.save(draft)
        return Composed(draft, payloads)
