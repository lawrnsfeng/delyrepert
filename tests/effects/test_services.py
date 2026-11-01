import pytest
from pytest_mock import MockerFixture

from daily_report.effects.models import Intent
from daily_report.effects.services import DryRunEffects, RealEffects

INTENT = Intent(verb="write", target="snapshots/x.json")


async def _action() -> str:
    return "done"


@pytest.mark.asyncio
async def test_ac29_dry_run_effects_report_and_skip_the_action(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """DryRunEffects records the intent, prints it on stderr and never awaits the action."""
    action = mocker.create_autospec(_action)
    effects = DryRunEffects()

    result = await effects.perform(INTENT, action)

    action.assert_not_awaited()
    assert result is None
    assert effects.intents == [INTENT]
    assert capsys.readouterr().err == "[dry-run] would write snapshots/x.json\n"


@pytest.mark.asyncio
async def test_ac29_real_effects_await_the_action(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """RealEffects awaits the action once, returns its result and prints nothing."""
    action = mocker.create_autospec(_action, return_value="done")

    result = await RealEffects().perform(INTENT, action)

    action.assert_awaited_once()
    assert result == "done"
    assert capsys.readouterr() == ("", "")
