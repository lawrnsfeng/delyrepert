"""Block Kit renderers: one class per standup style, behind `Renderer.render`."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from itertools import accumulate
from typing import Literal

from daily_report.collect.models import clip
from daily_report.compose.models import (
    SECTION_TEXT_MAX,
    Block,
    Draft,
    DraftItem,
    Group,
    Kind,
    SlackPayload,
    StyleName,
    day_label,
)
from daily_report.notes.models import Note, NoteGroup

HEADER_TEXT_MAX = 150


@dataclass(frozen=True)
class Icon:
    """A shape-distinct emoji and the word that always follows it (WCAG 1.4.1)."""

    emoji: str
    word: str


ICONS: dict[Kind | Literal["note"], Icon] = {
    "blocked": Icon("⛔", "Blocked"),
    "at_risk": Icon("⚠️", "At risk"),
    "comments": Icon("💬", "New comments"),
    "replied": Icon("💬", "Replied"),
    "done": Icon("✅", "Done"),
    "handed_off": Icon("↪️", "Handed off"),
    "in_progress": Icon("▶️", "In progress"),
    "context": Icon("📝", "Note"),
    "note": Icon("📝", "Note"),
}
COUNTED: tuple[tuple[Kind, str], ...] = (
    ("blocked", "⛔ {} blocked"),
    ("at_risk", "⚠️ {} at risk"),
    ("done", "✅ {} done"),
    ("in_progress", "▶️ {} today"),
)
NOTE_GROUPS: dict[NoteGroup, Group] = {"attention": "attention", "done": "since", "today": "today"}
YTB_SECTIONS: tuple[tuple[str, tuple[Kind, ...]], ...] = (
    ("Yesterday", ("done", "replied", "handed_off")),
    ("Today", ("in_progress", "at_risk", "comments", "context")),
    ("Blockers", ("blocked",)),
)


def escape(text: str) -> str:
    """Escape `&`, `<` and `>` for Slack mrkdwn."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def header(text: str) -> Block:
    """Return a header block."""
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def context(text: str) -> Block:
    """Return a context block holding one mrkdwn element."""
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def section(text: str) -> Block:
    """Return a mrkdwn section block."""
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def rich_text(text: str, *, bold: bool = False) -> Block:
    """Return a rich-text `text` element."""
    element: Block = {"type": "text", "text": text}
    if bold:
        element["style"] = {"bold": True}
    return element


def raw_cell(text: str) -> Block:
    """Return a `raw_text` table cell."""
    return {"type": "raw_text", "text": text}


def rich_cell(elements: list[Block]) -> Block:
    """Return a `rich_text` table cell with one section."""
    return {
        "type": "rich_text",
        "elements": [{"type": "rich_text_section", "elements": elements}],
    }


@dataclass(frozen=True)
class Entry:
    """One rendered line: icon and word, an optional reference, then the text."""

    icon: Icon
    text: str
    label: str | None = None
    url: str | None = None
    animated: str | None = None  # custom emoji name standing in for the icon

    @property
    def mrkdwn(self) -> str:
        """Return the line as escaped mrkdwn."""
        icon = f":{self.animated}:" if self.animated else self.icon.emoji
        ref = ""
        if self.label is not None:
            label = escape(self.label)
            ref = f"<{self.url}|{label}> " if self.url else f"{label} "
        return f"{icon} {self.icon.word} · {ref}{escape(self.text)}"

    @property
    def markdown(self) -> str:
        """Return the line as standard markdown (custom emoji never apply)."""
        ref = ""
        if self.label is not None:
            ref = f"[{self.label}]({self.url}) " if self.url else f"{self.label} "
        return f"{self.icon.emoji} {self.icon.word} · {ref}{self.text}"

    @property
    def rich_status(self) -> list[Block]:
        """Return the icon and word as rich-text elements."""
        icon = (
            {"type": "emoji", "name": self.animated}
            if self.animated
            else rich_text(self.icon.emoji)
        )
        return [icon, rich_text(f" {self.icon.word}")]

    @property
    def rich_ref(self) -> list[Block]:
        """Return the reference as rich-text elements (a link when there is a URL)."""
        if self.label is None:
            return []
        if self.url is None:
            return [rich_text(self.label)]
        return [{"type": "link", "url": self.url, "text": self.label}]

    @property
    def rich(self) -> list[Block]:
        """Return the whole line as rich-text elements."""
        ref = self.rich_ref
        tail = f" {self.text}" if ref else self.text
        return [*self.rich_status, rich_text(" · "), *ref, rich_text(tail)]


@dataclass(frozen=True)
class Section:
    """A titled group of lines."""

    title: str
    entries: list[Entry]


@dataclass(frozen=True)
class Layout:
    """Everything a style needs from one draft, in style-neutral form."""

    draft: Draft
    display_name: str | None
    animated: str | None
    collapse_done: bool = False

    @property
    def header(self) -> str:
        """Return the header text."""
        name, headline = self.display_name, self.draft.headline
        return clip(f"{name} · {headline}" if name else headline, HEADER_TEXT_MAX)

    @property
    def counts(self) -> str:
        """Return `⛔ 1 blocked · ⚠️ 1 at risk · ✅ 2 done · ▶️ 1 today`, zeros left out."""
        visible = self.draft.visible
        totals = [(text, sum(item.kind == kind for item in visible)) for kind, text in COUNTED]
        return " · ".join(text.format(total) for text, total in totals if total)

    @property
    def since(self) -> str:
        """Return `since Fri 30 Oct`, or the first run's 24-hour window."""
        day = self.draft.baseline_date
        return f"since {day_label(day)}" if day else "in the last 24 hours"

    @property
    def no_changes(self) -> str | None:
        """Return the empty-day line when the report had no Jira items."""
        if any(item.from_jira for item in self.draft.items):
            return None
        return f"🟰 *No Jira changes* {self.since}"

    @property
    def sources(self) -> str:
        """Return the sources line, marked when the AI polished the wording."""
        parts = [*self.draft.sources, *(["✨ AI-polished"] if self.draft.cooked else [])]
        return "Sources: " + " · ".join(parts)

    @property
    def footer(self) -> str:
        """Return the sources line as escaped mrkdwn."""
        return escape(self.sources)

    @property
    def fallback(self) -> str:
        """Return the notification text: header, every visible key, then the notes."""
        keys = [item.id for item in self.draft.visible]
        notes = [f"📝 {note.text}" for note in self.draft.notes]
        return escape(" · ".join([self.header, *keys, *notes]))

    @property
    def prelude(self) -> list[Block]:
        """Return the header, the counts line and the empty-day line."""
        blocks = [header(self.header)]
        if self.counts:
            blocks.append(context(self.counts))
        if self.no_changes:
            blocks.append(section(self.no_changes))
        return blocks

    @property
    def bluf(self) -> list[Section]:
        """Return Needs attention, Since <baseline> and Today."""
        titles: tuple[tuple[Group, str], ...] = (
            ("attention", "Needs attention"),
            ("since", self.since[0].upper() + self.since[1:]),
            ("today", "Today"),
        )
        sections = [
            Section(
                title,
                [
                    *self._entries([i for i in self.draft.visible if i.group == group]),
                    *self._notes([n for n in self.draft.notes if NOTE_GROUPS[n.group] == group]),
                ],
            )
            for group, title in titles
        ]
        return [section for section in sections if section.entries]

    @property
    def ytb(self) -> list[Section]:
        """Return Yesterday, Today (with every note) and Blockers."""
        sections = []
        for title, kinds in YTB_SECTIONS:
            items = [item for item in self.draft.visible if item.kind in kinds]
            items.sort(key=lambda item: kinds.index(item.kind))
            notes = self._notes(self.draft.notes) if title == "Today" else []
            sections.append(Section(title, [*self._entries(items), *notes]))
        return [section for section in sections if section.entries]

    @property
    def entries(self) -> list[Entry]:
        """Return every line in BLUF order."""
        return [entry for part in self.bluf for entry in part.entries]

    def _entries(self, items: list[DraftItem]) -> list[Entry]:
        done = [item.id for item in items if item.kind == "done"] if self.collapse_done else []
        entries = [
            Entry(
                ICONS[item.kind],
                f"{item.title} — {item.summary}",
                item.id,
                item.url,
                self.animated if item.kind == "blocked" else None,
            )
            for item in items
            if item.id not in done
        ]
        if done:
            entries.append(Entry(ICONS["done"], ", ".join(done)))
        return entries

    def _notes(self, notes: list[Note]) -> list[Entry]:
        return [Entry(ICONS["note"], note.text) for note in notes]


class StyleRenderer(ABC):
    """Turns a layout into the blocks of one style."""

    @abstractmethod
    def blocks(self, layout: Layout) -> list[Block]:
        """Return the style's blocks."""


class GroupedStyle(StyleRenderer):
    """Header, counts, one section per group, sources footer."""

    @abstractmethod
    def sections(self, layout: Layout) -> list[Section]:
        """Return the groups this style shows."""

    def blocks(self, layout: Layout) -> list[Block]:
        """Return one mrkdwn section per group."""
        groups = [
            section(f"*{part.title}*\n" + "\n".join(entry.mrkdwn for entry in part.entries))
            for part in self.sections(layout)
        ]
        return [*layout.prelude, *groups, context(layout.footer)]


class BlufStyle(GroupedStyle):
    """S1: Needs attention, Since <baseline>, Today."""

    def sections(self, layout: Layout) -> list[Section]:
        """Return the BLUF groups."""
        return layout.bluf


class YtbStyle(GroupedStyle):
    """S2: Yesterday, Today, Blockers."""

    def sections(self, layout: Layout) -> list[Section]:
        """Return the Yesterday/Today/Blockers groups."""
        return layout.ytb


class TableStyle(StyleRenderer):
    """S3: one table block, one row per line."""

    def blocks(self, layout: Layout) -> list[Block]:
        """Return the header, the table and the footer."""
        rows = [[raw_cell(name) for name in ("Group", "Status", "Ref", "Update")]]
        rows += [
            [
                raw_cell(part.title),
                rich_cell(entry.rich_status),
                rich_cell(entry.rich_ref) if entry.label else raw_cell("—"),
                raw_cell(entry.text),
            ]
            for part in layout.bluf
            for entry in part.entries
        ]
        table: Block = {
            "type": "table",
            "column_settings": [None, None, None, {"is_wrapped": True}],
            "rows": rows,
        }
        return [*layout.prelude, table, context(layout.footer)]


class MarkdownStyle(StyleRenderer):
    """S4: one standard-markdown block."""

    def blocks(self, layout: Layout) -> list[Block]:
        """Return a single markdown block."""
        lines = [f"## {layout.header}"]
        lines += [part for part in (layout.counts, layout.no_changes) if part]
        for part in layout.bluf:
            lines += ["", f"**{part.title}**", *(f"- {entry.markdown}" for entry in part.entries)]
        lines += ["", f"_{layout.sources}_"]
        return [{"type": "markdown", "text": "\n".join(lines)}]


class CompactStyle(StyleRenderer):
    """S5: every line in one section; past Slack's limit it ends with `…and N more`."""

    def blocks(self, layout: Layout) -> list[Block]:
        """Return the header, one section of lines and the footer."""
        lines = [entry.mrkdwn for entry in layout.entries]
        kept = lines
        if len("\n".join(lines)) > SECTION_TEXT_MAX:
            room = SECTION_TEXT_MAX - len(f"\n…and {len(lines)} more")
            # Each line costs its length plus a newline; prefix sums only grow, so count the fit.
            fits = sum(
                1 for total in accumulate(len(line) + 1 for line in lines) if total <= room + 1
            )
            kept = [*lines[:fits], f"…and {len(lines) - fits} more"]
        body = [section("\n".join(kept))] if kept else []
        return [*layout.prelude, *body, context(layout.footer)]


class CardsStyle(StyleRenderer):
    """S6: one card (a section and a divider) per line, under a title per group."""

    def blocks(self, layout: Layout) -> list[Block]:
        """Return two blocks per line."""
        blocks = layout.prelude
        for part in layout.bluf:
            blocks.append(section(f"*{part.title}*"))
            for entry in part.entries:
                blocks += [section(entry.mrkdwn), {"type": "divider"}]
        return [*blocks, context(layout.footer)]


class RichListStyle(StyleRenderer):
    """S7: one rich-text block with a bold title and a bullet list per group."""

    def blocks(self, layout: Layout) -> list[Block]:
        """Return the header, the rich-text list and the footer."""
        elements: list[Block] = []
        for part in layout.bluf:
            elements.append(
                {"type": "rich_text_section", "elements": [rich_text(part.title, bold=True)]},
            )
            elements.append(
                {
                    "type": "rich_text_list",
                    "style": "bullet",
                    "elements": [
                        {"type": "rich_text_section", "elements": entry.rich}
                        for entry in part.entries
                    ],
                },
            )
        body = [{"type": "rich_text", "elements": elements}] if elements else []
        return [*layout.prelude, *body, context(layout.footer)]


STYLES: dict[StyleName, StyleRenderer] = {
    "bluf": BlufStyle(),
    "ytb": YtbStyle(),
    "table": TableStyle(),
    "markdown": MarkdownStyle(),
    "compact": CompactStyle(),
    "cards": CardsStyle(),
    "richlist": RichListStyle(),
}


class Renderer:
    """Renders a draft in any style, applying the overflow rule (assumption A1)."""

    def __init__(self, *, display_name: str | None, blocked_animated: str | None) -> None:
        """Render with the configured display name and animated blocker emoji."""
        self._display_name = display_name
        self._blocked_animated = blocked_animated

    def render(self, draft: Draft, style: StyleName, *, animated_available: bool) -> SlackPayload:
        """Render `style`; over a Slack limit collapse done items, then fall back to compact."""
        layout = Layout(
            draft,
            self._display_name,
            self._blocked_animated if animated_available else None,
        )
        payload = self._payload(STYLES[style], layout)
        if payload.overflow is None:
            return payload
        layout = replace(layout, collapse_done=True)
        collapsed = self._payload(STYLES[style], layout)
        reason = collapsed.overflow
        if reason is None:
            return collapsed
        compact = self._payload(STYLES["compact"], layout)
        return compact.model_copy(update={"notice": f"{style} exceeds {reason}; showing compact"})

    def _payload(self, style: StyleRenderer, layout: Layout) -> SlackPayload:
        return SlackPayload(text=layout.fallback, blocks=style.blocks(layout))
