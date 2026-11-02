"""Paste-ready output for when Slack posting is not allowed: plain text and HTML.

Pasting plain text keeps bare URLs clickable (Slack links them); pasting the rendered HTML
page keeps bold and real links. Neither uses Slack mrkdwn, which only shows literally in the
composer.
"""

import re
from html import escape as html_escape
from typing import Literal

from daily_report.compose.models import Draft
from daily_report.compose.render import Entry, Layout

PasteFormat = Literal["text", "html"]

_CSS = (
    "body{font:15px/1.5 system-ui,sans-serif;max-width:46em;margin:2em auto;padding:0 1em}"
    "h1{font-size:1.3em}h2{font-size:1.05em;margin:1.2em 0 .3em}"
    "ul{margin:0;padding-left:1.4em}li{margin:.25em 0}"
    ".detail{color:#555;font-size:.9em}.foot{color:#777;font-size:.85em;margin-top:1.5em}"
)
_BOLD = re.compile(r"\*(.+?)\*")


def _text_line(entry: Entry) -> list[str]:
    """Return one item's lines: the line with its raw URL, then the detail (self mode)."""
    line = entry.plain + (f" · {entry.url}" if entry.url else "")
    return [line, *([f"   {entry.detail}"] if entry.detail else [])]


def _text(layout: Layout) -> str:
    lines = [layout.header.upper()]
    lines += [part for part in (layout.counts, (layout.no_changes or "").replace("*", "")) if part]
    for part in layout.sections:
        lines += ["", part.title.upper()]
        for entry in part.entries:
            lines += _text_line(entry)
    return "\n".join([*lines, "", layout.sources]) + "\n"


def _link(url: str | None, text: str) -> str:
    text = html_escape(text)
    return f'<a href="{html_escape(url)}">{text}</a>' if url else text


def _item(entry: Entry) -> str:
    ref = f"{_link(entry.url, entry.label)} " if entry.label else ""
    tail = f" — {html_escape(entry.tail)}" if entry.tail else ""
    detail = f'<br><span class="detail">{html_escape(entry.detail)}</span>' if entry.detail else ""
    status = f"<strong>{entry.icon.emoji} {html_escape(entry.icon.word)}</strong>"
    return f"<li>{status} · {ref}{_link(entry.url, entry.title)}{tail}{detail}</li>"


def _html(layout: Layout) -> str:
    body = [f"<h1>{html_escape(layout.header)}</h1>"]
    body += [f"<p>{html_escape(layout.counts)}</p>"] if layout.counts else []
    if layout.no_changes:
        body.append(
            "<p>" + _BOLD.sub(r"<strong>\1</strong>", html_escape(layout.no_changes)) + "</p>",
        )
    for part in layout.sections:
        items = "\n".join(_item(entry) for entry in part.entries)
        body += [f"<h2>{html_escape(part.title)}</h2>", f"<ul>\n{items}\n</ul>"]
    body.append(f'<p class="foot">{html_escape(layout.sources)}</p>')
    title = html_escape(layout.header)
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>{title}</title><style>{_CSS}</style></head>\n<body>\n"
        + "\n".join(body)
        + "\n</body></html>\n"
    )


def render_paste(draft: Draft, fmt: PasteFormat, *, display_name: str | None = None) -> str:
    """Render `draft` (in its own mode) as paste-ready `text` or a self-contained `html` page."""
    layout = Layout(draft, display_name, None)
    return _text(layout) if fmt == "text" else _html(layout)
