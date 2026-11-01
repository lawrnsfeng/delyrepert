"""Stub programs (`$EDITOR`, `cook.command`) written to `tmp_path` as Python scripts."""

import json
import sys
from pathlib import Path
from textwrap import dedent

from tests.compose.helpers import r1_draft


def stub(folder: Path, body: str, name: str = "stub.py") -> str:
    """Write a Python script and return the command that runs it (no spaces in the path)."""
    path = folder / name
    path.write_text(dedent(body), encoding="utf-8")
    return f"{sys.executable} {path}"


def cook_output(**overrides: object) -> str:
    """Return a valid cook output for R1 (every visible id), with `overrides` applied."""
    visible = r1_draft().visible
    output = {
        "headline": "Ledger export at risk",
        "summaries": {i.id: f"AI {i.id}" for i in visible},
    }
    return json.dumps(output | overrides)


def print_stub(folder: Path, text: str, *, code: int = 0) -> list[str]:
    """Return a `cook.command` argv that drains stdin, prints `text` and exits with `code`."""
    command = stub(
        folder,
        f"""
        import sys
        sys.stdin.read()
        print({text!r})
        sys.exit({code})
        """,
    )
    return command.split(" ", 1)


def toml_list(argv: list[str]) -> str:
    """Return `argv` as a TOML array."""
    return json.dumps(argv)
