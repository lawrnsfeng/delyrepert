import json
import sys
from pathlib import Path

import pytest

from daily_report.compose.models import STYLE_NAMES, Draft, DraftItem
from daily_report.config.models import ContextCommand
from daily_report.context.models import ContextResult, Dropped
from daily_report.context.services import ContextLoader
from tests.compose.helpers import FIXTURES, R1_AS_OF, plain, r1_draft, render, run_cli

LONG = "y" * 130
SKILL = {
    "source": "demo-skill",
    "items": [
        {"group": "today", "text": "Rehearse the demo"},
        {"group": "later", "text": "Not a group"},
        {"group": "done", "text": LONG},
    ],
}


def python_command(name: str, code: str) -> ContextCommand:
    """Return a context command running `python -c code`."""
    return ContextCommand(name=name, argv=[sys.executable, "-c", code])


def write_config(tmp_path: Path, commands: list[ContextCommand]) -> Path:
    """Write a config file with `context.commands`."""
    path = tmp_path / "config.toml"
    entries = "".join(
        f"\n[[context.commands]]\nname = {json.dumps(c.name)}\nargv = {json.dumps(c.argv)}\n"
        for c in commands
    )
    path.write_text(
        '[jira]\nbase_url = "https://example.atlassian.net"\nemail = "owner@example.com"\n'
        + entries,
        encoding="utf-8",
    )
    return path


def compose(tmp_path: Path, config: Path, *more: str) -> Draft:
    """Compose R1 with `more` arguments and return the saved draft."""
    state, out = tmp_path / "state", tmp_path / "out"
    result = run_cli(
        "compose",
        "--config",
        str(config),
        "--state-dir",
        str(state),
        "--as-of",
        R1_AS_OF,
        "--report",
        str(FIXTURES / "r1.json"),
        "--out",
        str(out),
        *more,
    )
    assert result.exit_code == 0, result.output
    (path,) = (state / "drafts").glob("*.json")
    return Draft.model_validate_json(path.read_text(encoding="utf-8"))


def test_ac9_context_file_items_render_and_bad_ones_drop(tmp_path: Path) -> None:
    """Item 1 renders with demo-skill in the footer; items 2 and 3 drop with reasons."""
    skill = tmp_path / "skill.json"
    skill.write_text(json.dumps(SKILL), encoding="utf-8")

    draft = compose(tmp_path, write_config(tmp_path, []), "--context", str(skill))

    assert [(d.source, d.index) for d in draft.dropped] == [("demo-skill", 2), ("demo-skill", 3)]
    assert draft.dropped[0].reason.startswith("group: Input should be 'attention'")
    assert draft.dropped[1].reason == "text: String should have at most 120 characters"
    bluf = (tmp_path / "out" / "bluf.json").read_text(encoding="utf-8")
    assert "▶️ In progress · demo-skill#1 Rehearse the demo — demo-skill" in bluf
    assert "Sources: Jira · demo-skill" in bluf
    assert LONG not in bluf


def test_ac9_failing_context_command_still_writes_the_draft(tmp_path: Path) -> None:
    """A context command that exits 1 drops with a reason; the draft is still written."""
    config = write_config(tmp_path, [python_command("demo-skill", "raise SystemExit(1)")])

    draft = compose(tmp_path, config)

    assert draft.dropped == [
        Dropped(source="demo-skill", index=None, reason="command demo-skill exited 1"),
    ]


@pytest.mark.asyncio
async def test_context_command_output_and_failures(tmp_path: Path) -> None:
    """Command JSON is parsed; a missing program and invalid JSON drop with reasons."""
    good = {"source": "cal", "items": [{"group": "note", "text": "1:1 at 14:00"}]}
    loader = ContextLoader(
        [
            python_command("cal", f"print({json.dumps(json.dumps(good))})"),
            ContextCommand(name="ghost", argv=[str(tmp_path / "missing")]),
            python_command("noisy", "print('not json')"),
            python_command("anon", "print('{\"items\": []}')"),
        ],
    )

    result = await loader.load([])

    assert result.sources == ["cal"]
    assert [entry.item.text for entry in result.entries] == ["1:1 at 14:00"]
    assert [(d.source, d.reason.split(":")[0]) for d in result.dropped] == [
        ("ghost", "command ghost failed"),
        ("noisy", "invalid context"),
        ("anon", "invalid context"),
    ]
    assert result.dropped[2].reason == "invalid context: source: Field required"


@pytest.mark.parametrize(
    ("item", "kind", "group"),
    [
        ({"group": "done", "text": "t", "priority": "blocked"}, "blocked", "attention"),
        ({"group": "today", "text": "t", "priority": "at_risk"}, "at_risk", "attention"),
        ({"group": "attention", "text": "t", "priority": "normal"}, "context", "attention"),
        ({"group": "done", "text": "t"}, "done", "since"),
        ({"group": "today", "text": "t"}, "in_progress", "today"),
        ({"group": "note", "text": "t"}, "context", "today"),
    ],
)
def test_context_item_kind_and_group(item: dict[str, str], kind: str, group: str) -> None:
    """A context priority wins; otherwise its group decides kind and place."""
    (entry,) = ContextResult.parse("x", json.dumps({"source": "s", "items": [item]})).entries

    drafted = DraftItem.from_context(entry)

    assert (drafted.kind, drafted.group, drafted.id) == (kind, group, "s#1")


def test_context_link_renders_in_every_style() -> None:
    """A linked context item shows its link in every style."""
    source = {"source": "s", "items": [{"group": "today", "text": "t", "link": "https://x.test"}]}
    draft = r1_draft(context=ContextResult.parse("s", json.dumps(source)))

    for style in STYLE_NAMES:
        payload = render(draft, style)
        assert "https://x.test" in payload.as_json
        assert "s#1" in plain(payload)
