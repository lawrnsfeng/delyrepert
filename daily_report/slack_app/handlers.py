"""What each `/standup` verb and DM action does, free of Bolt: every method returns the reply."""

import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable

from daily_report.compose.models import StyleName, Target
from daily_report.compose.services import DraftStore
from daily_report.exc import TemplatedError
from daily_report.notes.exceptions import NotesError
from daily_report.notes.models import Note
from daily_report.notes.services import NotesInbox
from daily_report.service import services
from daily_report.service.commands import _Env, run_service
from daily_report.service.exceptions import NotPendingError, PastDateError
from daily_report.service.models import Paused, RunRecord
from daily_report.service.services import ApproveService, PauseStore, RunStore
from daily_report.slack.exceptions import AlreadyPostedError
from daily_report.slack.services import PostService, ThreadService
from daily_report.slack_app.dm import DmService
from daily_report.slack_app.models import DEFAULT_STYLE, HELP

PAUSE_USAGE = "usage: /standup pause [YYYY-MM-DD]"
NOTE_USAGE = "usage: /standup note <text>"
NO_PENDING = "no pending draft — /standup run"


class StandupHandlers:
    """The owner's controls: `/standup <verb>` and the approval DM's buttons."""

    def __init__(
        self,
        env: _Env,
        dms: DmService,
        timer_next: Callable[[], Awaitable[str | None]],
    ) -> None:
        """Act on `env`'s state dir through `dms`; `timer_next` tells when the timer fires."""
        self._env = env
        self._dms = dms
        self._timer_next = timer_next
        self._lock = asyncio.Lock()  # two clicks must not both post
        self._runs = RunStore(env.root, env.effects)
        self._pauses = PauseStore(env.root, env.effects)
        self._store = DraftStore(env.root, env.effects)
        self._inbox = NotesInbox(env.root, env.effects)

    @staticmethod
    async def guard(work: Awaitable[str | None]) -> str | None:
        """Await `work`; a known failure becomes its reply."""
        try:
            return await work
        except TemplatedError as exc:
            return str(exc)

    async def command(self, text: str) -> str:
        """Run `/standup <text>` and return the reply."""
        verb, _, rest = text.strip().partition(" ")
        verb = verb or "help"
        rest = rest.strip()
        replies: dict[str, Callable[[], Awaitable[str]]] = {
            "run": self._run,
            "preview": self._preview,
            "status": self._status,
            "pause": lambda: self._pause(rest),
            "resume": self._resume,
            "note": lambda: self._note(rest),
            "thread": lambda: self._thread_verb(rest),
            "post": lambda: self._post_pending(rest),
            "help": self._help,
        }
        if verb not in replies:
            return f"unknown verb {verb}\n{HELP}"
        return await replies[verb]()

    async def post(
        self,
        run_id: str,
        style: StyleName | None,
        target: Target | None,
        thread: str | None = None,
    ) -> str | None:
        """Post the draft with the DM's selections; return a reply only when it is refused."""
        settings = self._env.settings
        async with self._lock:
            try:
                approve = ApproveService(
                    runs=self._runs,
                    poster=PostService.from_config(
                        settings,
                        token=self._env.resolver.slack_token(),
                        state_dir=self._env.root,
                        effects=self._env.effects,
                        thread=thread,
                    ),
                    cutoff=settings.approval_cutoff,
                    zone=settings.zone,
                    now=services.utc_now,
                )
                approved = await approve.approve(
                    run_id,
                    style=style or settings.render.style or DEFAULT_STYLE,
                    target=target or settings.post.target,
                )
            except TemplatedError as exc:
                record = self._runs.load(run_id)
                if record and record.status == "expired":
                    await self._dms.finish(record, "⌛ Expired")
                posted = record and record.status == "posted"
                return (
                    "already posted" if posted or isinstance(exc, AlreadyPostedError) else str(exc)
                )
            permalink = approved.posted.permalink if approved.posted else ""
            await self._dms.finish(approved, f"✅ Posted {permalink}")
            return None

    async def skip(self, run_id: str) -> str | None:
        """Mark the run skipped and close its DM."""
        async with self._lock:
            record = self._pending(run_id)
            skipped = record.model_copy(update={"status": "skipped"})
            await self._runs.save(skipped)
            await self._dms.finish(skipped, "⏭ Skipped")
        return None

    async def edit_notes(self, run_id: str, trigger_id: str) -> str | None:
        """Open the notes modal."""
        self._pending(run_id)
        await self._dms.open_notes(trigger_id, run_id)
        return None

    async def submit_note(self, run_id: str, text: str) -> str | None:
        """Add the modal's note to the inbox and the draft, redraw the DM; return a refusal."""
        record = self._pending(run_id)
        text = text.strip()
        if not text:
            return NOTE_USAGE
        try:
            now = services.utc_now()
            await self._inbox.add(text, group="today", at=now)
        except NotesError as exc:
            return str(exc)
        draft = self._store.load(run_id)
        note = Note(at=now, text=text, group="today")
        draft = draft.model_copy(update={"notes": [*draft.notes, note]})
        await self._store.save(draft)
        await self._dms.refresh(record, draft)
        return None

    def _pending(self, run_id: str) -> RunRecord:
        record = self._runs.load(run_id)
        if record is None or record.status != "pending":
            raise NotPendingError(run_id, record.status if record else "unknown")
        return record

    async def _run(self) -> str:
        record = await run_service(self._env).run("slack")
        pending = record.status == "pending"
        return f"run {record.run_id} {record.status}" + (
            " — preview sent in DM" if pending else ""
        )

    async def _resume(self) -> str:
        await self._pauses.resume()
        return "resumed"

    async def _help(self) -> str:
        return HELP

    async def _preview(self) -> str:
        pending = [record for record in self._runs.all() if record.status == "pending"]
        if not pending:
            return NO_PENDING
        record = pending[-1]
        await self._dms.send_preview(record, self._store.load(record.run_id))
        return f"preview for run {record.run_id} sent in DM"

    async def _status(self) -> str:
        paused = self._pauses.current()
        runs = self._runs.all()
        if paused is None:
            state = "not paused"
        else:
            state = f"paused until {paused.until}" if paused.until else "paused"
        last = f"{runs[-1].run_id} {runs[-1].status}" if runs else "none"
        return "\n".join(
            [
                f"next run: {await self._timer_next() or 'unknown'}",
                "serve: running",
                state,
                f"last run: {last}",
            ],
        )

    async def _pause(self, arg: str) -> str:
        try:
            day = dt.date.fromisoformat(arg) if arg else None
        except ValueError:
            return PAUSE_USAGE
        if day and day < self._env.today:
            return str(PastDateError(day.isoformat(), self._env.today.isoformat()))
        await self._pauses.pause(Paused(paused_at=services.utc_now(), until=day, by="slack"))
        return f"paused until {day}" if day else "paused"

    async def _note(self, text: str) -> str:
        if not text:
            return NOTE_USAGE
        try:
            await self._inbox.add(text, group="today", at=services.utc_now())
        except NotesError as exc:
            return str(exc)
        return f"📝 Noted for next standup ({len(self._inbox.pending())} pending)"

    async def _thread_verb(self, arg: str) -> str:
        threads = ThreadService.from_config(
            self._env.settings,
            token=self._env.resolver.slack_token(),
            state_dir=self._env.root,
            effects=self._env.effects,
        )
        if arg == "clear":
            return "pin cleared" if await threads.clear_pin() else "no pin set"
        if arg.startswith("https://"):
            return f"pinned for the next post: {(await threads.pin_permalink(arg)).permalink}"
        if arg:
            return f"pinned {arg}: {(await threads.pin_named(arg)).permalink}"
        resolved = await threads.show(None)
        message = resolved.located.message
        if message is None:
            return f"thread pin: {resolved.permalink}"
        return (
            f"thread {resolved.located.source}: {resolved.permalink}\n"
            f"from {message.author} · {message.first_line}"
        )

    async def _post_pending(self, arg: str) -> str:
        pending = [record for record in self._runs.all() if record.status == "pending"]
        if not pending:
            return NO_PENDING
        run_id = pending[-1].run_id
        refused = await self.post(run_id, None, None, thread=arg or None)
        if refused is not None:
            return refused
        done = self._runs.load(run_id)
        return f"✅ Posted {done.posted.permalink if done and done.posted else ''}"
