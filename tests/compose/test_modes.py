"""Team and self reports, the links on every item and the Builder link."""

import json
from typing import Any

import pytest

from daily_report.compose.models import STYLE_NAMES, Mode, StyleName
from daily_report.context.models import ContextEntry, ContextItem, ContextResult
from tests.compose.helpers import plain, r1_draft, r1_report, render

JIRA = "https://example.atlassian.net/browse/"
KEYS = ("PAY-412", "PAY-398", "PAY-405", "PAY-377", "PAY-420", "PAY-430", "PAY-431")
TITLES = {
    "PAY-412": "Ledger export to the bank",
    "PAY-398": "Refund webhook retries",
    "PAY-405": "Settlement report totals",
    "PAY-377": "Card BIN lookup cache",
    "PAY-420": "Payout scheduler",
    "PAY-430": "Currency rounding rules",
    "PAY-431": "Chargeback evidence upload",
}
MODES: tuple[Mode, ...] = ("team", "self")


def rich_links(node: Any) -> list[tuple[str, str]]:  # noqa: ANN401 - raw JSON
    """Return every (url, text) rich-text link inside `node`."""
    if isinstance(node, list):
        return [pair for child in node for pair in rich_links(child)]
    if not isinstance(node, dict):
        return []
    own = [(node["url"], node["text"])] if node.get("type") == "link" else []
    return own + [pair for value in node.values() for pair in rich_links(value)]


def linked(shown: str, url: str, text: str) -> bool:
    """Whether `text` links to `url` as mrkdwn, markdown or a rich-text link."""
    return any(
        form in shown
        for form in (
            f"<{url}|{text}>",
            f"[{text}]({url})",
            f'"url": "{url}", "text": "{text}"',
        )
    )


@pytest.mark.parametrize("mode", MODES)
def test_table_links_the_key_and_the_title(mode: Mode) -> None:
    """Each table row links its key (Ref) and its title (Update) to the issue."""
    table = next(b for b in render(r1_draft(mode=mode), "table").blocks if b["type"] == "table")

    seen = set()
    for _group, _status, ref, update in table["rows"][1:]:
        title_links, key_links = rich_links(update), rich_links(ref)
        if not title_links:  # the note has nothing to link
            continue
        url, title = title_links[0]
        assert title == TITLES.get(url.removeprefix(JIRA), title)
        if key_links:  # a ticket that left scope has no title, so no separate key
            assert key_links == [(url, url.removeprefix(JIRA))]
            seen.add(url.removeprefix(JIRA))
    assert seen >= set(KEYS)


@pytest.mark.parametrize("mode", MODES)
def test_cards_link_the_key_and_the_title(mode: Mode) -> None:
    """Each card's text links the key and the title to the issue."""
    blocks = render(r1_draft(mode=mode), "cards").blocks
    cards = [b["text"]["text"] for b in blocks if b["type"] == "section"]

    for key in KEYS:
        card = next(text for text in cards if f"|{key}>" in text)
        assert f"<{JIRA}{key}|{key}> <{JIRA}{key}|{TITLES[key]}>" in card


@pytest.mark.parametrize("style", STYLE_NAMES)
@pytest.mark.parametrize("mode", MODES)
def test_every_style_links_the_title(style: StyleName, mode: Mode) -> None:
    """In every style and mode a titled ticket's title is a link, not just its key."""
    shown = json.dumps(render(r1_draft(mode=mode), style).blocks, ensure_ascii=False)

    assert linked(shown, f"{JIRA}PAY-405", "Settlement report totals")


@pytest.mark.parametrize("style", ["table", "cards"])
@pytest.mark.parametrize("mode", MODES)
def test_context_item_without_a_url_stays_plain(style: StyleName, mode: Mode) -> None:
    """A context item with no link has no link; one with a link links its text."""
    entries = [
        ContextEntry("tools", 1, ContextItem(group="today", text="Plain chore")),
        ContextEntry(
            "tools",
            2,
            ContextItem(group="today", text="Linked chore", link="https://x.test/c"),
        ),
    ]
    draft = r1_draft(context=ContextResult(sources=["tools"], entries=entries), mode=mode)

    shown = json.dumps(render(draft, style).blocks, ensure_ascii=False)

    assert "Plain chore" in shown
    assert "|Plain chore" not in shown
    assert linked(shown, "https://x.test/c", "Linked chore")


@pytest.mark.parametrize("style", STYLE_NAMES)
def test_team_report_is_concise(style: StyleName) -> None:
    """Team: Yesterday/Today/Blockers, no transitions, authors, counts or bookkeeping."""
    shown = plain(render(r1_draft(mode="team"), style))

    for noise in ("→", "Dana", "Alice", "new comment", "Handed off", "Priority", "PAY-440"):
        assert noise not in shown, noise
    assert "due Tue 3 Nov" in shown
    assert "Source: Jira" in shown
    assert "Sources:" not in shown
    assert "Out 15:00" in shown
    if style not in {"bluf", "compact"}:
        assert all(title in shown for title in ("Yesterday", "Today", "Blockers"))


@pytest.mark.parametrize("style", STYLE_NAMES)
def test_self_report_is_detailed(style: StyleName) -> None:
    """Self: transitions, priority, comment authors with an excerpt, handed off, sources."""
    shown = plain(render(r1_draft(mode="self"), style))

    assert "In Progress → Blocked" in shown
    assert "Handed off" in shown
    assert "Sources: Jira" in shown
    if style != "compact":
        assert "Priority Medium" in shown
        assert "Dana, Alice: “Should we round half-even?”" in shown


def test_team_shows_a_polished_summary_and_hides_context_ids() -> None:
    """Polished wording reaches the team; a context `source#n` id never does."""
    entries = [ContextEntry("tools", 1, ContextItem(group="today", text="Chore"))]
    draft = r1_draft(context=ContextResult(sources=["tools"], entries=entries), mode="team")
    items = [
        i.model_copy(update={"summary": "Shipped"}) if i.id == "PAY-405" else i
        for i in draft.items
    ]
    draft = draft.model_copy(update={"items": items, "cooked": True})

    shown = plain(render(draft, "cards"))

    assert "Settlement report totals — Shipped" in shown
    assert "tools#1" not in shown


def test_team_with_no_jira_changes() -> None:
    """The empty day says so without baseline wording in team mode."""
    draft = r1_draft(mode="team").model_copy(update={"items": []})

    assert "No Jira updates" in plain(render(draft, "cards"))
    assert "since" not in plain(render(draft, "cards"))


def test_builder_url_names_the_team_only_when_set() -> None:
    """`builder_team` puts the workspace in the Builder link; unset keeps the short form."""
    payload = render(r1_draft(), "bluf")

    assert payload.builder_url().startswith("https://app.slack.com/block-kit-builder/#%7B")
    assert payload.builder_url("T0123456789").startswith(
        "https://app.slack.com/block-kit-builder/T0123456789/builder#%7B",
    )


def test_r1_report_item_carries_the_due_date() -> None:
    """The collector's due date reaches the draft."""
    draft = r1_draft()

    assert next(i for i in draft.items if i.id == "PAY-398").due is not None
    assert r1_report().items[1].due is not None
