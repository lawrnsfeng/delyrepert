from pathlib import Path

import pytest
import yaml
from typer.testing import Result

from daily_report.compose.edit import apply_edit, to_yaml, with_error
from daily_report.compose.exceptions import InvalidEditError
from daily_report.compose.models import Draft
from tests.compose.helpers import draft_of, r1_draft, report, run_cli
from tests.slack.helpers import RUN_ID, SlackEnv


def edited(change: dict[str, object] | None = None, *, items: dict[str, dict[str, object]]) -> str:
    """Return R1's edit YAML with `items` fields and `change` applied."""
    data = yaml.safe_load(to_yaml(r1_draft()))
    for item in data["items"]:
        item.update(items.get(item["id"], {}))
    return yaml.safe_dump(data | (change or {}), allow_unicode=True)


def test_yaml_round_trips_the_draft() -> None:
    """Dumping and applying an untouched YAML changes nothing."""
    draft = r1_draft()

    assert apply_edit(draft, to_yaml(draft)) == draft


def test_edit_updates_summaries_hidden_headline_and_notes() -> None:
    """Editable fields land in the draft; notes keep their stamp by position."""
    draft = r1_draft()
    data = yaml.safe_load(to_yaml(draft))
    data["headline"] = "Standup · Mon"
    data["items"][0]["summary"] = "new words"
    data["items"][1]["hidden"] = True
    data["notes"][0]["group"] = "attention"
    data["notes"].append({"text": "second", "group": "done"})

    result = apply_edit(draft, yaml.safe_dump(data))

    assert result.headline == "Standup · Mon"
    assert result.items[0].summary == "new words"
    assert result.items[1].hidden
    assert [(n.text, n.group) for n in result.notes] == [
        (draft.notes[0].text, "attention"),
        ("second", "done"),
    ]
    assert result.notes[0].at == draft.notes[0].at
    assert result.notes[1].at == draft.created_at


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("a: [", "not valid YAML"),
        ("- 1\n", "document: Input should be a valid dictionary"),
        (edited(items={"PAY-412": {"summary": "x" * 130}}), "items.0.summary: String should have"),
        (edited({"extra": 1}, items={}), "extra: Extra inputs are not permitted"),
        (edited(items={"PAY-412": {"id": "PAY-999"}}), "unknown item PAY-999"),
        (edited({"items": []}, items={}), "missing item PAY-377"),
    ],
)
def test_invalid_edit_names_the_problem(text: str, reason: str) -> None:
    """Each broken edit raises with the field or item named."""
    with pytest.raises(InvalidEditError) as raised:
        apply_edit(r1_draft(), text)

    assert reason in raised.value.message


def test_with_error_replaces_an_earlier_error_line() -> None:
    """One error line heads the text, whatever came before."""
    once = with_error("a: 1\n", "first")

    assert with_error(once, "second") == "# error: second\na: 1\n"


def test_empty_draft_round_trips() -> None:
    """A draft with no items has an empty items list."""
    draft = draft_of(report([]))

    assert apply_edit(draft, to_yaml(draft)) == draft


def edit_cli(env: SlackEnv, source: Path, *args: str, run_id: str = RUN_ID) -> Result:
    """Run `draft edit <run_id> --from source`."""
    return run_cli(
        *args,
        *("draft", "edit", run_id, "--from", str(source)),
        *("--config", str(env.config), "--state-dir", str(env.state)),
    )


def test_ac10_draft_edit_applies_good_yaml_and_refuses_bad(
    env: SlackEnv,
    tmp_path: Path,
) -> None:
    """Good YAML updates the summaries; a 130-char summary exits 2 and leaves the file alone."""
    good, bad = tmp_path / "good.yaml", tmp_path / "bad.yaml"
    good.write_text(edited(items={"PAY-398": {"summary": "2 review comments"}}), encoding="utf-8")
    bad.write_text(edited(items={"PAY-398": {"summary": "x" * 130}}), encoding="utf-8")
    before = env.draft_file.read_bytes()

    refused = edit_cli(env, bad)

    assert refused.exit_code == 2
    assert "items.1.summary" in refused.stderr
    assert env.draft_file.read_bytes() == before

    accepted = edit_cli(env, good)

    assert accepted.exit_code == 0, accepted.output
    saved = Draft.model_validate_json(env.draft_file.read_text(encoding="utf-8"))
    assert {i.id: i.summary for i in saved.items}["PAY-398"] == "2 review comments"


def test_ac10_draft_edit_dry_run_reports_the_write(env: SlackEnv, tmp_path: Path) -> None:
    """Dry-run names the draft file and leaves it byte-identical."""
    good = tmp_path / "good.yaml"
    good.write_text(edited(items={"PAY-398": {"summary": "changed"}}), encoding="utf-8")
    before = env.draft_file.read_bytes()

    result = edit_cli(env, good, "--dry-run")

    assert result.exit_code == 0
    assert f"[dry-run] would write {env.draft_file}" in result.stderr
    assert env.draft_file.read_bytes() == before


def test_draft_edit_unknown_run_exits_2(env: SlackEnv, tmp_path: Path) -> None:
    """A run id with no draft is a usage error."""
    source = tmp_path / "x.yaml"
    source.write_text("a: 1\n", encoding="utf-8")

    result = edit_cli(env, source, run_id="20200101-000000")

    assert result.exit_code == 2
    assert "cannot read draft" in result.stderr
