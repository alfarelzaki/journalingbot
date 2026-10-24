"""Time helpers. All storage is UTC; local time exists only for deciding
"which day is this" and when jobs are due.

Every conversion goes through zoneinfo so a timezone change (or DST) cannot
misfile an entry.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def parse_hhmm(value: str) -> time:
    """Parse a 24h 'HH:MM' string; raise ValueError on anything else."""
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts) or len(parts[0]) > 2 or len(parts[1]) != 2:
        raise ValueError("Use 24-hour HH:MM, for example 20:00.")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Hour must be 0-23 and minute 0-59.")
    return time(hour, minute)


def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError(f"Unknown timezone {name!r}. Use an IANA name like Asia/Jakarta.") from None


def local_now(now_utc: datetime, tz: ZoneInfo) -> datetime:
    return now_utc.astimezone(tz)


def local_date(now_utc: datetime, tz: ZoneInfo) -> date:
    return now_utc.astimezone(tz).date()


def is_due(now_utc: datetime, tz: ZoneInfo, at: time, grace: timedelta) -> bool:
    """True from local time `at` until `grace` later, on the same local day.

    The grace window lets a tick that was missed (restart, outage) still fire
    shortly afterwards, without ever firing hours late.
    """
    local = now_utc.astimezone(tz)
    start = datetime.combine(local.date(), at, tzinfo=tz)
    return start <= local < start + grace


def week_start(d: date) -> date:
    """Monday of the week containing `d`."""
    return d - timedelta(days=d.weekday())


def previous_week(today: date) -> tuple[date, date]:
    """(Monday, Sunday) of the week before the one containing `today`."""
    monday = week_start(today) - timedelta(days=7)
    return monday, monday + timedelta(days=6)


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_bounds(key: str) -> tuple[date, date]:
    year, month = int(key[:4]), int(key[5:7])
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return first, nxt - timedelta(days=1)


def previous_month_key(today: date) -> str:
    return month_key(today.replace(day=1) - timedelta(days=1))
