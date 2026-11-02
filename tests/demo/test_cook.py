from pathlib import Path

import pytest

from tests.demo.helpers import DemoEnv


def test_ac4_cook_stub_wording_reaches_the_files(tmp_path: Path) -> None:
    """A valid `{summaries, headline}` replaces the script wording."""
    env = DemoEnv(tmp_path)
    env.write_cook_config("ok")

    result = env.run("--cook", "--style", "bluf")

    assert result.exit_code == 0, result.output
    text = (env.out / "self" / "bluf.json").read_text("utf-8")
    assert "Cooked DEMO-103" in text
    assert "Cooked headline" in text
    assert "Cooked demo-skill#1" in text


def test_ac4_cook_accepts_structured_output(tmp_path: Path) -> None:
    """The same answer inside `structured_output` works."""
    env = DemoEnv(tmp_path)
    env.write_cook_config("structured")

    result = env.run("--cook", "--style", "bluf")

    assert result.exit_code == 0, result.output
    assert "Cooked DEMO-103" in (env.out / "self" / "bluf.json").read_text("utf-8")


@pytest.mark.parametrize(
    ("mode", "cause"),
    [
        ("missing", "cook dropped item DEMO-103"),
        ("extra", "cook added unknown item PAY-999"),
        (
            "long",
            "cook output invalid: summaries.DEMO-103 String should have at most 120 characters",
        ),
        ("field", "cook output invalid: status Extra inputs are not permitted"),
        ("invalid", "cook returned invalid JSON"),
        ("array", "cook output invalid: output Input should be a valid dictionary"),
        ("exit3", "cook exited 3"),
    ],
)
def test_ac4_rejected_cook_keeps_the_script_wording(tmp_path: Path, mode: str, cause: str) -> None:
    """An unusable answer warns, keeps the script text and still exits 0."""
    env = DemoEnv(tmp_path)
    env.write_cook_config(mode)

    result = env.run("--cook", "--style", "bluf")

    assert result.exit_code == 0, result.output
    assert f"cook rejected: {cause}" in result.stderr
    assert "Cooked" not in (env.out / "self" / "bluf.json").read_text("utf-8")


def test_ac4_cook_timeout_keeps_the_script_wording(tmp_path: Path) -> None:
    """A cook past `cook.timeout_s` is killed and rejected."""
    env = DemoEnv(tmp_path)
    env.write_cook_config("sleep", timeout_s=1)

    result = env.run("--cook", "--style", "bluf")

    assert result.exit_code == 0, result.output
    assert "cook rejected: cook timed out after 1s" in result.stderr


def test_ac4_unrunnable_cook_command_is_rejected(tmp_path: Path) -> None:
    """A missing program is a rejection, not a crash."""
    env = DemoEnv(tmp_path)
    env.write_config(extra=f'[cook]\ncommand = ["{tmp_path}/nope"]\n')

    result = env.run("--cook", "--style", "bluf")

    assert result.exit_code == 0, result.output
    assert "cook rejected: cook failed to start" in result.stderr


def test_ac4_cook_needs_a_command(tmp_path: Path) -> None:
    """`--cook` without `cook.command` exits 2."""
    env = DemoEnv(tmp_path)

    result = env.run("--cook", config=False)

    assert result.exit_code == 2
    assert "cook.command" in result.stderr
