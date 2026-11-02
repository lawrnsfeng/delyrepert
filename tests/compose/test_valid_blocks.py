"""Every style's blocks are valid for Slack's Block Kit Builder."""

from typing import Any

import pytest
from slack_sdk.models.blocks import Block

from daily_report.compose.models import STYLE_NAMES, Mode, StyleName
from tests.compose.helpers import r1_draft, render


def nulls(node: Any) -> list[str]:  # noqa: ANN401 - raw JSON
    """Return the paths of every `null` inside a `column_settings` list."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "column_settings":
                found += [f"{key}[{i}]" for i, entry in enumerate(value) if entry is None]
            found += nulls(value)
    elif isinstance(node, list):
        for child in node:
            found += nulls(child)
    return found


@pytest.mark.parametrize("style", STYLE_NAMES)
@pytest.mark.parametrize("mode", ["team", "self"])
def test_no_style_emits_a_null_column_setting_and_every_block_parses(
    style: StyleName,
    mode: Mode,
) -> None:
    """The Builder rejects `null` column settings; slack_sdk must parse every block."""
    payload = render(r1_draft(mode=mode), style)

    assert nulls(payload.blocks) == []
    for block in payload.blocks:
        parsed = Block.parse(block)
        assert parsed is not None, block
        parsed.validate_json()
