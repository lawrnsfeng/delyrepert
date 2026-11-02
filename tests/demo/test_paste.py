"""Paste-ready text and HTML for both modes, from the demo fixtures."""

from pathlib import Path

import pytest

from daily_report.compose.models import Draft, Mode
from daily_report.compose.paste import PasteFormat, render_paste
from tests.compose.helpers import r1_draft
from tests.demo.helpers import LINK, DemoEnv

PASTE = Path(__file__).parent / "paste"
EXTENSIONS = {"text": "txt", "html": "html"}
KEYS = ("DEMO-101", "DEMO-102", "DEMO-103", "DEMO-104", "DEMO-105", "DEMO-108")


def demo_draft(root: Path) -> Draft:
    """Run the demo and return the draft it saved."""
    state = root / "state"
    result = DemoEnv(root).run("--state-dir", str(state), config=False)
    assert result.exit_code == 0, result.output
    (path,) = (state / "drafts").glob("*.json")
    return Draft.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("fmt", ["text", "html"])
@pytest.mark.parametrize("mode", ["team", "self"])
def test_paste_matches_the_golden(tmp_path: Path, mode: Mode, fmt: PasteFormat) -> None:
    """Both formats in both modes equal their golden byte for byte."""
    draft = demo_draft(tmp_path).model_copy(update={"mode": mode})

    golden = PASTE / f"{mode}.{EXTENSIONS[fmt]}"

    assert render_paste(draft, fmt) == golden.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["team", "self"])
def test_text_has_raw_urls_and_no_slack_markup(tmp_path: Path, mode: Mode) -> None:
    """Every ticket line ends in its bare URL; nothing Slack would show literally."""
    draft = demo_draft(tmp_path).model_copy(update={"mode": mode})

    text = render_paste(draft, "text")

    for key in KEYS:
        assert any(
            key in line and line.endswith(f" · https://demo.atlassian.net/browse/{key}")
            for line in text.splitlines()
        ), key
    for markup in ("*", "<http", "](", "_Priority"):
        assert markup not in text
    assert ("Priority" in text) is (mode == "self")
    assert text.splitlines()[0] == "STANDUP · TUE 3 NOV"


@pytest.mark.parametrize("mode", ["team", "self"])
def test_html_is_self_contained_with_real_links(tmp_path: Path, mode: Mode) -> None:
    """Key and title are anchors to Jira; no script and no external asset."""
    draft = demo_draft(tmp_path).model_copy(update={"mode": mode})

    page = render_paste(draft, "html")

    assert page.startswith("<!DOCTYPE html>")
    assert '<meta charset="utf-8">' in page
    for key in KEYS:
        url = f"https://demo.atlassian.net/browse/{key}"
        assert page.count(f'<a href="{url}">') == 2, key
    for external in ("<script", "src=", "<link", "@import", "url("):
        assert external not in page


def test_paste_of_an_empty_day_and_a_display_name() -> None:
    """With no Jira items both formats say so; the display name prefixes the header."""
    draft = r1_draft(mode="team").model_copy(update={"items": [], "notes": []})

    text = render_paste(draft, "text", display_name="Dana")
    page = render_paste(draft, "html", display_name="Dana")

    assert "No Jira updates" in text
    assert "<strong>No Jira updates</strong>" in page
    assert text.startswith("DANA · STANDUP")
    assert "<h1>Dana · Standup" in page
    assert LINK not in text
