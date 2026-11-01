import stat
from pathlib import Path

from daily_report.service.exceptions import (
    InsecureSecretsError,
    MissingSecretsError,
    UnreadableSecretsError,
)
from daily_report.service.models import SECRET_NAMES


def load_secrets(path: Path) -> dict[str, str]:
    """Read the 0600 `KEY=VALUE` file; refuse loose modes and missing names (never values)."""
    try:
        mode = path.stat().st_mode
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UnreadableSecretsError(str(path), str(exc)) from exc
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise InsecureSecretsError(str(path))
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip().strip("'\"")
    missing = [name for name in SECRET_NAMES if not values.get(name)]
    if missing:
        raise MissingSecretsError(str(path), ", ".join(missing))
    return {name: values[name] for name in SECRET_NAMES}
