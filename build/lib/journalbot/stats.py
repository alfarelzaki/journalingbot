"""Numbers for the recaps. Computed in code, never by the model: language
models miscount, and "5 of 7 days" must be true."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from journalbot.timeutil import WEEKDAYS


def streak_ending(days: set[date], end: date) -> int:
    """Consecutive journaled days counting back from `end` (0 if `end` is empty)."""
    n, d = 0, end
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n


def longest_streak(days: set[date]) -> int:
    best = run = 0
    prev: date | None = None
    for d in sorted(days):
        run = run + 1 if prev is not None and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    return best


def week_calendar(days: set[date], monday: date) -> str:
    return " ".join(
        f"{WEEKDAYS[i]}{'🟩' if monday + timedelta(days=i) in days else '⬜'}" for i in range(7)
    )


def top_weekday(days: set[date]) -> str | None:
    if not days:
        return None
    counts = Counter(d.weekday() for d in days)
    best = max(counts.values())
    # Earliest weekday wins a tie so the result is deterministic.
    return WEEKDAYS[min(i for i, c in counts.items() if c == best)]
