import datetime as dt
from pathlib import Path

from daily_report.effects.models import Intent
from daily_report.effects.services import Effects
from daily_report.notes.exceptions import NoteNotFoundError, NoteTooLongError
from daily_report.notes.models import NOTE_MAX, Note, NoteGroup


class NotesInbox:
    """`<state_dir>/notes/pending.jsonl`: notes for the next standup; writes go through Effects."""

    def __init__(self, state_dir: Path, effects: Effects) -> None:
        """Keep the inbox under `state_dir`."""
        self.path = state_dir / "notes" / "pending.jsonl"
        self._effects = effects

    def pending(self) -> list[Note]:
        """Return the pending notes, oldest first."""
        return [Note.model_validate_json(line) for line in self._lines()]

    async def add(self, text: str, *, group: NoteGroup, at: dt.datetime) -> None:
        """Append a note; raise `NoteTooLongError` past the limit."""
        if len(text) > NOTE_MAX:
            raise NoteTooLongError(len(text))
        line = Note(at=at, text=text, group=group).model_dump_json() + "\n"

        async def append() -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as inbox:
                inbox.write(line)

        await self._effects.perform(Intent("append", str(self.path)), append)

    async def remove(self, number: int) -> None:
        """Remove note `number` (1-based, as `note list` prints it)."""
        lines = self._lines()
        if not 1 <= number <= len(lines):
            raise NoteNotFoundError(number, len(lines))
        text = "".join(line + "\n" for index, line in enumerate(lines, 1) if index != number)

        async def write() -> None:
            self.path.write_text(text, encoding="utf-8")

        await self._effects.perform(Intent("remove", f"note {number} from {self.path}"), write)

    async def consume(self, posted: list[Note]) -> None:
        """Drop the notes a posted standup carried (matched by `at`, so preview edits count)."""
        stamps = {note.at for note in posted}
        kept = [note for note in self.pending() if note.at not in stamps]
        text = "".join(note.model_dump_json() + "\n" for note in kept)

        async def write() -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(text, encoding="utf-8")

        intent = Intent("consume", f"{len(posted)} notes from {self.path}")
        await self._effects.perform(intent, write)

    def _lines(self) -> list[str]:
        if not self.path.exists():
            return []
        return self.path.read_text(encoding="utf-8").splitlines()
