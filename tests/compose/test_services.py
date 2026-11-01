import datetime as dt
from pathlib import Path

import pytest

from daily_report.collect.models import Report
from daily_report.compose.models import STYLE_NAMES, Draft, StyleName
from daily_report.compose.render import Renderer
from daily_report.compose.services import Composed, ComposeService, DraftStore
from daily_report.context.services import ContextLoader
from daily_report.effects.services import RealEffects
from daily_report.notes.services import NotesInbox
from tests.compose.helpers import CREATED_AT, R1_NOTE_TEXT, report, report_item


async def compose(
    state_dir: Path,
    source: Report,
    styles: tuple[StyleName, ...] = STYLE_NAMES,
) -> Composed:
    """Compose `source` with the inbox under `state_dir`, for real."""
    effects = RealEffects()
    service = ComposeService(
        inbox=NotesInbox(state_dir, effects),
        store=DraftStore(state_dir, effects),
        context=ContextLoader([]),
        renderer=Renderer(display_name=None, blocked_animated=None),
        zone=dt.UTC,
    )
    return await service.compose(source, context_files=[], styles=styles, now=CREATED_AT)


def saved(state_dir: Path, composed: Composed) -> Draft:
    """Read back the saved draft."""
    path = state_dir / "drafts" / f"{composed.draft.run_id}.json"
    return Draft.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_ac6_empty_day_writes_a_draft_with_the_pending_note(tmp_path: Path) -> None:
    """An empty report with one pending note still writes the draft, holding the note."""
    await NotesInbox(tmp_path, RealEffects()).add(R1_NOTE_TEXT, group="today", at=CREATED_AT)

    composed = await compose(tmp_path, report([]))

    draft = saved(tmp_path, composed)
    assert draft.run_id == "20261102-090500"
    assert [note.text for note in draft.notes] == [R1_NOTE_TEXT]
    assert "🟰 *No Jira changes* since Fri 30 Oct" in composed.payloads["bluf"].as_json


@pytest.mark.asyncio
async def test_ac7_render_notice_is_saved_in_the_draft(tmp_path: Path) -> None:
    """When cards fall back to compact, the saved draft carries the notice."""
    items = [report_item(f"PAY-{n}", "in_progress") for n in range(1, 61)]

    composed = await compose(tmp_path, report(items), ("cards",))

    assert saved(tmp_path, composed).render_notice == "cards exceeds 50 blocks; showing compact"


@pytest.mark.asyncio
async def test_render_notices_of_several_styles_are_joined(tmp_path: Path) -> None:
    """Each style that fell back adds its notice, in render order."""
    items = [report_item(f"PAY-{n}", "in_progress") for n in range(1, 61)]

    composed = await compose(tmp_path, report(items), ("cards", "bluf"))

    assert saved(tmp_path, composed).render_notice == (
        "cards exceeds 50 blocks; showing compact"
        " · bluf exceeds 3000 characters in a section; showing compact"
    )


@pytest.mark.asyncio
async def test_no_overflow_leaves_render_notice_null(tmp_path: Path) -> None:
    """A draft that fits every style has no notice."""
    composed = await compose(tmp_path, report([report_item("PAY-1", "done")]))

    assert saved(tmp_path, composed).render_notice is None
