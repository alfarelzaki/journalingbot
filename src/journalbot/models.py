from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class User:
    telegram_id: int
    timezone: str
    prompt_time: str
    paused: bool
    language: str  # last detected journal language: 'id', 'en', ...


@dataclass
class Entry:
    id: int
    user_id: int
    local_date: str  # YYYY-MM-DD in the user's timezone at prompt time
    state: str  # prompted | collecting | summarized | skipped
    origin: str  # nightly | manual
    prompted_at: datetime | None
    last_message_at: datetime | None
    finished_at: datetime | None
    summary_status: str  # none | ok | failed


@dataclass
class Message:
    id: int
    entry_id: int
    source: str  # text | voice
    text: str
    sent_at: datetime


@dataclass
class DailySummary:
    grateful: list[str] = field(default_factory=list)
    mistakes: list[str] = field(default_factory=list)
    learned: list[str] = field(default_factory=list)
    empty_note: str = ""  # "nothing mentioned" phrase in the user's language
    language: str = "en"
    model: str = ""
