"""SQLite storage. The database is the single source of truth; exports are
derived copies.

One connection is shared by the whole (single-threaded, asyncio) process.
Queries here take microseconds for a one-person journal, so they are called
directly from async code rather than pushed to a thread.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from journalbot.models import DailySummary, Entry, Message, User
from journalbot.timeutil import from_iso, month_key, to_iso, week_start

DEFAULT_TIMEZONE = "Asia/Jakarta"
DEFAULT_PROMPT_TIME = "20:00"

MIGRATIONS = [
    """
    CREATE TABLE users(
        telegram_id INTEGER PRIMARY KEY,
        timezone    TEXT NOT NULL,
        prompt_time TEXT NOT NULL,
        paused      INTEGER NOT NULL DEFAULT 0,
        language    TEXT NOT NULL DEFAULT 'en',
        created_at  TEXT NOT NULL
    );
    CREATE TABLE entries(
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL REFERENCES users(telegram_id),
        local_date      TEXT NOT NULL,
        state           TEXT NOT NULL CHECK(state IN ('prompted','collecting','summarized','skipped')),
        origin          TEXT NOT NULL CHECK(origin IN ('nightly','manual')),
        prompted_at     TEXT,
        last_message_at TEXT,
        finished_at     TEXT,
        summary_status  TEXT NOT NULL DEFAULT 'none' CHECK(summary_status IN ('none','ok','failed')),
        UNIQUE(user_id, local_date)
    );
    CREATE TABLE messages(
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
        source   TEXT NOT NULL DEFAULT 'text' CHECK(source IN ('text','voice')),
        text     TEXT NOT NULL,
        sent_at  TEXT NOT NULL
    );
    CREATE TABLE summaries(
        entry_id   INTEGER PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
        grateful   TEXT NOT NULL,
        mistakes   TEXT NOT NULL,
        learned    TEXT NOT NULL,
        empty_note TEXT NOT NULL DEFAULT '',
        language   TEXT NOT NULL DEFAULT 'en',
        model      TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE weekly_recaps(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES users(telegram_id),
        week_start  TEXT NOT NULL,
        format_name TEXT NOT NULL,
        content     TEXT NOT NULL,
        status      TEXT NOT NULL CHECK(status IN ('ok','stale','failed','empty')),
        created_at  TEXT NOT NULL,
        UNIQUE(user_id, week_start)
    );
    CREATE TABLE monthly_reviews(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(telegram_id),
        month      TEXT NOT NULL,
        content    TEXT NOT NULL,
        status     TEXT NOT NULL CHECK(status IN ('ok','stale','failed','empty')),
        created_at TEXT NOT NULL,
        UNIQUE(user_id, month)
    );
    CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
]

_USER_FIELDS = {"timezone", "prompt_time", "paused", "language"}


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    # -- schema -----------------------------------------------------------

    def _migrate(self) -> None:
        # A version table lets future releases change the schema without
        # touching an existing journal's data.
        self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)")
        row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = row["v"] or 0
        for number, script in enumerate(MIGRATIONS, start=1):
            if number > current:
                self.conn.executescript(script)
                self.conn.execute("INSERT INTO schema_version(version) VALUES (?)", (number,))
                self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def backup_to(self, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(dest))
        try:
            self.conn.backup(target)
        finally:
            target.close()

    # -- users ------------------------------------------------------------

    def ensure_user(self, telegram_id: int, now: datetime) -> User:
        self.conn.execute(
            "INSERT OR IGNORE INTO users(telegram_id, timezone, prompt_time, created_at) VALUES (?,?,?,?)",
            (telegram_id, DEFAULT_TIMEZONE, DEFAULT_PROMPT_TIME, to_iso(now)),
        )
        self.conn.commit()
        user = self.get_user(telegram_id)
        assert user is not None
        return user

    def get_user(self, telegram_id: int) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        return _user(row) if row else None

    def list_users(self) -> list[User]:
        return [_user(r) for r in self.conn.execute("SELECT * FROM users ORDER BY telegram_id")]

    def update_user(self, telegram_id: int, **fields: Any) -> None:
        bad = set(fields) - _USER_FIELDS
        if bad:
            raise ValueError(f"Unknown user fields: {sorted(bad)}")
        if not fields:
            return
        assigns = ", ".join(f"{k}=?" for k in fields)
        values = [int(v) if isinstance(v, bool) else v for v in fields.values()]
        self.conn.execute(f"UPDATE users SET {assigns} WHERE telegram_id=?", (*values, telegram_id))
        self.conn.commit()

    # -- entries ----------------------------------------------------------

    def get_entry(self, user_id: int, local_date: str) -> Entry | None:
        row = self.conn.execute(
            "SELECT * FROM entries WHERE user_id=? AND local_date=?", (user_id, local_date)
        ).fetchone()
        return _entry(row) if row else None

    def get_entry_by_id(self, entry_id: int) -> Entry | None:
        row = self.conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        return _entry(row) if row else None

    def create_entry(self, user_id: int, local_date: str, state: str, origin: str, now: datetime) -> Entry:
        self.conn.execute(
            "INSERT INTO entries(user_id, local_date, state, origin, prompted_at) VALUES (?,?,?,?,?)",
            (user_id, local_date, state, origin, to_iso(now)),
        )
        self.conn.commit()
        entry = self.get_entry(user_id, local_date)
        assert entry is not None
        return entry

    def reopen_entry(self, entry_id: int, now: datetime) -> None:
        """Start another session on an already-summarized day (spec: append).

        finished_at is kept on purpose: comparing it with the newest message
        is how we know whether anything new needs summarizing.
        """
        self.conn.execute(
            "UPDATE entries SET state='collecting', last_message_at=? WHERE id=?",
            (to_iso(now), entry_id),
        )
        self.conn.commit()

    def reprompt_entry(self, entry_id: int, now: datetime) -> None:
        """Reopen a skipped day: the user changed their mind."""
        self.conn.execute(
            "UPDATE entries SET state='prompted', prompted_at=? WHERE id=?", (to_iso(now), entry_id)
        )
        self.conn.commit()

    def set_state(self, entry_id: int, state: str, finished_at: datetime | None = None) -> None:
        self.conn.execute(
            "UPDATE entries SET state=?, finished_at=COALESCE(?, finished_at) WHERE id=?",
            (state, to_iso(finished_at) if finished_at else None, entry_id),
        )
        self.conn.commit()

    def open_entries(self, user_id: int) -> list[Entry]:
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE user_id=? AND state IN ('prompted','collecting') ORDER BY local_date DESC",
            (user_id,),
        )
        return [_entry(r) for r in rows]

    def entries_between(self, user_id: int, start: date, end: date) -> list[Entry]:
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE user_id=? AND local_date BETWEEN ? AND ? ORDER BY local_date",
            (user_id, start.isoformat(), end.isoformat()),
        )
        return [_entry(r) for r in rows]

    def recent_summarized(self, user_id: int, limit: int) -> list[Entry]:
        rows = self.conn.execute(
            "SELECT e.* FROM entries e JOIN summaries s ON s.entry_id=e.id "
            "WHERE e.user_id=? ORDER BY e.local_date DESC LIMIT ?",
            (user_id, limit),
        )
        return [_entry(r) for r in rows]

    def failed_entries(self, user_id: int) -> list[Entry]:
        rows = self.conn.execute(
            "SELECT * FROM entries WHERE user_id=? AND summary_status='failed' ORDER BY local_date", (user_id,)
        )
        return [_entry(r) for r in rows]

    def delete_entry(self, user_id: int, local_date: str) -> bool:
        entry = self.get_entry(user_id, local_date)
        if entry is None:
            return False
        self._mark_stale(user_id, local_date)
        self.conn.execute("DELETE FROM entries WHERE id=?", (entry.id,))  # cascades to messages/summaries
        self.conn.commit()
        return True

    # -- messages ---------------------------------------------------------

    def add_message(self, entry_id: int, text: str, source: str, now: datetime) -> None:
        self.conn.execute(
            "INSERT INTO messages(entry_id, source, text, sent_at) VALUES (?,?,?,?)",
            (entry_id, source, text, to_iso(now)),
        )
        self.conn.execute(
            "UPDATE entries SET state='collecting', last_message_at=? WHERE id=?", (to_iso(now), entry_id)
        )
        self.conn.commit()

    def messages(self, entry_id: int) -> list[Message]:
        rows = self.conn.execute("SELECT * FROM messages WHERE entry_id=? ORDER BY id", (entry_id,))
        return [Message(r["id"], r["entry_id"], r["source"], r["text"], from_iso(r["sent_at"])) for r in rows]

    def remove_last_message(self, entry_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM messages WHERE id=(SELECT MAX(id) FROM messages WHERE entry_id=?)", (entry_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def replace_last_message(self, entry_id: int, text: str) -> bool:
        cur = self.conn.execute(
            "UPDATE messages SET text=? WHERE id=(SELECT MAX(id) FROM messages WHERE entry_id=?)",
            (text, entry_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def recent_texts(self, user_id: int, before_date: str, limit: int) -> list[str]:
        """Most recent past messages, used only as a writing-style sample."""
        rows = self.conn.execute(
            "SELECT m.text FROM messages m JOIN entries e ON e.id=m.entry_id "
            "WHERE e.user_id=? AND e.local_date<? ORDER BY m.sent_at DESC LIMIT ?",
            (user_id, before_date, limit),
        )
        return [r["text"] for r in rows]

    # -- summaries --------------------------------------------------------

    def save_summary(self, entry_id: int, summary: DailySummary, now: datetime) -> None:
        entry = self.get_entry_by_id(entry_id)
        assert entry is not None
        self.conn.execute(
            "INSERT OR REPLACE INTO summaries(entry_id, grateful, mistakes, learned, empty_note, language, model, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                entry_id,
                json.dumps(summary.grateful, ensure_ascii=False),
                json.dumps(summary.mistakes, ensure_ascii=False),
                json.dumps(summary.learned, ensure_ascii=False),
                summary.empty_note,
                summary.language,
                summary.model,
                to_iso(now),
            ),
        )
        self.conn.execute("UPDATE entries SET summary_status='ok' WHERE id=?", (entry_id,))
        self._mark_stale(entry.user_id, entry.local_date)
        self.conn.commit()

    def get_summary(self, entry_id: int) -> DailySummary | None:
        r = self.conn.execute("SELECT * FROM summaries WHERE entry_id=?", (entry_id,)).fetchone()
        if r is None:
            return None
        return DailySummary(
            grateful=json.loads(r["grateful"]),
            mistakes=json.loads(r["mistakes"]),
            learned=json.loads(r["learned"]),
            empty_note=r["empty_note"],
            language=r["language"],
            model=r["model"],
        )

    def set_summary_status(self, entry_id: int, status: str) -> None:
        self.conn.execute("UPDATE entries SET summary_status=? WHERE id=?", (status, entry_id))
        self.conn.commit()

    # -- weekly / monthly -------------------------------------------------

    def save_weekly(self, user_id: int, week_start_: date, format_name: str, content: dict, status: str, now: datetime) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO weekly_recaps(user_id, week_start, format_name, content, status, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (user_id, week_start_.isoformat(), format_name, json.dumps(content, ensure_ascii=False), status, to_iso(now)),
        )
        self.conn.commit()

    def get_weekly(self, user_id: int, week_start_: date) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM weekly_recaps WHERE user_id=? AND week_start=?", (user_id, week_start_.isoformat())
        ).fetchone()
        return _recap(r, "week_start") if r else None

    def list_weekly(self, user_id: int) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM weekly_recaps WHERE user_id=? ORDER BY week_start", (user_id,))
        return [_recap(r, "week_start") for r in rows]

    def save_monthly(self, user_id: int, month: str, content: dict, status: str, now: datetime) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO monthly_reviews(user_id, month, content, status, created_at) VALUES (?,?,?,?,?)",
            (user_id, month, json.dumps(content, ensure_ascii=False), status, to_iso(now)),
        )
        self.conn.commit()

    def get_monthly(self, user_id: int, month: str) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM monthly_reviews WHERE user_id=? AND month=?", (user_id, month)
        ).fetchone()
        return _recap(r, "month") if r else None

    def list_monthly(self, user_id: int) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM monthly_reviews WHERE user_id=? ORDER BY month", (user_id,))
        return [_recap(r, "month") for r in rows]

    def _mark_stale(self, user_id: int, local_date: str) -> None:
        """A recap built from days that later change must not look current."""
        d = date.fromisoformat(local_date)
        self.conn.execute(
            "UPDATE weekly_recaps SET status='stale' WHERE user_id=? AND week_start=? AND status='ok'",
            (user_id, week_start(d).isoformat()),
        )
        self.conn.execute(
            "UPDATE monthly_reviews SET status='stale' WHERE user_id=? AND month=? AND status='ok'",
            (user_id, month_key(d)),
        )

    # -- key/value --------------------------------------------------------

    def kv_get(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def kv_set(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?,?)", (key, value))
        self.conn.commit()


def _user(r: sqlite3.Row) -> User:
    return User(r["telegram_id"], r["timezone"], r["prompt_time"], bool(r["paused"]), r["language"])


def _opt(value: str | None) -> datetime | None:
    return from_iso(value) if value else None


def _entry(r: sqlite3.Row) -> Entry:
    return Entry(
        r["id"], r["user_id"], r["local_date"], r["state"], r["origin"],
        _opt(r["prompted_at"]), _opt(r["last_message_at"]), _opt(r["finished_at"]), r["summary_status"],
    )


def _recap(r: sqlite3.Row, key: str) -> dict:
    out = {"key": r[key], "status": r["status"], "content": json.loads(r["content"]), "created_at": r["created_at"]}
    if "format_name" in r.keys():
        out["format_name"] = r["format_name"]
    return out

