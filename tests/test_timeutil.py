from datetime import date, datetime, time, timedelta, timezone

import pytest

from journalbot.timeutil import (
    get_zone,
    is_due,
    local_date,
    month_bounds,
    month_key,
    parse_hhmm,
    previous_month_key,
    previous_week,
    week_start,
)

JAKARTA = get_zone("Asia/Jakarta")  # UTC+7, no DST
UTC = timezone.utc


def test_parse_hhmm_ok_and_bad():
    assert parse_hhmm("20:00") == time(20, 0)
    assert parse_hhmm("8:05") == time(8, 5)
    for bad in ["2000", "25:00", "20:60", "20:5", "ab:cd", "", "20:00:00"]:
        with pytest.raises(ValueError):
            parse_hhmm(bad)


def test_get_zone_rejects_unknown():
    with pytest.raises(ValueError):
        get_zone("Mars/Base")


def test_local_date_crosses_midnight_in_jakarta():
    # 23:50 UTC on the 1st is 06:50 on the 2nd in Jakarta.
    assert local_date(datetime(2026, 10, 1, 23, 50, tzinfo=UTC), JAKARTA) == date(2026, 10, 2)
    assert local_date(datetime(2026, 10, 1, 16, 59, tzinfo=UTC), JAKARTA) == date(2026, 10, 1)


def test_is_due_window():
    at, grace = time(20, 0), timedelta(hours=3)
    base = datetime(2026, 10, 24, 13, 0, tzinfo=UTC)  # 20:00 Jakarta
    assert is_due(base, JAKARTA, at, grace)
    assert not is_due(base - timedelta(minutes=1), JAKARTA, at, grace)
    assert is_due(base + timedelta(hours=2, minutes=59), JAKARTA, at, grace)
    assert not is_due(base + timedelta(hours=3), JAKARTA, at, grace)


def test_week_and_month_helpers():
    assert week_start(date(2026, 10, 24)) == date(2026, 10, 19)  # Sat -> Mon
    assert previous_week(date(2026, 10, 26)) == (date(2026, 10, 19), date(2026, 10, 25))
    assert month_key(date(2026, 1, 5)) == "2026-01"
    assert month_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    assert month_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))
    assert previous_month_key(date(2026, 1, 1)) == "2025-12"
