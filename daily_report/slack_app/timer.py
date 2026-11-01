from daily_report.effects.services import RealEffects
from daily_report.service.installer import Installer, SubprocessRunner


async def timer_next() -> str | None:
    """Return when the user timer next fires, or `None` when systemd does not know it."""
    return (await Installer(SubprocessRunner(), RealEffects()).status()).timer_next
