"""The restricted edit YAML: headline, item summaries and `hidden`, and notes (decision D9)."""

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from daily_report.collect.models import SUMMARY_MAX
from daily_report.compose.exceptions import (
    BadFieldError,
    MissingItemError,
    NotYamlError,
    UnknownItemError,
)
from daily_report.compose.models import Draft
from daily_report.notes.models import NOTE_MAX, Note, NoteGroup

ERROR_PREFIX = "# error: "


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EditItem(_Strict):
    """One editable item line."""

    id: str
    summary: str = Field(max_length=SUMMARY_MAX)
    hidden: bool = False


class EditNote(_Strict):
    """One editable note."""

    text: str = Field(min_length=1, max_length=NOTE_MAX)
    group: NoteGroup


class EditDocument(_Strict):
    """The whole edit YAML."""

    headline: str = Field(min_length=1)
    items: list[EditItem]
    notes: list[EditNote]


def to_yaml(draft: Draft) -> str:
    """Return the edit YAML of `draft`."""
    document = EditDocument(
        headline=draft.headline,
        items=[EditItem(id=i.id, summary=i.summary, hidden=i.hidden) for i in draft.items],
        notes=[EditNote(text=n.text, group=n.group) for n in draft.notes],
    )
    return yaml.safe_dump(document.model_dump(), sort_keys=False, allow_unicode=True, width=10**6)


def with_error(text: str, reason: str) -> str:
    """Return `text` headed by `# error: <reason>` (replacing an earlier error line)."""
    body = text.partition("\n")[2] if text.startswith(ERROR_PREFIX) else text
    return f"{ERROR_PREFIX}{reason}\n{body}"


def apply_edit(draft: Draft, text: str) -> Draft:
    """Return `draft` with the edited fields; raise `InvalidEditError` naming what is wrong."""
    try:
        document = EditDocument.model_validate(yaml.safe_load(text))
    except yaml.YAMLError as exc:
        raise NotYamlError(str(exc)) from exc
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "document"
        raise BadFieldError(where, first["msg"]) from exc
    edits = {item.id: item for item in document.items}
    known = {item.id for item in draft.items}
    for key in edits:
        if key not in known:
            raise UnknownItemError(key)
    missing = sorted(known - edits.keys())
    if missing:
        raise MissingItemError(missing[0])
    # shortcut: notes match by position and new ones are stamped with the draft's time;
    # a deleted note stays in the inbox, so it returns in the next standup.
    notes = [
        Note(
            at=draft.notes[index].at if index < len(draft.notes) else draft.created_at,
            text=note.text,
            group=note.group,
        )
        for index, note in enumerate(document.notes)
    ]
    items = [
        item.model_copy(
            update={"summary": edits[item.id].summary, "hidden": edits[item.id].hidden},
        )
        for item in draft.items
    ]
    return draft.model_copy(update={"headline": document.headline, "items": items, "notes": notes})
