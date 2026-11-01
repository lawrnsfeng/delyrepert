import datetime as dt
import fcntl
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import IO, Protocol

from daily_report.compose.exceptions import InvalidDraftError
from daily_report.compose.models import Posted, StyleName, Target
from daily_report.compose.services import Composed
from daily_report.effects.models import Intent
from daily_report.effects.services import Effects
from daily_report.exc import TemplatedError
from daily_report.logger import get_logger
from daily_report.service.exceptions import (
    ExpiredError,
    NotPendingError,
    RunInProgressError,
)
from daily_report.service.models import Paused, RunRecord, Trigger
from daily_report.slack.exceptions import SlackError

_RUN_ID = re.compile(r"\d{8}-\d{6}")


def utc_now() -> dt.datetime:
    """Return the current instant (tests replace it to pin "now")."""
    return dt.datetime.now(tz=dt.UTC)


class Poster(Protocol):
    """What approval needs from `PostService`."""

    async def post(self, run_id: str, *, style: StyleName, target: Target) -> Posted | None:
        """Post the draft of `run_id`; `None` when nothing was sent (dry-run)."""


class RunNotifier(Protocol):
    """Tells the owner where a run ended up (the Slack DM); it never raises Slack errors."""

    async def pending(self, record: RunRecord, composed: Composed) -> RunRecord:
        """Announce a run that reached `pending`; return its record, updated if needed."""

    async def failed(self, record: RunRecord) -> None:
        """Announce a run recorded as `failed`."""


class RunStore:
    """`<state_dir>/runs/<run-id>.json`; writes go through Effects."""

    def __init__(self, state_dir: Path, effects: Effects) -> None:
        """Keep run records under `state_dir`."""
        self._folder = state_dir / "runs"
        self._effects = effects

    def load(self, run_id: str) -> RunRecord | None:
        """Return the record of `run_id`, or `None` when there is none."""
        path = self._folder / f"{run_id}.json"
        if not _RUN_ID.fullmatch(run_id) or not path.is_file():
            return None
        return RunRecord.model_validate_json(path.read_bytes())

    def all(self) -> list[RunRecord]:
        """Return every record, oldest first (run ids sort by time)."""
        paths = sorted(self._folder.glob("*.json"))
        return [RunRecord.model_validate_json(path.read_bytes()) for path in paths]

    async def save(self, record: RunRecord) -> None:
        """Write `record`, replacing an earlier version of the same run."""
        relative = f"runs/{record.run_id}.json"
        path = self._folder / f"{record.run_id}.json"
        text = record.model_dump_json(indent=2) + "\n"

        async def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        await self._effects.perform(Intent("write", relative), write)


class PauseStore:
    """`<state_dir>/paused.json`; writes go through Effects."""

    def __init__(self, state_dir: Path, effects: Effects) -> None:
        """Keep the pause flag under `state_dir`."""
        self._path = state_dir / "paused.json"
        self._effects = effects

    def current(self) -> Paused | None:
        """Return the pause flag, or `None` when runs are not paused."""
        if not self._path.is_file():
            return None
        return Paused.model_validate_json(self._path.read_bytes())

    async def pause(self, paused: Paused) -> None:
        """Write the flag."""
        text = paused.model_dump_json(indent=2) + "\n"

        async def write() -> None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(text, encoding="utf-8")

        await self._effects.perform(Intent("write", "paused.json"), write)

    async def resume(self) -> None:
        """Delete the flag if it exists."""
        if self._path.is_file():

            async def delete() -> None:
                self._path.unlink(missing_ok=True)

            await self._effects.perform(Intent("delete", "paused.json"), delete)


class RunLock:
    """`<state_dir>/run.lock`: an flock held for a run, holding the run id; a crash frees it."""

    def __init__(self, state_dir: Path, effects: Effects) -> None:
        """Lock under `state_dir`."""
        self._path = state_dir / "run.lock"
        self._effects = effects

    def holder(self) -> str | None:
        """Return the run id holding the lock, or `None` when it is free."""
        try:
            handle = self._path.open("r+", encoding="utf-8")
        except FileNotFoundError:
            return None
        with handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return handle.read().strip() or "unknown"
        return None

    @asynccontextmanager
    async def hold(self, run_id: str) -> AsyncGenerator[None]:
        """Hold the lock for `run_id`; raise `RunInProgressError` when another run has it."""
        if (holder := self.holder()) is not None:
            raise RunInProgressError(holder)
        handle = await self._effects.perform(
            Intent("acquire", "run.lock"),
            partial(self._acquire, run_id),
        )
        try:
            yield
        finally:
            if handle:
                handle.close()  # closing the file releases the flock

    async def _acquire(self, run_id: str) -> IO[str]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:  # another run won the race after `holder()`
            handle.close()
            raise RunInProgressError(self.holder() or "unknown") from exc
        handle.truncate(0)
        handle.write(run_id)
        handle.flush()
        return handle


class RunService:
    """Runs collect and compose once, leaving the draft pending for approval."""

    def __init__(  # noqa: PLR0913 - the collaborators a run needs
        self,
        *,
        runs: RunStore,
        pauses: PauseStore,
        lock: RunLock,
        compose: Callable[[dt.datetime], Awaitable[Composed]],
        zone: dt.tzinfo,
        notifier: RunNotifier,
        now: Callable[[], dt.datetime] = utc_now,
    ) -> None:
        """Run with these collaborators; `compose(now)` collects, composes and saves the draft."""
        self._runs = runs
        self._pauses = pauses
        self._lock = lock
        self._compose = compose
        self._zone = zone
        self._now = now
        self._notifier = notifier

    async def run(self, trigger: Trigger) -> RunRecord:
        """Run once; a paused scheduled run is skipped, a failure is recorded then raised."""
        now = self._now()
        run_id = f"{now.astimezone(self._zone):%Y%m%d-%H%M%S}"
        record = RunRecord(
            run_id=run_id,
            trigger=trigger,
            started_at=now,
            status="running",
        )
        async with self._lock.hold(run_id):
            today = now.astimezone(self._zone).date()
            paused = self._pauses.current()
            if paused and paused.until and paused.until <= today:
                await self._pauses.resume()
                paused = None
            if paused and trigger == "scheduled":
                get_logger().info("skipped: paused")
                record = record.model_copy(update={"status": "paused_skip"})
                await self._runs.save(record)
                return record
            for old in self._runs.all():
                if old.status == "pending":
                    await self._runs.save(old.model_copy(update={"status": "expired"}))
            await self._runs.save(record)
            try:
                composed = await self._compose(now)
            except TemplatedError as exc:
                failed = record.model_copy(update={"status": "failed", "error": str(exc)})
                await self._runs.save(failed)
                await self._notifier.failed(failed)
                raise
            record = record.model_copy(
                update={"status": "pending", "draft_path": f"drafts/{composed.draft.run_id}.json"},
            )
            await self._runs.save(record)
            return await self._notifier.pending(record, composed)


class ApproveService:
    """Posts a pending run's draft once the owner approves it before the cutoff."""

    def __init__(
        self,
        *,
        runs: RunStore,
        poster: Poster,
        cutoff: dt.time,
        zone: dt.tzinfo,
        now: Callable[[], dt.datetime] = utc_now,
    ) -> None:
        """Approve through `poster`; drafts expire at `cutoff` (local time) on their day."""
        self._runs = runs
        self._poster = poster
        self._cutoff = cutoff
        self._zone = zone
        self._now = now

    async def approve(self, run_id: str, *, style: StyleName, target: Target) -> RunRecord:
        """Post the draft of `run_id`; refuse anything not pending or past its cutoff."""
        record = self._runs.load(run_id)
        if record is None or record.status != "pending":
            raise NotPendingError(run_id, record.status if record else "unknown")
        started = record.started_at.astimezone(self._zone)
        cutoff = started.replace(
            hour=self._cutoff.hour,
            minute=self._cutoff.minute,
            second=0,
            microsecond=0,
        )
        if started < cutoff <= self._now().astimezone(self._zone):
            await self._runs.save(record.model_copy(update={"status": "expired"}))
            raise ExpiredError(run_id, f"{cutoff:%H:%M}")
        try:
            posted = await self._poster.post(run_id, style=style, target=target)
        except (SlackError, InvalidDraftError) as exc:
            await self._runs.save(record.model_copy(update={"error": str(exc)}))
            raise
        if posted is None:
            return record
        record = record.model_copy(update={"status": "posted", "posted": posted, "error": None})
        await self._runs.save(record)
        return record
