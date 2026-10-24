import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from journalbot.db import Store
from journalbot.llm import LLMError
from journalbot.service import JournalService
from journalbot.summarizer import Summarizer
from tests.fakes import GOOD_DAILY, FakeLLM

UID = 42
UTC = timezone.utc
# 2026-10-24 is a Saturday. 13:00 UTC == 20:00 in Asia/Jakarta (UTC+7).
EVENING = datetime(2026, 10, 24, 13, 0, tzinfo=UTC)

WEEKLY_REPLY = json.dumps({"headline": "H", "lesson": "L", "side_quest": "S"})
MONTHLY_REPLY = json.dumps({"opener": "Great"})


class FakeMessenger:
    def __init__(self):
        self.sent: list[tuple[int, str, object]] = []
        self.docs: list[tuple[str, bytes, str]] = []
        self.fail = False

    async def send(self, user_id, text, buttons=None):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append((user_id, text, buttons))

    async def send_document(self, user_id, data, filename, caption=""):
        self.docs.append((filename, data, caption))

    @property
    def texts(self):
        return [t for _, t, _ in self.sent]

    @property
    def last(self):
        return self.sent[-1][1]


class Clock:
    def __init__(self, now=EVENING):
        self.now = now

    def __call__(self):
        return self.now


def make(*replies, store=None, messenger=None, clock=None, backup_dir=None):
    store = store or Store(":memory:")
    messenger = messenger or FakeMessenger()
    clock = clock or Clock()
    llm = FakeLLM(*replies)
    svc = JournalService(
        store, Summarizer(llm, "small", "big", retry_delay=0), messenger,
        style_samples=3, backup_dir=backup_dir, clock=clock,
    )
    svc.llm = llm
    svc.messenger_ = messenger
    return svc, store, messenger, clock


# -- core flow -------------------------------------------------------------

async def test_full_flow_manual_session():
    svc, store, m, clock = make(GOOD_DAILY)
    await svc.start_session(UID)
    assert "Evening check-in" in m.last or "Check-in" in m.last
    assert m.sent[-1][2][0][0][1] == "done"
    assert await svc.add_text(UID, "bangun telat tapi ibu sehat")
    assert await svc.add_text(UID, "belajar pasang alarm jauh")
    await svc.finish(UID)

    summary = m.last
    assert summary.index("Grateful") < summary.index("Mistakes") < summary.index("What I learned")
    assert "Aku telat bangun" in summary
    entry = store.get_entry(UID, "2026-10-24")
    assert entry.state == "summarized" and entry.summary_status == "ok"
    assert store.get_user(UID).language == "id"


async def test_text_without_session_is_parked_then_saved_by_button():
    svc, store, m, clock = make(GOOD_DAILY)
    assert not await svc.add_text(UID, "thought at 3pm")
    assert m.sent[-1][2] == [[("Journal now 📓", "now")]]
    await svc.start_session(UID)  # what the button does
    entry = store.get_entry(UID, "2026-10-24")
    assert [x.text for x in store.messages(entry.id)] == ["thought at 3pm"]


async def test_undo_and_fix():
    svc, store, m, _ = make()
    await svc.start_session(UID)
    await svc.add_text(UID, "one")
    await svc.add_text(UID, "two")
    await svc.fix(UID, "TWO!")
    await svc.undo(UID)
    entry = store.get_entry(UID, "2026-10-24")
    assert [x.text for x in store.messages(entry.id)] == ["one"]


async def test_done_with_nothing_written_skips():
    svc, store, m, _ = make()
    await svc.start_session(UID)
    await svc.finish(UID)
    assert store.get_entry(UID, "2026-10-24").state == "skipped"
    assert "skip" in m.last.lower()


async def test_skip_refuses_when_there_is_text():
    svc, store, m, _ = make()
    await svc.start_session(UID)
    await svc.add_text(UID, "something")
    await svc.skip(UID)
    assert store.get_entry(UID, "2026-10-24").state == "collecting"


async def test_reopen_without_new_text_does_not_resummarize():
    svc, store, m, clock = make(GOOD_DAILY)
    await svc.start_session(UID)
    await svc.add_text(UID, "hello")
    clock.now += timedelta(minutes=1)
    await svc.finish(UID, clock.now)
    n_calls = len(svc.llm.calls)
    clock.now += timedelta(minutes=5)
    await svc.start_session(UID, clock.now)  # reopen
    clock.now += timedelta(minutes=61)
    await svc.tick(clock.now)  # idle finalize with nothing new
    assert len(svc.llm.calls) == n_calls
    assert store.get_entry(UID, "2026-10-24").state == "summarized"


# -- failure handling ------------------------------------------------------

async def test_llm_outage_keeps_raw_entry_and_retry_recovers():
    svc, store, m, clock = make(LLMError("500"), LLMError("500"), GOOD_DAILY)
    await svc.start_session(UID)
    await svc.add_text(UID, "important thoughts")
    await svc.finish(UID)
    entry = store.get_entry(UID, "2026-10-24")
    assert entry.summary_status == "failed"
    assert [x.text for x in store.messages(entry.id)] == ["important thoughts"]
    assert "saved your entry" in m.last

    await svc.retry(UID)
    assert store.get_entry(UID, "2026-10-24").summary_status == "ok"
    assert any("couldn't finish earlier" in t for t in m.texts)


async def test_auto_retry_is_throttled_to_half_hours():
    svc, store, m, clock = make(LLMError("x"), LLMError("x"), GOOD_DAILY)
    await svc.start_session(UID)
    await svc.add_text(UID, "text")
    await svc.finish(UID)
    calls = len(svc.llm.calls)
    await svc.tick(clock.now.replace(minute=7))
    assert len(svc.llm.calls) == calls  # not a retry minute
    await svc.tick(clock.now.replace(minute=30))
    assert store.get_entry(UID, "2026-10-24").summary_status == "ok"


# -- scheduler -------------------------------------------------------------

async def test_nightly_prompt_fires_once_at_2000_jakarta():
    svc, store, m, clock = make()
    store.ensure_user(UID, EVENING)
    await svc.tick(EVENING - timedelta(minutes=1))
    assert m.sent == []
    await svc.tick(EVENING)
    await svc.tick(EVENING + timedelta(minutes=1))
    assert len(m.sent) == 1
    assert store.get_entry(UID, "2026-10-24").origin == "nightly"


async def test_prompt_catches_up_after_missed_tick_but_not_hours_late():
    svc, store, m, _ = make()
    store.ensure_user(UID, EVENING)
    await svc.tick(EVENING + timedelta(hours=2))
    assert len(m.sent) == 1
    svc2, store2, m2, _ = make()
    store2.ensure_user(UID, EVENING)
    await svc2.tick(EVENING + timedelta(hours=3, minutes=1))
    assert m2.sent == []


async def test_paused_user_gets_nothing():
    svc, store, m, _ = make()
    store.ensure_user(UID, EVENING)
    await svc.pause(UID)
    m.sent.clear()
    await svc.tick(EVENING)
    assert m.sent == []


async def test_failed_prompt_send_is_rolled_back_and_retried():
    svc, store, m, _ = make()
    store.ensure_user(UID, EVENING)
    m.fail = True
    await svc.tick(EVENING)
    assert store.get_entry(UID, "2026-10-24") is None
    m.fail = False
    await svc.tick(EVENING + timedelta(minutes=1))
    assert len(m.sent) == 1


async def test_idle_finalize_after_60_minutes():
    svc, store, m, clock = make(GOOD_DAILY)
    store.ensure_user(UID, EVENING)
    await svc.tick(EVENING)
    clock.now = EVENING + timedelta(minutes=5)
    await svc.add_text(UID, "my day", clock.now)
    await svc.tick(EVENING + timedelta(minutes=64))
    assert store.get_entry(UID, "2026-10-24").state == "collecting"
    await svc.tick(EVENING + timedelta(minutes=66))
    assert store.get_entry(UID, "2026-10-24").state == "summarized"


async def test_no_reply_expires_to_skipped_after_4_hours():
    svc, store, m, _ = make()
    store.ensure_user(UID, EVENING)
    await svc.tick(EVENING)
    await svc.tick(EVENING + timedelta(hours=3, minutes=59))
    assert store.get_entry(UID, "2026-10-24").state == "prompted"
    await svc.tick(EVENING + timedelta(hours=4))
    assert store.get_entry(UID, "2026-10-24").state == "skipped"
    assert len(m.sent) == 1  # no nagging


async def test_unused_manual_session_does_not_block_tonights_prompt():
    svc, store, m, clock = make()
    afternoon = EVENING - timedelta(hours=5)  # 15:00 Jakarta
    clock.now = afternoon
    await svc.start_session(UID, afternoon)
    await svc.tick(afternoon + timedelta(hours=4, minutes=1))  # 19:01, expired and removed
    assert store.get_entry(UID, "2026-10-24") is None
    await svc.tick(EVENING)
    assert store.get_entry(UID, "2026-10-24").origin == "nightly"


async def test_restart_mid_entry_loses_nothing():
    store = Store(":memory:")
    svc1, _, m1, clock = make(store=store)
    await svc1.start_session(UID)
    await svc1.add_text(UID, "before restart")
    svc2, _, m2, _ = make(GOOD_DAILY, store=store, clock=clock)  # a brand-new process
    await svc2.add_text(UID, "after restart")
    await svc2.finish(UID)
    entry = store.get_entry(UID, "2026-10-24")
    assert [x.text for x in store.messages(entry.id)] == ["before restart", "after restart"]
    assert entry.state == "summarized"


async def test_reply_after_midnight_belongs_to_the_prompted_day():
    svc, store, m, clock = make(GOOD_DAILY)
    store.ensure_user(UID, EVENING)
    await svc.tick(EVENING + timedelta(hours=2, minutes=59))  # 22:59 Jakarta: last minute of the window
    clock.now = EVENING + timedelta(hours=4, minutes=10)  # 00:10 on the 25th
    await svc.add_text(UID, "late thoughts", clock.now)
    entry = store.get_entry(UID, "2026-10-24")
    assert [x.text for x in store.messages(entry.id)] == ["late thoughts"]
    assert store.get_entry(UID, "2026-10-25") is None


# -- weekly / monthly delivery --------------------------------------------

def seed_days(store, days):
    from journalbot.models import DailySummary
    store.ensure_user(UID, EVENING)
    for d in days:
        e = store.create_entry(UID, d, "summarized", "nightly", EVENING)
        store.add_message(e.id, "x", "text", EVENING)
        store.set_state(e.id, "summarized", finished_at=EVENING)
        store.save_summary(e.id, DailySummary(grateful=["g"], language="en", model="m"), EVENING)


MON_0759 = datetime(2026, 10, 26, 0, 59, tzinfo=UTC)  # Monday 07:59 Jakarta
MON_0800 = datetime(2026, 10, 26, 1, 0, tzinfo=UTC)


async def test_weekly_recap_sent_monday_0800_once():
    svc, store, m, _ = make(WEEKLY_REPLY, MONTHLY_REPLY)
    seed_days(store, ["2026-10-19", "2026-10-20"])
    await svc.tick(MON_0759)
    assert not any("Weekly Wrapped" in t for t in m.texts)
    await svc.tick(MON_0800)
    await svc.tick(MON_0800 + timedelta(minutes=1))
    assert sum("Weekly Wrapped" in t for t in m.texts) == 1
    assert store.get_weekly(UID, datetime(2026, 10, 19).date())["status"] == "ok"


async def test_weekly_catches_up_later_in_the_week():
    svc, store, m, _ = make(WEEKLY_REPLY, MONTHLY_REPLY)
    seed_days(store, ["2026-10-19"])
    await svc.tick(MON_0800 + timedelta(days=2))
    assert any("Weekly Wrapped" in t for t in m.texts)


async def test_empty_week_is_recorded_not_retried_every_minute():
    svc, store, m, _ = make()
    store.ensure_user(UID, EVENING)
    await svc.tick(MON_0800)
    await svc.tick(MON_0800 + timedelta(minutes=1))
    assert store.get_weekly(UID, datetime(2026, 10, 19).date())["status"] == "empty"
    assert svc.llm.calls == [] and m.sent == []


async def test_monthly_review_sent_on_the_first():
    svc, store, m, _ = make(MONTHLY_REPLY)
    seed_days(store, ["2026-09-03", "2026-09-04"])
    first_0759 = datetime(2026, 10, 1, 0, 59, tzinfo=UTC)
    await svc.tick(first_0759)
    assert not any("Month in Review" in t for t in m.texts)
    await svc.tick(first_0759 + timedelta(minutes=1))
    assert any("Month in Review — September 2026" in t for t in m.texts)


async def test_failed_weekly_retries_and_delete_marks_stale():
    svc, store, m, _ = make(LLMError("x"), LLMError("x"), WEEKLY_REPLY)
    seed_days(store, ["2026-10-19"])
    monday = datetime(2026, 10, 19).date()
    await svc.tick(MON_0800 + timedelta(minutes=1))  # not a retry minute
    assert store.get_weekly(UID, monday)["status"] == "failed"
    await svc.retry(UID)
    assert store.get_weekly(UID, monday)["status"] == "ok"
    await svc.delete_confirmed(UID, "2026-10-19")
    assert store.get_weekly(UID, monday)["status"] == "stale"
    await svc.week(UID)
    assert "out of date" in m.last


# -- data control ----------------------------------------------------------

async def test_delete_flow_requires_confirmation():
    svc, store, m, _ = make(GOOD_DAILY)
    await svc.start_session(UID)
    await svc.add_text(UID, "private")
    await svc.finish(UID)
    await svc.delete_request(UID, "")
    assert m.sent[-1][2][0][0] == ("Yes, delete", "del:2026-10-24")
    assert store.get_entry(UID, "2026-10-24") is not None  # nothing deleted yet
    await svc.delete_confirmed(UID, "2026-10-24")
    assert store.get_entry(UID, "2026-10-24") is None
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


async def test_export_excludes_raw_by_default():
    svc, store, m, _ = make(GOOD_DAILY)
    await svc.start_session(UID)
    await svc.add_text(UID, "my secret raw words")
    await svc.finish(UID)

    await svc.export(UID, "")
    name, data, _ = m.docs[-1]
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    assert "journal-export/daily/2026/10/2026-10-24.md" in names
    assert "journal-export/data/summaries.csv" in names
    blob = b"".join(z.read(n) for n in names)
    assert b"my secret raw words" not in blob

    await svc.export(UID, "raw")
    z = zipfile.ZipFile(io.BytesIO(m.docs[-1][1]))
    assert b"my secret raw words" in b"".join(z.read(n) for n in z.namelist())


async def test_export_bad_args_and_settings_validation():
    svc, store, m, _ = make()
    await svc.export(UID, "everything")
    assert "Usage" in m.last
    await svc.set_time(UID, "25:00")
    assert "Hour" in m.last
    await svc.set_time(UID, "21:30")
    await svc.set_timezone(UID, "Mars/Base")
    assert "Unknown timezone" in m.last
    await svc.set_timezone(UID, "Asia/Makassar")
    u = store.get_user(UID)
    assert (u.prompt_time, u.timezone) == ("21:30", "Asia/Makassar")


async def test_backup_runs_once_per_day_and_rotates(tmp_path):
    svc, store, m, _ = make(backup_dir=tmp_path)
    store.ensure_user(UID, EVENING)
    for i in range(16):
        await svc.tick(EVENING + timedelta(days=i))
        await svc.tick(EVENING + timedelta(days=i, minutes=1))
    files = sorted(p.name for p in tmp_path.glob("journal-*.db"))
    assert len(files) == 14
    assert Store(tmp_path / files[-1]).get_user(UID) is not None


async def test_csv_formula_injection_is_neutralized():
    from journalbot.exporter import _csv_safe
    assert _csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert _csv_safe("normal") == "normal"


async def test_backup_count_is_configurable(tmp_path):
    store = Store(":memory:")
    svc = JournalService(
        store, Summarizer(FakeLLM(), "s", "b", retry_delay=0), FakeMessenger(),
        backup_dir=tmp_path, backups_kept=3, clock=Clock(),
    )
    store.ensure_user(UID, EVENING)
    for i in range(6):
        await svc.tick(EVENING + timedelta(days=i))
    assert len(list(tmp_path.glob("journal-*.db"))) == 3


async def test_messages_sent_while_offline_are_all_kept_with_one_offer():
    svc, store, m, clock = make(GOOD_DAILY)
    for t in ("first thought", "second thought", "third thought"):
        assert not await svc.add_text(UID, t)
    assert sum("Want to journal now" in x for x in m.texts) == 1
    await svc.start_session(UID)  # what the "Journal now" button does
    entry = store.get_entry(UID, "2026-10-24")
    assert [x.text for x in store.messages(entry.id)] == ["first thought\n\nsecond thought\n\nthird thought"]
    # and the parked text is cleared, so it can't leak into a later session
    assert not store.kv_get(f"pending:{UID}")
