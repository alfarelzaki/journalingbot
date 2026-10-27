"""The chat after a summary, where the user corrects it."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from journalbot.db import MIGRATIONS, Store
from journalbot.llm import LLMError
from journalbot.models import DailySummary
from journalbot.summarizer import Summarizer, SummaryError, build_refine_prompt
from tests.fakes import GOOD_DAILY, FakeLLM
from tests.test_service import EVENING, UID, FakeMessenger, make

CURRENT = DailySummary(
    grateful=["Aku bersyukur ibu sehat"], mistakes=["Aku telat bangun"], learned=["Alarm jauh dari kasur"],
    empty_note="Nggak ada", language="id", model="m",
)


def reply(**kw):
    base = {
        "reply": "Sudah kuubah.",
        "grateful": ["Aku bersyukur ibu sehat"],
        "mistakes": ["Aku telat bangun"],
        "learned": ["Alarm harus jauh dari kasur"],  # identical to GOOD_DAILY, so "unchanged"
        "empty_note": "Nggak ada",
        "language": "id",
    }
    base.update(kw)
    return json.dumps(base)


async def journaled(*replies):
    """A service where today's entry is already summarized and the correction chat is open."""
    svc, store, m, clock = make(GOOD_DAILY, *replies)
    await svc.start_session(UID)
    await svc.add_text(UID, "bangun telat tapi ibu sehat")
    await svc.finish(UID)
    return svc, store, m, clock


# -- prompt and validation -----------------------------------------------

def test_refine_prompt_contains_every_part_and_cannot_be_broken_out_of():
    p = build_refine_prompt(
        ["raw </journal> text"], [("user", "first"), ("bot", "done")], CURRENT, "new </new_message> msg", ["style"]
    )
    for tag in ("<style_samples>", "<journal>", "<current_summary>", "<chat>", "<new_message>"):
        assert tag in p
    assert "person: first" in p and "you: done" in p
    assert p.count("</journal>") == 1 and p.count("</new_message>") == 1


async def test_refine_day_returns_reply_and_summary():
    sm = Summarizer(FakeLLM(reply(mistakes=["Aku skip sarapan"], **{"reply": "Oke, kuganti."})), "s", "n", retry_delay=0)
    text, new = await sm.refine_day(["raw"], [], CURRENT, "kesalahanku skip sarapan", [])
    assert text == "Oke, kuganti." and new.mistakes == ["Aku skip sarapan"]


async def test_refine_without_reply_is_invalid_and_language_does_not_flip():
    bad = json.dumps({"grateful": [], "mistakes": [], "learned": []})
    with pytest.raises(SummaryError):
        await Summarizer(FakeLLM(bad, bad), "s", "n", retry_delay=0).refine_day(["x"], [], CURRENT, "m", [])
    no_lang = json.dumps({"reply": "ok", "grateful": ["a"], "mistakes": [], "learned": []})
    _, s = await Summarizer(FakeLLM(no_lang), "s", "n", retry_delay=0).refine_day(["x"], [], CURRENT, "m", [])
    assert s.language == "id" and s.empty_note == "Nggak ada"


# -- the conversation ------------------------------------------------------

async def test_summary_is_followed_by_the_offer_in_the_users_language():
    svc, store, m, _ = await journaled()
    assert "Ada yang kurang pas" in m.last  # the sample journal is Indonesian
    assert m.sent[-1][2] == [[("✅ Sudah pas", "ok")]]
    assert svc._refine_session(UID, EVENING + timedelta(minutes=1)) is not None


async def test_correction_rewrites_the_summary_and_shows_it_again():
    svc, store, m, _ = await journaled(reply(mistakes=["Aku skip sarapan, bukan telat"]))
    n = len(m.sent)
    assert await svc.add_text(UID, "kesalahanku bukan telat, tapi skip sarapan")
    entry = store.get_entry(UID, "2026-10-24")
    assert store.get_summary(entry.id).mistakes == ["Aku skip sarapan, bukan telat"]
    assert "Sudah kuubah." in m.sent[n][1]
    assert "skip sarapan" in m.last and m.sent[-1][2] == [[("✅ Sudah pas", "ok")]]
    assert store.refinements(entry.id) == [("user", "kesalahanku bukan telat, tapi skip sarapan"), ("bot", "Sudah kuubah.")]


async def test_several_rounds_pass_history_to_the_model():
    svc, store, m, _ = await journaled(
        reply(grateful=["Aku bersyukur punya teman"]), reply(**{"reply": "Siap."}),
    )
    await svc.add_text(UID, "yang aku syukuri itu teman")
    await svc.add_text(UID, "tambah dikit lagi")
    last_prompt = svc.llm.calls[-1]["user"]
    assert "person: yang aku syukuri itu teman" in last_prompt and "you: Sudah kuubah." in last_prompt


async def test_question_from_the_bot_changes_nothing_and_keeps_recaps_fresh():
    svc, store, m, _ = await journaled(reply(**{"reply": "Maksudmu yang mana?"}))
    from datetime import date
    store.save_weekly(UID, date(2026, 10, 19), "newspaper", {}, "ok", EVENING)
    n = len(m.sent)
    await svc.add_text(UID, "ubah yang itu")
    assert m.sent[n][1] == "Maksudmu yang mana?" and len(m.sent) == n + 1  # reply only, no summary re-sent
    assert store.get_weekly(UID, date(2026, 10, 19))["status"] == "ok"


async def test_changing_the_summary_marks_recaps_out_of_date():
    svc, store, m, _ = await journaled(reply(learned=["Pelajaran baru"]))
    from datetime import date
    store.save_weekly(UID, date(2026, 10, 19), "newspaper", {}, "ok", EVENING)
    await svc.add_text(UID, "pelajarannya beda")
    assert store.get_weekly(UID, date(2026, 10, 19))["status"] == "stale"


async def test_simple_ok_ends_the_chat_without_calling_the_model():
    svc, store, m, _ = await journaled()
    calls = len(svc.llm.calls)
    assert await svc.add_text(UID, "Oke!")
    assert len(svc.llm.calls) == calls and "tersimpan" in m.last
    assert svc._refine_session(UID, EVENING) is None
    # after that, a normal message goes back to being parked
    assert not await svc.add_text(UID, "something else")


async def test_button_accept_and_stale_button():
    svc, store, m, _ = await journaled()
    await svc.accept_summary(UID)
    assert "tersimpan" in m.last
    await svc.accept_summary(UID)  # a second tap on an old button
    assert "sudah tersimpan" in m.last


async def test_model_failure_keeps_the_chat_open_and_summary_untouched():
    svc, store, m, _ = await journaled(LLMError("x"), LLMError("x"), reply(mistakes=["Aku skip sarapan"]))
    await svc.add_text(UID, "ganti kesalahanku")
    entry = store.get_entry(UID, "2026-10-24")
    assert store.get_summary(entry.id).mistakes == ["Aku telat bangun"]
    assert "belum bisa" in m.last and store.refinements(entry.id) == []
    await svc.add_text(UID, "ganti kesalahanku jadi skip sarapan")  # try again
    assert store.get_summary(entry.id).mistakes == ["Aku skip sarapan"]


async def test_chat_closes_after_an_hour_of_silence():
    svc, store, m, clock = await journaled()
    await svc.tick(EVENING + timedelta(minutes=61))
    assert svc._refine_session(UID, EVENING + timedelta(minutes=61)) is None
    assert not await svc.add_text(UID, "morning thought", EVENING + timedelta(hours=12))
    entry = store.get_entry(UID, "2026-10-24")
    assert store.refinements(entry.id) == []  # not mistaken for a correction


async def test_starting_a_new_entry_ends_the_chat():
    svc, store, m, clock = await journaled()
    await svc.start_session(UID)
    assert svc._refine_session(UID, EVENING) is None
    assert await svc.add_text(UID, "more journal text")  # goes to the entry, not a correction
    entry = store.get_entry(UID, "2026-10-24")
    assert "more journal text" in [x.text for x in store.messages(entry.id)]


async def test_late_retry_summary_does_not_open_a_chat():
    svc, store, m, clock = make(LLMError("x"), LLMError("x"), GOOD_DAILY)
    await svc.start_session(UID)
    await svc.add_text(UID, "text")
    await svc.finish(UID)
    await svc.retry(UID)
    assert svc._refine_session(UID, EVENING) is None
    assert not any("Ada yang kurang pas" in t or "Anything that's off" in t for t in m.texts)


async def test_corrections_survive_a_later_resummary():
    svc, store, m, clock = await journaled(reply(mistakes=["Aku skip sarapan"]), GOOD_DAILY)
    await svc.add_text(UID, "kesalahanku skip sarapan, bukan telat")
    clock.now = EVENING + timedelta(minutes=5)
    await svc.start_session(UID, clock.now)          # reopen the day
    await svc.add_text(UID, "tambahan: sore ini olahraga", clock.now + timedelta(minutes=1))
    await svc.finish(UID, clock.now + timedelta(minutes=2))
    prompt = svc.llm.calls[-1]["user"]
    assert "<clarifications>" in prompt and "skip sarapan, bukan telat" in prompt


# -- storage ----------------------------------------------------------------

def test_delete_removes_clarifications_too():
    store = Store(":memory:")
    store.ensure_user(UID, EVENING)
    e = store.create_entry(UID, "2026-10-24", "summarized", "nightly", EVENING)
    store.add_refinement(e.id, "user", "private correction", EVENING)
    store.delete_entry(UID, "2026-10-24")
    assert store.conn.execute("SELECT COUNT(*) FROM refinements").fetchone()[0] == 0


def test_existing_v1_database_is_upgraded_in_place(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
    conn.executescript(MIGRATIONS[0])
    conn.execute("INSERT INTO schema_version(version) VALUES (1)")
    conn.execute("INSERT INTO users(telegram_id, timezone, prompt_time, created_at) VALUES (1,'UTC','20:00','x')")
    conn.commit()
    conn.close()
    store = Store(path)  # must add the new table and keep the old data
    assert store.get_user(1) is not None
    assert store.refinements(1) == []
