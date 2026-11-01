import json

import pytest

from daily_report.compose.models import STYLE_NAMES, Draft, StyleName
from daily_report.compose.render import Layout, Section
from tests.compose.helpers import (
    GOLDEN,
    R1_NOTE_TEXT,
    draft_of,
    note,
    plain,
    r1_draft,
    render,
    report,
    report_item,
)

R1_KEYS = ("PAY-412", "PAY-398", "PAY-405", "PAY-377", "PAY-420", "PAY-430", "PAY-431", "PAY-440")
R1_LINES = {
    "⛔ Blocked": 1,
    "⚠️ At risk": 1,
    "💬 New comments": 1,
    "💬 Replied": 1,
    "✅ Done": 2,
    "↪️ Handed off": 1,
    "▶️ In progress": 1,
    "📝 Note": 1,
}
MRKDWN_STYLES: tuple[StyleName, ...] = ("bluf", "ytb", "compact", "cards")


def sections(parts: list[Section]) -> list[tuple[str, list[str]]]:
    """Return (title, [`<icon> <word> <ref or text>`]) per section."""
    return [
        (part.title, [f"{e.icon.emoji} {e.icon.word} {e.label or e.text}" for e in part.entries])
        for part in parts
    ]


def layout(draft: Draft) -> Layout:
    """Return the plain layout of `draft`."""
    return Layout(draft, display_name=None, animated=None)


def test_ac1_r1_groups_in_bluf_order() -> None:
    """R1 composes into Needs attention, Since Fri 30 Oct and Today, in that order."""
    draft = r1_draft()

    assert sections(layout(draft).bluf) == [
        (
            "Needs attention",
            ["⛔ Blocked PAY-412", "⚠️ At risk PAY-398", "💬 New comments PAY-430"],
        ),
        (
            "Since Fri 30 Oct",
            ["✅ Done PAY-405", "✅ Done PAY-377", "💬 Replied PAY-431", "↪️ Handed off PAY-440"],
        ),
        ("Today", ["▶️ In progress PAY-420", f"📝 Note {R1_NOTE_TEXT}"]),
    ]
    comments = next(item for item in draft.items if item.id == "PAY-430")
    assert (comments.summary, comments.comment_count, comments.first_author) == (
        "2 new from Dana",
        2,
        "Dana",
    )


def test_ac2_ytb_sections() -> None:
    """Ytb puts done/replied/handed off under Yesterday, the rest and notes under Today."""
    assert sections(layout(r1_draft()).ytb) == [
        (
            "Yesterday",
            ["✅ Done PAY-405", "✅ Done PAY-377", "💬 Replied PAY-431", "↪️ Handed off PAY-440"],
        ),
        (
            "Today",
            [
                "▶️ In progress PAY-420",
                "⚠️ At risk PAY-398",
                "💬 New comments PAY-430",
                f"📝 Note {R1_NOTE_TEXT}",
            ],
        ),
        ("Blockers", ["⛔ Blocked PAY-412"]),
    ]


@pytest.mark.parametrize("style", STYLE_NAMES)
def test_ac3_golden_payloads(style: StyleName) -> None:
    """Each style is byte-equal to its golden file, never unfurls and fits in 50 blocks."""
    payload = render(r1_draft(), style)

    assert payload.as_json == (GOLDEN / f"{style}.json").read_text(encoding="utf-8")
    assert (payload.unfurl_links, payload.unfurl_media) == (False, False)
    assert len(payload.blocks) <= 50


def test_ac3_all_styles_share_one_fallback_text() -> None:
    """The text fallback is the same in every style and names every key and the note."""
    texts = {render(r1_draft(), style).text for style in STYLE_NAMES}

    assert len(texts) == 1
    (text,) = texts
    for key in R1_KEYS:
        assert key in text
    assert R1_NOTE_TEXT in text


@pytest.mark.parametrize("style", STYLE_NAMES)
def test_ac4_every_line_shows_icon_then_word(style: StyleName) -> None:
    """Every R1 line shows one of the 8 icon-word pairs, and the counts line is exact."""
    shown = plain(render(r1_draft(), style))

    for pair, count in R1_LINES.items():
        assert shown.count(pair) == count, pair
    assert "⛔ 1 blocked · ⚠️ 1 at risk · ✅ 2 done · ▶️ 1 today" in shown


def test_ac5_animated_blocker_emoji_replaces_the_blocked_icon() -> None:
    """With the emoji available, blocker lines use it; markdown and counts keep ⛔."""
    draft = r1_draft()

    def styled(style: StyleName) -> str:
        return render(draft, style, animated="alert-anim", available=True).as_json

    for style in MRKDWN_STYLES:
        shown = styled(style)
        assert shown.count(":alert-anim:") == 1
        assert ":alert-anim: Blocked · <https://example.atlassian.net/browse/PAY-412|PAY-412>" in (
            shown
        )
        assert "⛔ Blocked" not in shown
        assert "⛔ 1 blocked" in shown
    emoji = {"type": "emoji", "name": "alert-anim"}
    for style in ("table", "richlist"):
        payload = json.loads(styled(style))
        assert json.dumps(payload).count(json.dumps(emoji)) == 1
    assert "alert-anim" not in styled("markdown")
    assert "⛔ Blocked" in styled("markdown")


@pytest.mark.parametrize(("animated", "available"), [("alert-anim", False), (None, True)])
def test_ac5_blocked_icon_without_the_animated_emoji(
    animated: str | None,
    *,
    available: bool,
) -> None:
    """Unavailable or unset, every style shows ⛔ on the blocker line."""
    for style in STYLE_NAMES:
        payload = render(r1_draft(), style, animated=animated, available=available)
        assert "alert-anim" not in payload.as_json
        assert "⛔ Blocked" in plain(payload)


def test_ac6_empty_day_still_drafts_with_the_note_under_today() -> None:
    """With no Jira items, bluf says so and the note sits under Today."""
    draft = draft_of(report([]), [note()])

    shown = render(draft, "bluf").as_json
    assert "🟰 *No Jira changes* since Fri 30 Oct" in shown
    assert f"*Today*\\n📝 Note · {R1_NOTE_TEXT}" in shown


def test_ac7_cards_collapse_done_items_to_bare_keys() -> None:
    """30 items with 20 done: cards collapse the done ones into one line and fit."""
    items = [report_item(f"PAY-{n}", "done") for n in range(1, 21)]
    items += [report_item(f"PAY-{n}", "in_progress") for n in range(21, 31)]
    draft = draft_of(report(items))

    payload = render(draft, "cards")

    keys = ", ".join(f"PAY-{n}" for n in range(1, 21))
    assert f"✅ Done · {keys}" in payload.as_json
    assert len(payload.blocks) <= 50
    assert payload.notice is None


def test_ac8_notes_are_escaped_in_mrkdwn_styles() -> None:
    """`&`, `<` and `>` in a note are escaped before mrkdwn."""
    draft = r1_draft(notes=[note("a <b> & c")])

    for style in MRKDWN_STYLES:
        assert "a &lt;b&gt; &amp; c" in render(draft, style).as_json


def test_hidden_items_leave_the_fallback_text() -> None:
    """A hidden item is not named in the fallback text."""
    draft = r1_draft()
    hidden = [item.model_copy(update={"hidden": item.id == "PAY-440"}) for item in draft.items]

    text = render(draft.model_copy(update={"items": hidden}), "bluf").text

    assert "PAY-440" not in text
    assert "PAY-412" in text


def test_display_name_leads_the_header() -> None:
    """A configured display name prefixes the header."""
    payload = render(r1_draft(), "bluf", display_name="Lawrence")

    assert payload.blocks[0]["text"]["text"] == "Lawrence · Standup · Mon 2 Nov"


@pytest.mark.parametrize("style", STYLE_NAMES)
def test_empty_first_run_renders_header_and_footer_only(style: StyleName) -> None:
    """No baseline, items or notes: the empty-day line names the 24-hour window."""
    draft = draft_of(report([], baseline=False))

    shown = plain(render(draft, style))

    assert "🟰 *No Jira changes* in the last 24 hours" in shown
    assert "Sources: Jira" in shown


def test_ac7_cards_over_50_blocks_fall_back_to_compact() -> None:
    """60 in-progress items cannot collapse, so cards render as compact with a notice."""
    items = [report_item(f"PAY-{n}", "in_progress") for n in range(1, 61)]
    draft = draft_of(report(items))

    payload = render(draft, "cards")

    assert payload.notice == "cards exceeds 50 blocks; showing compact"
    assert payload.blocks == render(draft.model_copy(), "compact").blocks
    assert "notice" not in payload.as_json


@pytest.mark.parametrize(
    ("style", "notice"),
    [
        ("bluf", "bluf exceeds 3000 characters in a section; showing compact"),
        ("ytb", "ytb exceeds 3000 characters in a section; showing compact"),
        ("table", "table exceeds 100 table rows; showing compact"),
        ("markdown", "markdown exceeds 12000 characters in a markdown block; showing compact"),
        ("cards", "cards exceeds 50 blocks; showing compact"),
        ("compact", None),
        ("richlist", None),
    ],
)
def test_overflow_rule_per_style(style: StyleName, notice: str | None) -> None:
    """Past a Slack limit a style falls back to compact, which ends with `…and N more`."""
    items = [report_item(f"PAY-{n}", "in_progress", "x" * 100) for n in range(1, 151)]

    payload = render(draft_of(report(items)), style)

    assert payload.notice == notice
    assert payload.overflow is None
    if style == "richlist":
        return
    body = payload.blocks[2]["text"]["text"]
    assert len(body) <= 3000
    assert body.endswith(" more")
