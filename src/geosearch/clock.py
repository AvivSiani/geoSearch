"""Wall-clock time for expiry (Stage 6). Everything that stamps or checks an
`expires_at` takes a Clock, so tests can move time without sleeping."""

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """PyMongo returns naive UTC datetimes by default; make them comparable
    with the aware ones the clock produces."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)
