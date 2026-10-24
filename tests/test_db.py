import sqlite3
from datetime import date, datetime, timezone

import pytest

from journalbot.db import Store
from journalbot.models import DailySummary

NOW = datetime(2026, 10, 24, 13, 0, tzinfo=timezone.utc)


@pytest.fixture
def store():
    s = Store(":memory:")
    s.ensure_user(1, NOW)
    return s


def test_defaults_for_new_user(store):
    u = store.get_user(1)
    assert (u.timezone, u.prompt_time, u.paused) == ("Asia/Jakarta", "20:00", False)


def test_one_entry_per_day(store):
    store.create_entry(1, "2026-10-24", "prompted", "nightly", NOW)
    with pytest.raises(sqlite3.IntegrityError):
        store.create_entry(1, "2026-10-24", "prompted", "nightly", NOW)


def test_messages_collect_and_edit(store):
    e = store.create_entry(1, "2026-10-24", "prompted", "nightly", NOW)
    store.add_message(e.id, "one", "text", NOW)
    store.add_message(e.id, "two", "text", NOW)
    assert store.get_entry(1, "2026-10-24").state == "collecting"
    assert store.replace_last_message(e.id, "TWO")
    assert [m.text for m in store.messages(e.id)] == ["one", "TWO"]
    assert store.remove_last_message(e.id)
    assert [m.text for m in store.messages(e.id)] == ["one"]


def test_delete_cascades_and_marks_recaps_stale(store):
    e = store.create_entry(1, "2026-10-24", "prompted", "nightly", NOW)
    store.add_message(e.id, "secret", "text", NOW)
    store.save_summary(e.id, DailySummary(grateful=["g"], model="m"), NOW)
    store.save_weekly(1, date(2026, 10, 19), "newspaper", {"x": 1}, "ok", NOW)
    store.save_monthly(1, "2026-10", {"x": 1}, "ok", NOW)

    assert store.delete_entry(1, "2026-10-24")

    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0] == 0
    assert store.get_weekly(1, date(2026, 10, 19))["status"] == "stale"
    assert store.get_monthly(1, "2026-10")["status"] == "stale"
    assert not store.delete_entry(1, "2026-10-24")


def test_resummarizing_marks_recaps_stale(store):
    e = store.create_entry(1, "2026-10-24", "prompted", "nightly", NOW)
    store.save_weekly(1, date(2026, 10, 19), "newspaper", {}, "ok", NOW)
    store.save_summary(e.id, DailySummary(model="m"), NOW)
    assert store.get_weekly(1, date(2026, 10, 19))["status"] == "stale"


def test_summary_roundtrip_keeps_unicode(store):
    e = store.create_entry(1, "2026-10-24", "prompted", "nightly", NOW)
    s = DailySummary(grateful=["Aku bersyukur ☕"], empty_note="Nggak ada.", language="id", model="m")
    store.save_summary(e.id, s, NOW)
    got = store.get_summary(e.id)
    assert got.grateful == ["Aku bersyukur ☕"] and got.language == "id"
    assert store.get_entry_by_id(e.id).summary_status == "ok"


def test_style_samples_only_from_past_days(store):
    old = store.create_entry(1, "2026-10-20", "prompted", "nightly", NOW)
    store.add_message(old.id, "old text", "text", NOW)
    cur = store.create_entry(1, "2026-10-24", "prompted", "nightly", NOW)
    store.add_message(cur.id, "today text", "text", NOW)
    assert store.recent_texts(1, "2026-10-24", 3) == ["old text"]


def test_update_user_rejects_unknown_field(store):
    with pytest.raises(ValueError):
        store.update_user(1, evil="x")
    store.update_user(1, paused=True, timezone="UTC")
    u = store.get_user(1)
    assert u.paused and u.timezone == "UTC"


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "j.db"
    Store(path).ensure_user(1, NOW)
    s2 = Store(path)  # reopening must not recreate tables or lose data
    assert s2.get_user(1) is not None


def test_backup_copies_data(store, tmp_path):
    store.backup_to(tmp_path / "b" / "copy.db")
    assert Store(tmp_path / "b" / "copy.db").get_user(1) is not None
