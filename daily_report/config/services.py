import tomllib
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from daily_report.config.exceptions import (
    ConfigFileUnsetError,
    InvalidConfigError,
    StateDirUnsetError,
    TokenUnsetError,
)
from daily_report.config.models import Config
from daily_report.effects.exceptions import InvalidDryRunValueError

_TRUTHY = frozenset({"1", "true", "yes"})
_FALSY = frozenset({"0", "false", "no", ""})


class ConfigResolver:
    """Resolves every setting from a CLI flag, then an env var, then the config file."""

    def __init__(self, environ: Mapping[str, str]) -> None:
        """Resolve against `environ` (normally `os.environ`)."""
        self._environ = environ

    def dry_run(self) -> bool:
        """Read `DAILY_REPORT_DRY_RUN`; raise `InvalidDryRunValueError` on an unknown value."""
        value = self._environ.get("DAILY_REPORT_DRY_RUN", "")
        if value.lower() in _TRUTHY:
            return True
        if value.lower() in _FALSY:
            return False
        raise InvalidDryRunValueError(value)

    def load(self, flag: Path | None) -> Config:
        """Load the config file named by `--config`, then `DAILY_REPORT_CONFIG`."""
        path = flag or self._env_path("DAILY_REPORT_CONFIG")
        if path is None:
            raise ConfigFileUnsetError
        try:
            return Config.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
            raise InvalidConfigError(str(path), str(exc)) from exc

    def state_dir(self, flag: Path | None, config: Config) -> Path:
        """Resolve the state dir from `--state-dir`, `DAILY_REPORT_STATE_DIR`, then config."""
        path = flag or self._env_path("DAILY_REPORT_STATE_DIR") or config.state_dir
        if path is None:
            raise StateDirUnsetError
        return path

    def jira_token(self) -> str:
        """Return `JIRA_API_TOKEN`."""
        token = self._environ.get("JIRA_API_TOKEN", "")
        if not token:
            raise TokenUnsetError
        return token

    def _env_path(self, name: str) -> Path | None:
        value = self._environ.get(name, "")
        return Path(value) if value else None
