import json
from pathlib import Path

import pytest

from daily_report.compose.models import Draft
from daily_report.cook.exceptions import CookInvalidOutputError
from daily_report.cook.services import parse_output
from tests.compose.helpers import FIXTURES, plain, r1_draft, render, run_cli
from tests.compose.stubs import cook_output, print_stub, stub, toml_list

R1_REPORT = str(FIXTURES / "r1.json")
VISIBLE = [item.id for item in r1_draft().visible]


def compose_cooked(
    tmp_path: Path,
    config_file: Path,
    argv: list[str],
    *,
    timeout_s: int = 60,
    env: dict[str, str] | None = None,
) -> tuple[Draft, str]:
    """Run `compose --cook` with `argv` as cook.command; return the saved draft and stdout."""
    text = config_file.read_text(encoding="utf-8")
    text += f"\n[cook]\ncommand = {toml_list(argv)}\ntimeout_s = {timeout_s}\n"
    config_file.write_text(text, encoding="utf-8")
    state = tmp_path / "state"
    result = run_cli(
        "compose",
        *("--config", str(config_file), "--state-dir", str(state)),
        *("--report", R1_REPORT, "--cook", "--style", "bluf"),
        env=env,
    )
    assert result.exit_code == 0, result.output
    (path,) = (state / "drafts").glob("*.json")
    return Draft.model_validate_json(path.read_text(encoding="utf-8")), result.stdout


@pytest.mark.parametrize("wrap", [False, True])
def test_ac1_cook_output_replaces_wording(
    tmp_path: Path,
    config_file: Path,
    *,
    wrap: bool,
) -> None:
    """Bare or `structured_output` cook JSON becomes the draft text, marked cooked."""
    output = cook_output()
    text = json.dumps({"structured_output": json.loads(output)}) if wrap else output

    draft, _ = compose_cooked(tmp_path, config_file, print_stub(tmp_path, text))

    assert draft.cooked
    assert draft.cook_error is None
    assert draft.headline == "Ledger export at risk"
    assert {item.id: item.summary for item in draft.items} == {i: f"AI {i}" for i in VISIBLE}
    assert "✨ AI-polished" in plain(render(draft, "bluf"))
    assert "✨ AI-polished" in plain(render(draft, "markdown"))


def test_cook_never_marks_an_uncooked_draft() -> None:
    """Without cook, the footer carries no AI mark."""
    assert "AI-polished" not in plain(render(r1_draft(), "bluf"))


def test_ac3_cook_stdin_has_visible_items_only(tmp_path: Path, config_file: Path) -> None:
    """The stub gets headline and items{id,kind,title,summary}: no notes, no token."""
    seen = tmp_path / "stdin.json"
    command = stub(
        tmp_path,
        f"""
        import sys
        from pathlib import Path
        Path({str(seen)!r}).write_text(sys.stdin.read(), encoding="utf-8")
        print({cook_output()!r})
        """,
    )

    compose_cooked(
        tmp_path,
        config_file,
        command.split(" ", 1),
        env={"SLACK_USER_TOKEN": "xoxp-secret", "JIRA_API_TOKEN": "jira-secret"},
    )

    raw = seen.read_text(encoding="utf-8")
    sent = json.loads(raw)
    assert set(sent) == {"headline", "items"}
    assert [item["id"] for item in sent["items"]] == VISIBLE
    assert all(set(item) == {"id", "kind", "title", "summary"} for item in sent["items"])
    assert "secret" not in raw
    assert "dentist" not in raw  # R1's pending note


def broken(**changes: object) -> str:
    """Return cook output with `changes` applied to the valid one."""
    return cook_output(**changes)


def without_first() -> str:
    """Return cook output missing the first visible id."""
    data = json.loads(cook_output())
    del data["summaries"][VISIBLE[0]]
    return json.dumps(data)


def with_extra_item() -> str:
    """Return cook output with an added PAY-999."""
    data = json.loads(cook_output())
    data["summaries"]["PAY-999"] = "new"
    return json.dumps(data)


def too_long() -> str:
    """Return cook output with a 121-character summary."""
    data = json.loads(cook_output())
    data["summaries"][VISIBLE[0]] = "x" * 121
    return json.dumps(data)


@pytest.mark.parametrize(
    ("text", "code", "cause"),
    [
        (without_first(), 0, f"cook dropped item {VISIBLE[0]}"),
        (with_extra_item(), 0, "cook added unknown item PAY-999"),
        (too_long(), 0, f"cook output invalid: summaries.{VISIBLE[0]} String should have at most"),
        (broken(status="done"), 0, "cook output invalid: status Extra inputs are not permitted"),
        ("not json", 0, "cook returned invalid JSON"),
        ("[]", 0, "cook output invalid: output Input should be a valid dictionary"),
        (cook_output(), 3, "cook exited 3"),
    ],
)
def test_ac2_rejected_cook_keeps_script_text(
    tmp_path: Path,
    config_file: Path,
    text: str,
    code: int,
    cause: str,
) -> None:
    """Every rejected output leaves the script summaries and says why."""
    draft, stdout = compose_cooked(tmp_path, config_file, print_stub(tmp_path, text, code=code))

    assert not draft.cooked
    assert draft.cook_error is not None
    assert draft.cook_error.startswith(cause)
    assert f"cook rejected: {draft.cook_error}" in stdout
    assert draft.headline.startswith("Standup · ")
    assert "AI " not in " ".join(item.summary for item in draft.items)
    assert "AI-polished" not in plain(render(draft, "bluf"))


def test_ac2_cook_past_timeout_is_rejected(tmp_path: Path, config_file: Path) -> None:
    """A cook still running after cook.timeout_s is killed and rejected."""
    command = stub(tmp_path, "import time\ntime.sleep(30)\n")

    draft, stdout = compose_cooked(tmp_path, config_file, command.split(" ", 1), timeout_s=1)

    assert draft.cook_error == "cook timed out after 1s"
    assert "cook rejected: cook timed out after 1s" in stdout


def test_cook_that_cannot_start_is_rejected(tmp_path: Path, config_file: Path) -> None:
    """A missing program is a rejection with the OS reason, not a crash."""
    draft, _ = compose_cooked(tmp_path, config_file, [str(tmp_path / "missing")])

    assert draft.cook_error is not None
    assert draft.cook_error.startswith("cook failed to start: ")


def test_cook_without_command_exits_2(tmp_path: Path, config_file: Path) -> None:
    """`--cook` with no cook.command is a config error."""
    result = run_cli(
        "compose",
        *("--config", str(config_file), "--state-dir", str(tmp_path / "state")),
        *("--report", R1_REPORT, "--cook"),
    )

    assert result.exit_code == 2
    assert "cook.command unset" in result.stderr
    assert not (tmp_path / "state").exists()


def test_parse_output_names_the_document_when_it_is_not_an_object() -> None:
    """A JSON list is invalid at the top level."""
    with pytest.raises(CookInvalidOutputError):
        parse_output("[]", set())
