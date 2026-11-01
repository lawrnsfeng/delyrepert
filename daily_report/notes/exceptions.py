from dataclasses import dataclass

from daily_report.exc import TemplatedError
from daily_report.notes.models import NOTE_MAX


class NotesError(TemplatedError):
    """Base for notes-inbox errors; every one exits 2."""


@dataclass
class NoteTooLongError(NotesError):
    """A note is longer than the limit."""

    TEMPLATE = "note is {length} characters; the limit is {limit}"
    length: int
    limit: int = NOTE_MAX


@dataclass
class NoteNotFoundError(NotesError):
    """`note rm N` names a note the inbox does not hold."""

    TEMPLATE = "no note {number}: the inbox holds {count}"
    number: int
    count: int


@dataclass
class NoteUsageError(NotesError):
    """`note` got arguments that are neither TEXT, `list` nor `rm N`."""

    TEMPLATE = "note takes one TEXT, `list` or `rm N`, not: {given}"
    given: str
