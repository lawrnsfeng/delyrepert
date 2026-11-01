import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

# A plain alias: typer reads it and cannot unwrap a `type` statement.
NoteGroup = Literal["attention", "today", "done"]
NOTE_MAX = 120


class Note(BaseModel):
    """One line of `<state_dir>/notes/pending.jsonl`."""

    at: dt.datetime
    text: str = Field(min_length=1, max_length=NOTE_MAX)
    group: NoteGroup
