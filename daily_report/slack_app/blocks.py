"""Block Kit for the approval DM and the notes modal."""

from typing import Any

from daily_report.compose.models import STYLE_NAMES, Draft, StyleName, Target
from daily_report.compose.render import Renderer, context
from daily_report.notes.models import NOTE_MAX
from daily_report.slack_app.models import (
    ACTION_EDIT_NOTES,
    ACTION_POST,
    ACTION_SKIP,
    ACTION_STYLE,
    ACTION_TARGET,
    DEFAULT_STYLE,
    NOTE_BLOCK,
    NOTE_INPUT,
    NOTES_MODAL,
    TARGETS,
)

type Json = dict[str, Any]

_GALLERY_BLOCKS = 4  # blocks shown per style: 7 styles must fit Slack's 50 with the controls
_SINGLE_BLOCKS = 44


def _text(kind: str, text: str) -> Json:
    return {"type": kind, "text": text}


def _plain(text: str) -> Json:
    return _text("plain_text", text)


def _option(value: str) -> Json:
    return {"text": _plain(value), "value": value}


def _select(action_id: str, values: tuple[str, ...], initial: str) -> Json:
    return {
        "type": "static_select",
        "action_id": action_id,
        "options": [_option(value) for value in values],
        "initial_option": _option(initial),
    }


def _button(action_id: str, label: str, run_id: str, style: str | None = None) -> Json:
    button: Json = {
        "type": "button",
        "action_id": action_id,
        "text": _plain(label),
        "value": run_id,
    }
    if style:
        button["style"] = style
    return button


def preview_body(
    draft: Draft,
    renderer: Renderer,
    *,
    chosen: StyleName | None,
    target: Target,
) -> Json:
    """Return the DM: the draft (all seven styles when `chosen` is unset), selects and buttons."""
    if chosen is None:
        blocks: list[Json] = []
        for style in STYLE_NAMES:
            rendered = renderer.render(draft, style, animated_available=False)
            blocks += [context(f"*{style}*"), *rendered.blocks[:_GALLERY_BLOCKS]]
    else:
        rendered = renderer.render(draft, chosen, animated_available=False)
        blocks = rendered.blocks[:_SINGLE_BLOCKS]
        if len(rendered.blocks) > _SINGLE_BLOCKS:
            blocks.append(context("… shortened here; Post sends the whole draft"))
    if draft.notes:
        blocks.append(context("📝 " + " · ".join(note.text for note in draft.notes)))
    selects = [
        *([_select(ACTION_STYLE, STYLE_NAMES, DEFAULT_STYLE)] if chosen is None else []),
        _select(ACTION_TARGET, TARGETS, target),
    ]
    blocks += [
        {"type": "actions", "block_id": "standup_choices", "elements": selects},
        {
            "type": "actions",
            "block_id": "standup_buttons",
            "elements": [
                _button(ACTION_POST, "Post", draft.run_id, "primary"),
                _button(ACTION_EDIT_NOTES, "Edit notes", draft.run_id),
                _button(ACTION_SKIP, "Skip", draft.run_id, "danger"),
            ],
        },
    ]
    return {"text": f"Standup draft {draft.run_id}: approve to post", "blocks": blocks}


def final_body(text: str) -> Json:
    """Return a message body that replaces the buttons with one line."""
    return {"text": text, "blocks": [{"type": "section", "text": _text("mrkdwn", text)}]}


def notes_modal(run_id: str) -> Json:
    """Return the modal that takes one note; `private_metadata` carries the run."""
    return {
        "type": "modal",
        "callback_id": NOTES_MODAL,
        "private_metadata": run_id,
        "title": _plain("Edit notes"),
        "submit": _plain("Add"),
        "blocks": [
            {
                "type": "input",
                "block_id": NOTE_BLOCK,
                "label": _plain("Note for the next standup"),
                "element": {
                    "type": "plain_text_input",
                    "action_id": NOTE_INPUT,
                    "max_length": NOTE_MAX,
                },
            },
        ],
    }
