from collections.abc import Sequence
from dataclasses import dataclass, field

from daily_report.service.installer import Completed


@dataclass
class FakeSystemd:
    """Answers `loginctl`, `systemd-analyze` and `systemctl` and records every call."""

    linger: str = "yes"
    linger_code: int = 0
    calendar_error: str = ""
    failing: str = ""
    shown: dict[str, str] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)

    async def __call__(self, argv: Sequence[str]) -> Completed:
        """Record `argv` and return the scripted outcome."""
        argv = list(argv)
        self.calls.append(argv)
        match argv:
            case ["loginctl", "show-user", *_]:
                return Completed(self.linger_code, f"{self.linger}\n")
            case ["systemd-analyze", "calendar", *_] if self.calendar_error:
                return Completed(1, stderr=f"{self.calendar_error}\n")
            case ["systemctl", "--user", "show", _, prop, "--value"]:
                return Completed(0, self.shown.get(prop.removeprefix("--property="), "") + "\n")
        if self.failing and " ".join(argv).startswith(self.failing):
            return Completed(1, stderr="Failed to connect to bus\n")
        return Completed(0)

    def commands(self, program: str) -> list[str]:
        """Return the recorded calls of `program` as command lines."""
        return [" ".join(call) for call in self.calls if call[0] == program]
