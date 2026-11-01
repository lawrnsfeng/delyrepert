from dataclasses import dataclass

from daily_report.exc import TemplatedError


class EffectsError(TemplatedError):
    """Base for effects-gate errors."""


@dataclass
class InvalidDryRunValueError(EffectsError):
    """`DAILY_REPORT_DRY_RUN` holds a value that is neither truthy nor falsy."""

    TEMPLATE = (
        "DAILY_REPORT_DRY_RUN={value!r} is invalid: use 1, true or yes to turn dry-run on, "
        "or 0, false, no or an empty value to turn it off"
    )
    value: str
