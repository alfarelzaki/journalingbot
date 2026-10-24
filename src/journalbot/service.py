"""Conversation and scheduling logic, independent of Telegram.

Talks to the user only through a `Messenger`, so the whole flow (prompt ->
messages -> Done -> summary, plus the scheduler) is testable with fakes.
All session state lives in SQLite, so a restart mid-entry loses nothing.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol

from journalbot import recaps
from journalbot.db import Store
from journalbot.exporter import build_export
from journalbot.formatting import HtmlFmt, render_daily, split_blocks
from journalbot.models import Entry, User
from journalbot.summarizer import Summarizer, SummaryError
from journalbot.timeutil import (
    UTC,
    get_zone,
    is_due,
    local_date,
    local_now,
    month_bounds,
    parse_hhmm,
    previous_month_key,
    previous_week,
    utcnow,
)

log = logging.getLogger(__name__)

Buttons = list[list[tuple[str, str]]]

PROMPT_GRACE = timedelta(hours=3)  # a missed tick may still fire the prompt this late
IDLE_FINALIZE = timedelta(minutes=60)  # silence after the last message -> summarize
NO_REPLY_EXPIRY = timedelta(hours=4)  # unanswered prompt -> skipped
RECAP_TIME = time(8, 0)
RETRY_EVERY_MINUTES = 30
DEFAULT_BACKUPS_KEPT = 14

PROMPTS = {
    "id": "🌙 Check-in malam. Hari ini ngapain aja? Tulis sebebas dan sepanjang yang kamu mau, boleh dikirim beberapa pesan. Tekan Selesai kalau sudah.",
    "en": "🌙 Evening check-in. What did you do today? Write as much or as little as you like, in as many messages as you want. Tap Done when you're finished.",
}
LABELS = {
    "id": {"done": "Selesai ✅", "skip": "Lewati hari ini"},
    "en": {"done": "Done ✅", "skip": "Skip today"},
}

HELP = """\
<b>Commands</b>
/now — start a journal entry now
/done — finish and summarize
/skip — skip today
/undo — remove your last message
/fix <i>text</i> — replace your last message
/today — today's summary
/history <i>[n]</i> — last n daily summaries
/week · /week redo — latest weekly recap / regenerate it
/month · /month redo — latest monthly review / regenerate it
/export <i>[raw] [YYYY-MM]</i> — organized zip of your journal
/delete <i>[YYYY-MM-DD]</i> — delete a day (asks to confirm)
/retry — retry failed summaries
/time <i>HH:MM</i> — nightly prompt time
/timezone <i>Area/City</i> — e.g. Asia/Jakarta
/pause · /resume — stop / restart nightly prompts"""


class Messenger(Protocol):
    async def send(self, user_id: int, text: str, buttons: Buttons | None = None) -> None: ...
    async def send_document(self, user_id: int, data: bytes, filename: str, caption: str = "") -> None: ...


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def _zone(user: User):
    try:
        return get_zone(user.timezone)
    except ValueError:
        return UTC


class JournalService:
    def __init__(
        self,
        store: Store,
        summarizer: Summarizer,
        messenger: Messenger,
        *,
        style_samples: int = 3,
        provider_name: str = "an AI provider",
        backup_dir: Path | None = None,
        backups_kept: int = DEFAULT_BACKUPS_KEPT,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.store = store
        self.summarizer = summarizer
        self.messenger = messenger
        self.style_samples = style_samples
        self.provider_name = provider_name
        self.backup_dir = backup_dir
        self.backups_kept = backups_kept
        self.clock = clock
        self.fmt = HtmlFmt()
        # The scheduler and the user's Done tap can race to summarize the same
        # entry; one lock plus a state re-check makes that harmless.
        self._lock = asyncio.Lock()

    # -- helpers ----------------------------------------------------------

    async def _say(self, user_id: int, text: str, buttons: Buttons | None = None) -> None:
        await self.messenger.send(user_id, text, buttons)

    async def _say_blocks(self, user_id: int, blocks: list[str]) -> None:
        for part in split_blocks(blocks):
            await self.messenger.send(user_id, part)

    def _user(self, user_id: int, now: datetime) -> User:
        return self.store.ensure_user(user_id, now)

    def _today(self, user: User, now: datetime) -> date:
        return local_date(now, _zone(user))

    def _open_entry(self, user_id: int) -> Entry | None:
        entries = self.store.open_entries(user_id)
        return entries[0] if entries else None

    def _prompt_buttons(self, lang: str) -> Buttons:
        labels = LABELS.get(lang, LABELS["en"])
        return [[(labels["done"], "done"), (labels["skip"], "skip")]]

    # -- onboarding & settings -------------------------------------------

    async def on_start(self, user_id: int) -> None:
        user = self._user(user_id, self.clock())
        await self._say(
            user_id,
            "Hi! I'm your journaling bot. 🌙\n\n"
            f"Every night at <b>{esc(user.prompt_time)}</b> ({esc(user.timezone)}) I'll ask what you did today. "
            "Reply in as many messages as you like, then tap Done and I'll sum it up as "
            "<b>Grateful → Mistakes → Learned</b>, in your own words.\n\n"
            "Every Monday you get a fun Weekly Wrapped, and on the 1st a Month in Review.\n\n"
            f"Privacy: your text is sent to {esc(self.provider_name)} to write the summaries, "
            "and Telegram bot chats are not end-to-end encrypted.\n\n"
            "Start tonight's entry any time with /now. /help lists everything.",
        )

    async def help(self, user_id: int) -> None:
        await self._say(user_id, HELP)

    async def set_time(self, user_id: int, arg: str) -> None:
        user = self._user(user_id, self.clock())
        try:
            t = parse_hhmm(arg)
        except ValueError as exc:
            await self._say(user_id, f"{esc(str(exc))} Example: /time 20:00")
            return
        value = f"{t.hour:02d}:{t.minute:02d}"
        self.store.update_user(user_id, prompt_time=value)
        await self._say(user_id, f"Got it. I'll ask you every night at <b>{value}</b> ({esc(user.timezone)}).")

    async def set_timezone(self, user_id: int, arg: str) -> None:
        self._user(user_id, self.clock())
        try:
            zone = get_zone(arg)
        except ValueError as exc:
            await self._say(user_id, f"{esc(str(exc))} Example: /timezone Asia/Jakarta")
            return
        self.store.update_user(user_id, timezone=str(zone))
        await self._say(user_id, f"Timezone set to <b>{esc(str(zone))}</b>.")

    async def pause(self, user_id: int) -> None:
        self._user(user_id, self.clock())
        self.store.update_user(user_id, paused=True)
        await self._say(user_id, "Paused. No nightly prompts or recaps until you send /resume. Your data is untouched.")

    async def resume(self, user_id: int) -> None:
        self._user(user_id, self.clock())
        self.store.update_user(user_id, paused=False)
        await self._say(user_id, "Welcome back! Nightly prompts are on again.")

    # -- sessions ---------------------------------------------------------

    async def start_session(self, user_id: int, now: datetime | None = None) -> None:
        now = now or self.clock()
        user = self._user(user_id, now)
        today = self._today(user, now).isoformat()
        entry = self.store.get_entry(user_id, today)
        if entry is None:
            self.store.create_entry(user_id, today, "prompted", "manual", now)
        elif entry.state in ("prompted", "collecting"):
            await self._say(user_id, "An entry is already open. Just keep writing, and tap Done when you're finished.")
            return
        elif entry.state == "summarized":
            self.store.reopen_entry(entry.id, now)
        else:  # skipped
            self.store.reprompt_entry(entry.id, now)
        await self._say(user_id, PROMPTS.get(user.language, PROMPTS["en"]), self._prompt_buttons(user.language))
        await self._flush_pending(user_id, now)

    async def _flush_pending(self, user_id: int, now: datetime) -> None:
        pending = self.store.kv_get(f"pending:{user_id}")
        entry = self._open_entry(user_id)
        if pending and entry:
            self.store.add_message(entry.id, pending, "text", now)
            self.store.kv_set(f"pending:{user_id}", "")

    async def add_text(self, user_id: int, text: str, now: datetime | None = None) -> bool:
        """Append a message to the open entry. Returns False if none is open
        (the text is parked so the 'Journal now' button doesn't lose it)."""
        now = now or self.clock()
        self._user(user_id, now)
        entry = self._open_entry(user_id)
        if entry is None:
            self.store.kv_set(f"pending:{user_id}", text)
            await self._say(
                user_id,
                "I'm not collecting an entry right now. Want to journal now? I'll keep what you just wrote.",
                [[("Journal now 📓", "now")]],
            )
            return False
        self.store.add_message(entry.id, text, "text", now)
        return True

    async def undo(self, user_id: int) -> None:
        entry = self._open_entry(user_id)
        if entry and self.store.remove_last_message(entry.id):
            await self._say(user_id, "Removed your last message.")
        else:
            await self._say(user_id, "Nothing to undo in the open entry.")

    async def fix(self, user_id: int, text: str) -> None:
        entry = self._open_entry(user_id)
        if not text.strip():
            await self._say(user_id, "Usage: /fix the corrected text")
        elif entry and self.store.replace_last_message(entry.id, text.strip()):
            await self._say(user_id, "Replaced your last message.")
        else:
            await self._say(user_id, "Nothing to fix in the open entry.")

    async def skip(self, user_id: int, now: datetime | None = None) -> None:
        now = now or self.clock()
        user = self._user(user_id, now)
        entry = self._open_entry(user_id)
        if entry and self.store.messages(entry.id):
            await self._say(user_id, "You've already written something. Tap Done (or /done) to summarize it.")
            return
        if entry:
            self.store.set_state(entry.id, "skipped")
        else:
            today = self._today(user, now).isoformat()
            if self.store.get_entry(user_id, today) is None:
                e = self.store.create_entry(user_id, today, "skipped", "manual", now)
                self.store.set_state(e.id, "skipped")
        await self._say(user_id, "Okay, skipping today. See you tomorrow. 🌿")

    async def finish(self, user_id: int, now: datetime | None = None) -> None:
        now = now or self.clock()
        self._user(user_id, now)
        entry = self._open_entry(user_id)
        if entry is None:
            await self._say(user_id, "No open entry. Send /now to start one.")
            return
        await self._finalize_locked(entry.id, now, explicit=True)

    async def _finalize_locked(self, entry_id: int, now: datetime, explicit: bool) -> None:
        async with self._lock:
            entry = self.store.get_entry_by_id(entry_id)
            if entry is None or entry.state not in ("prompted", "collecting"):
                return  # someone else already finished it
            await self._finalize(entry, now, explicit)

    async def _finalize(self, entry: Entry, now: datetime, explicit: bool) -> None:
        user_id = entry.user_id
        msgs = self.store.messages(entry.id)
        if not msgs:
            self.store.set_state(entry.id, "skipped")
            if explicit:
                await self._say(user_id, "You haven't written anything yet, so I'll skip today.")
            return
        has_summary = self.store.get_summary(entry.id) is not None
        newest = max(m.sent_at for m in msgs)
        if has_summary and entry.finished_at and newest <= entry.finished_at:
            self.store.set_state(entry.id, "summarized")
            if explicit:
                await self._say(user_id, "Nothing new since your last summary. /today shows it.")
            return
        await self._say(user_id, "✨ Putting it together…")
        await self._summarize(entry, now, announce_failure=True)

    async def _summarize(self, entry: Entry, now: datetime, announce_failure: bool, late: bool = False) -> bool:
        user_id = entry.user_id
        texts = [m.text for m in self.store.messages(entry.id)]
        samples = [
            t for t in self.store.recent_texts(user_id, entry.local_date, 10) if len(t.strip()) >= 20
        ][: self.style_samples]
        try:
            summary = await self.summarizer.summarize_day(texts, samples)
        except SummaryError as exc:
            log.error("summary failed for entry %s: %s", entry.id, type(exc).__name__)
            self.store.set_summary_status(entry.id, "failed")
            self.store.set_state(entry.id, "summarized", finished_at=now)
            if announce_failure:
                await self._say(
                    user_id,
                    "I saved your entry, but couldn't write the summary right now. "
                    "I'll keep retrying automatically, or send /retry.",
                )
            return False
        self.store.save_summary(entry.id, summary, now)
        self.store.set_state(entry.id, "summarized", finished_at=now)
        self.store.update_user(user_id, language=summary.language)
        blocks = render_daily(self.fmt, date.fromisoformat(entry.local_date), summary)
        if late:
            blocks.insert(0, "Here's the summary I couldn't finish earlier:")
        await self._say_blocks(user_id, blocks)
        return True

    # -- viewing ----------------------------------------------------------

    async def _show_entry(self, user_id: int, entry: Entry) -> None:
        summary = self.store.get_summary(entry.id)
        if summary:
            await self._say_blocks(user_id, render_daily(self.fmt, date.fromisoformat(entry.local_date), summary))
        elif entry.summary_status == "failed":
            await self._say(user_id, f"{entry.local_date}: saved, but not summarized yet. Try /retry.")
        else:
            await self._say(user_id, f"{entry.local_date}: no summary ({esc(entry.state)}).")

    async def today(self, user_id: int) -> None:
        now = self.clock()
        user = self._user(user_id, now)
        entry = self.store.get_entry(user_id, self._today(user, now).isoformat())
        if entry is None:
            await self._say(user_id, "Nothing for today yet. Send /now to start.")
        else:
            await self._show_entry(user_id, entry)

    async def history(self, user_id: int, arg: str) -> None:
        self._user(user_id, self.clock())
        n = 5
        if arg.strip():
            if not arg.strip().isdigit() or not 1 <= int(arg) <= 14:
                await self._say(user_id, "Usage: /history [1-14]")
                return
            n = int(arg)
        entries = self.store.recent_summarized(user_id, n)
        if not entries:
            await self._say(user_id, "No summaries yet.")
            return
        for entry in reversed(entries):
            await self._show_entry(user_id, entry)

    # -- weekly / monthly -------------------------------------------------

    def _journaled(self, user_id: int, start: date, end: date):
        out = []
        for e in self.store.entries_between(user_id, start, end):
            s = self.store.get_summary(e.id)
            if s:
                out.append((date.fromisoformat(e.local_date), s))
        return out

    async def _build_weekly(self, user_id: int, monday: date, now: datetime, announce: bool) -> bool:
        days = self._journaled(user_id, monday, monday + timedelta(days=6))
        if not days:
            self.store.save_weekly(user_id, monday, "-", {}, "empty", now)
            if announce:
                await self._say(user_id, "No journaled days in that week, so there's nothing to recap yet.")
            return False
        try:
            content = await recaps.generate_weekly(self.summarizer, monday, days)
        except SummaryError as exc:
            log.error("weekly recap failed: %s", type(exc).__name__)
            self.store.save_weekly(user_id, monday, "-", {}, "failed", now)
            if announce:
                await self._say(user_id, "I couldn't write the weekly recap right now. I'll retry automatically.")
            return False
        self.store.save_weekly(user_id, monday, content["format_name"], content, "ok", now)
        await self._say_blocks(user_id, recaps.render_weekly(self.fmt, monday, content))
        return True

    async def week(self, user_id: int, arg: str = "") -> None:
        now = self.clock()
        user = self._user(user_id, now)
        if arg.strip().lower() == "redo":
            monday, _ = previous_week(self._today(user, now))
            await self._build_weekly(user_id, monday, now, announce=True)
            return
        rows = [w for w in self.store.list_weekly(user_id) if w["status"] in ("ok", "stale")]
        if not rows:
            await self._say(user_id, "No weekly recap yet. The first one arrives Monday at 08:00 once you've journaled.")
            return
        row = rows[-1]
        blocks = recaps.render_weekly(self.fmt, date.fromisoformat(row["key"]), row["content"])
        if row["status"] == "stale":
            blocks.append(self.fmt.italic("This recap is out of date because a day changed. Send /week redo to regenerate."))
        await self._say_blocks(user_id, blocks)

    async def _build_monthly(self, user_id: int, month: str, now: datetime, announce: bool) -> bool:
        first, last = month_bounds(month)
        days = self._journaled(user_id, first, last)
        if not days:
            self.store.save_monthly(user_id, month, {}, "empty", now)
            if announce:
                await self._say(user_id, "No journaled days in that month, so there's nothing to review yet.")
            return False
        weeklies = [
            w for w in self.store.list_weekly(user_id)
            if w["status"] in ("ok", "stale") and first <= date.fromisoformat(w["key"]) <= last
        ]
        prev_key = previous_month_key(first)
        prev_row = self.store.get_monthly(user_id, prev_key)
        previous = prev_row["content"] if prev_row and prev_row["status"] in ("ok", "stale") else None
        try:
            content = await recaps.generate_monthly(self.summarizer, month, days, weeklies, previous)
        except SummaryError as exc:
            log.error("monthly review failed: %s", type(exc).__name__)
            self.store.save_monthly(user_id, month, {}, "failed", now)
            if announce:
                await self._say(user_id, "I couldn't write the monthly review right now. I'll retry automatically.")
            return False
        self.store.save_monthly(user_id, month, content, "ok", now)
        await self._say_blocks(user_id, recaps.render_monthly(self.fmt, month, content))
        return True

    async def month(self, user_id: int, arg: str = "") -> None:
        now = self.clock()
        user = self._user(user_id, now)
        if arg.strip().lower() == "redo":
            await self._build_monthly(user_id, previous_month_key(self._today(user, now)), now, announce=True)
            return
        rows = [m for m in self.store.list_monthly(user_id) if m["status"] in ("ok", "stale")]
        if not rows:
            await self._say(user_id, "No monthly review yet. The first one arrives on the 1st at 08:00 once you've journaled.")
            return
        row = rows[-1]
        blocks = recaps.render_monthly(self.fmt, row["key"], row["content"])
        if row["status"] == "stale":
            blocks.append(self.fmt.italic("This review is out of date because a day changed. Send /month redo to regenerate."))
        await self._say_blocks(user_id, blocks)

    # -- data control -----------------------------------------------------

    async def export(self, user_id: int, args: str) -> None:
        self._user(user_id, self.clock())
        tokens = args.split()
        raw = "raw" in [t.lower() for t in tokens]
        months = [t for t in tokens if len(t) == 7 and t[4] == "-" and (t[:4] + t[5:]).isdigit()]
        unknown = [t for t in tokens if t.lower() != "raw" and t not in months]
        if unknown or len(months) > 1:
            await self._say(user_id, "Usage: /export [raw] [YYYY-MM]")
            return
        data = build_export(self.store, user_id, include_raw=raw, month=months[0] if months else None)
        name = f"journal-export{'-' + months[0] if months else ''}.zip"
        caption = "Your journal export, with raw notes." if raw else "Your journal export (summaries only; add 'raw' to include your raw notes)."
        await self.messenger.send_document(user_id, data, name, caption)

    async def delete_request(self, user_id: int, arg: str) -> None:
        now = self.clock()
        user = self._user(user_id, now)
        target = arg.strip() or self._today(user, now).isoformat()
        try:
            date.fromisoformat(target)
        except ValueError:
            await self._say(user_id, "Usage: /delete [YYYY-MM-DD]")
            return
        if self.store.get_entry(user_id, target) is None:
            await self._say(user_id, f"There's no entry for {esc(target)}.")
            return
        await self._say(
            user_id,
            f"Delete <b>{esc(target)}</b> permanently, including your notes and summary?",
            [[("Yes, delete", f"del:{target}"), ("Cancel", "delno")]],
        )

    async def delete_confirmed(self, user_id: int, target: str) -> None:
        try:
            date.fromisoformat(target)
        except ValueError:
            return
        if self.store.delete_entry(user_id, target):
            await self._say(user_id, f"Deleted {esc(target)}. Any weekly/monthly recap that included it is marked out of date.")
        else:
            await self._say(user_id, "Already gone.")

    async def retry(self, user_id: int) -> None:
        now = self.clock()
        self._user(user_id, now)
        n = await self._retry_failed(user_id, now)
        await self._say(user_id, "Done. Nothing was waiting to be retried." if n == 0 else f"Retried {n} item(s).")

    async def _retry_failed(self, user_id: int, now: datetime) -> int:
        async with self._lock:
            return await self._retry_failed_unlocked(user_id, now)

    async def _retry_failed_unlocked(self, user_id: int, now: datetime) -> int:
        n = 0
        for entry in self.store.failed_entries(user_id):
            n += 1
            await self._summarize(entry, now, announce_failure=False, late=True)
        for w in self.store.list_weekly(user_id):
            if w["status"] == "failed":
                n += 1
                await self._build_weekly(user_id, date.fromisoformat(w["key"]), now, announce=False)
        for m in self.store.list_monthly(user_id):
            if m["status"] == "failed":
                n += 1
                await self._build_monthly(user_id, m["key"], now, announce=False)
        return n

    # -- scheduler --------------------------------------------------------

    async def tick(self, now: datetime | None = None) -> None:
        """Run everything that is due. Stateless and self-healing: it only
        looks at the clock and the database, so a restart or a missed minute
        is caught up on the next call."""
        now = now or self.clock()
        for user in self.store.list_users():
            if user.paused:
                continue
            for job in (self._tick_prompt, self._tick_sessions, self._tick_weekly, self._tick_monthly, self._tick_retries):
                try:
                    await job(user, now)
                except Exception as exc:  # one failing job must not stop the others
                    log.error("scheduler job %s failed: %s", job.__name__, type(exc).__name__)
        try:
            self._tick_backup(now)
        except Exception as exc:
            log.error("backup failed: %s", type(exc).__name__)

    async def _tick_prompt(self, user: User, now: datetime) -> None:
        tz = _zone(user)
        if not is_due(now, tz, parse_hhmm(user.prompt_time), PROMPT_GRACE):
            return
        today = local_date(now, tz).isoformat()
        if self.store.get_entry(user.telegram_id, today) is not None:
            return
        # Create first so a user's quick reply finds the open entry; roll back
        # if sending fails so the next tick retries instead of losing the prompt.
        self.store.create_entry(user.telegram_id, today, "prompted", "nightly", now)
        try:
            await self._say(user.telegram_id, PROMPTS.get(user.language, PROMPTS["en"]), self._prompt_buttons(user.language))
        except Exception:
            self.store.delete_entry(user.telegram_id, today)
            raise

    async def _tick_sessions(self, user: User, now: datetime) -> None:
        for entry in self.store.open_entries(user.telegram_id):
            if entry.state == "collecting" and entry.last_message_at and now - entry.last_message_at >= IDLE_FINALIZE:
                await self._finalize_locked(entry.id, now, explicit=False)
            elif entry.state == "prompted" and entry.prompted_at and now - entry.prompted_at >= NO_REPLY_EXPIRY:
                if entry.origin == "nightly":
                    self.store.set_state(entry.id, "skipped")
                else:  # a manual session nobody wrote in must not block tonight's prompt
                    self.store.delete_entry(entry.user_id, entry.local_date)

    def _recap_due(self, user: User, now: datetime) -> tuple[bool, date]:
        local = local_now(now, _zone(user))
        # Due from 08:00 on the first day of the period and on every later day,
        # so a bot that was offline Monday still delivers on Tuesday.
        return (local.weekday() > 0 or local.time() >= RECAP_TIME), local.date()

    async def _tick_weekly(self, user: User, now: datetime) -> None:
        due, today = self._recap_due(user, now)
        monday, _ = previous_week(today)
        if due and self.store.get_weekly(user.telegram_id, monday) is None:
            await self._build_weekly(user.telegram_id, monday, now, announce=False)

    async def _tick_monthly(self, user: User, now: datetime) -> None:
        local = local_now(now, _zone(user))
        if local.day == 1 and local.time() < RECAP_TIME:
            return
        month = previous_month_key(local.date())
        if self.store.get_monthly(user.telegram_id, month) is None:
            await self._build_monthly(user.telegram_id, month, now, announce=False)

    async def _tick_retries(self, user: User, now: datetime) -> None:
        # Throttled so an API outage costs a handful of calls, not one a minute.
        if now.minute % RETRY_EVERY_MINUTES == 0:
            await self._retry_failed(user.telegram_id, now)

    def _tick_backup(self, now: datetime) -> None:
        if self.backup_dir is None:
            return
        stamp = now.astimezone(UTC).strftime("%Y%m%d")
        if self.store.kv_get("last_backup") == stamp:
            return
        self.store.backup_to(self.backup_dir / f"journal-{stamp}.db")
        self.store.kv_set("last_backup", stamp)
        for old in sorted(self.backup_dir.glob("journal-*.db"))[: -self.backups_kept]:
            old.unlink()
