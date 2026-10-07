"""Source timestamp parsing with explicit time zone handling.

CRM timestamps are Europe/Berlin local time without offset; LXP timestamps are UTC (``Z``).
The canonical technical value is always a timezone-aware UTC instant; the source value is
kept next to it. Nothing is converted silently: unparseable, non-existent (spring-forward
gap) and ambiguous (autumn overlap) local times are reported so the caller can quarantine
the row and keep the original text.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..helpers.extraction_types import (
    TimestampResult,
)

UTC = timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")


def _parse_berlin_local_time(text: str) -> TimestampResult:
    """CRM timestamp: Berlin local time without offset. Converted to UTC unless the local time is impossible."""
    local = datetime.fromisoformat(text)
    if local.tzinfo is not None:
        return TimestampResult(
            None, None, "invalid", "CRM timestamp unexpectedly contains an offset"
        )

    # A local time can exist once, never (clock jumps forward) or twice (clock jumps back). Try both readings.
    readings = [local.replace(tzinfo=BERLIN, fold=fold) for fold in (0, 1)]
    possible = [
        r
        for r in readings
        if r.astimezone(UTC).astimezone(BERLIN).replace(tzinfo=None) == local
    ]
    if not possible:
        return TimestampResult(
            local, None, "invalid", "nonexistent Europe/Berlin local time"
        )
    if len(possible) == 2 and possible[0].utcoffset() != possible[1].utcoffset():
        return TimestampResult(
            local, None, "invalid", "ambiguous Europe/Berlin local time"
        )
    return TimestampResult(local, possible[0].astimezone(UTC), "parsed")


def _parse_utc_time(text: str) -> TimestampResult:
    """LXP timestamp: UTC, normally with a trailing ``Z``."""
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    instant = datetime.fromisoformat(text)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    instant = instant.astimezone(UTC)
    return TimestampResult(instant, instant, "parsed")


def parse_timestamp(value, source: str) -> TimestampResult:
    """Parse one source timestamp. ``source`` is ``CRM`` (Berlin local) or ``LXP`` (UTC)."""
    if value is None or str(value).strip() == "":
        return TimestampResult(None, None, "missing")
    text = str(value).strip()
    try:
        return (
            _parse_berlin_local_time(text) if source == "CRM" else _parse_utc_time(text)
        )
    except (TypeError, ValueError) as error:
        return TimestampResult(None, None, "invalid", str(error))
