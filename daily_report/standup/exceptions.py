from dataclasses import dataclass

from daily_report.config.exceptions import ConfigError


@dataclass
class CopyFormatError(ConfigError):
    """`--copy` was given with a format other than `text`."""

    TEMPLATE = (
        "--copy only copies --format text: pasting HTML source into Slack shows the tags; "
        "use --format html --out FILE and copy from the browser instead"
    )


@dataclass
class ClipboardUnavailableError(ConfigError):
    """None of the known clipboard programs is installed."""

    TEMPLATE = "no clipboard program found: install one of {names}, or drop --copy"
    names: str


@dataclass
class ClipboardFailedError(ConfigError):
    """The clipboard program exited non-zero."""

    TEMPLATE = "{program} failed to copy (exit {code})"
    program: str
    code: int
