import pytest

from daily_report.retry import RetriesExhaustedError, RetryableError, RetryPolicy


class Sleeps:
    """Records requested sleeps instead of sleeping."""

    def __init__(self) -> None:
        """Start with no sleeps."""
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        """Record one sleep."""
        self.seconds.append(seconds)


async def _always_unavailable() -> None:
    raise RetryableError.from_response(503, None)


@pytest.mark.asyncio
async def test_backs_off_1_2_4_seconds_plus_jitter_then_gives_up() -> None:
    """Without Retry-After, waits double from 1 s plus jitter and stops after 4 attempts."""
    sleeps = Sleeps()
    policy = RetryPolicy(sleep=sleeps, jitter=lambda: 0.25)

    with pytest.raises(RetriesExhaustedError, match=r"^HTTP 503 after 4 attempts$"):
        await policy.run(_always_unavailable)

    assert sleeps.seconds == [1.25, 2.25, 4.25]


@pytest.mark.asyncio
async def test_default_jitter_stays_under_half_a_second() -> None:
    """The default jitter adds between 0 and 0.5 s to each back-off."""
    sleeps = Sleeps()

    with pytest.raises(RetriesExhaustedError):
        await RetryPolicy(sleep=sleeps).run(_always_unavailable)

    for base, slept in zip([1, 2, 4], sleeps.seconds, strict=True):
        assert base <= slept <= base + 0.5


@pytest.mark.parametrize(
    ("header", "expected"),
    [("3", 3.0), ("Wed, 21 Oct 2026 07:28:00 GMT", None)],
)
def test_retry_after_reads_seconds_and_ignores_dates(header: str, expected: float | None) -> None:
    """Retry-After in seconds is honoured; an HTTP date falls back to back-off."""
    assert RetryableError.from_response(429, header).retry_after == expected
