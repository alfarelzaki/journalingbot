import json
from datetime import date, timedelta

import pytest

from journalbot import stats
from journalbot.formatting import HtmlFmt, MdFmt
from journalbot.models import DailySummary
from journalbot.recaps import (
    WEEKLY_FORMATS,
    format_for_week,
    generate_monthly,
    generate_weekly,
    render_monthly,
    render_weekly,
    validate_monthly,
)
from journalbot.summarizer import Summarizer, SummaryError
from tests.fakes import FakeLLM

MON = date(2026, 10, 19)


def day(i, **kw):
    return (MON + timedelta(days=i), DailySummary(language="id", **kw))


def sm(*replies):
    return Summarizer(FakeLLM(*replies), "small", "big", retry_delay=0)


# -- stats ---------------------------------------------------------------

def test_streaks():
    days = {date(2026, 10, d) for d in (1, 2, 3, 5, 6)}
    assert stats.longest_streak(days) == 3
    assert stats.streak_ending(days, date(2026, 10, 6)) == 2
    assert stats.streak_ending(days, date(2026, 10, 4)) == 0
    assert stats.longest_streak(set()) == 0


def test_calendar_and_top_weekday():
    days = {MON, MON + timedelta(days=2)}
    assert stats.week_calendar(days, MON) == "Mon🟩 Tue⬜ Wed🟩 Thu⬜ Fri⬜ Sat⬜ Sun⬜"
    assert stats.top_weekday({MON, MON + timedelta(days=7)}) == "Mon"
    assert stats.top_weekday(set()) is None


def test_format_rotates_deterministically():
    names = {format_for_week(MON + timedelta(weeks=i))[0] for i in range(len(WEEKLY_FORMATS))}
    assert len(names) == len(WEEKLY_FORMATS)


# -- weekly ----------------------------------------------------------------

FULL = {
    "headline": "Telat Bangun Gate",
    "subhead": "Tapi kopi menyelamatkan hari",
    "awards": [
        {"title": "Biggest Win", "text": "Selesai laporan", "date": "2026-10-20"},
        {"title": "Fake", "text": "invented day", "date": "2026-01-01"},
    ],
    "gratitude_highlights": ["kopi", "ibu"],
    "oops": [{"text": "telat bangun", "repeated": True}],
    "lesson": "Alarm jauh dari kasur",
    "side_quest": "Taruh HP di meja",
}


async def test_weekly_full_drops_awards_with_unknown_dates_and_computes_stats():
    days = [day(0, mistakes=["telat"]), day(1), day(2), day(3)]
    c = await generate_weekly(sm(json.dumps(FULL)), MON, days)
    assert [a["title"] for a in c["awards"]] == ["Biggest Win"]
    assert c["mini"] is False
    assert c["stats"]["days"] == 4 and c["stats"]["streak"] == 0  # Sunday not journaled
    text = "\n".join(render_weekly(HtmlFmt(), MON, c))
    assert "Weekly Wrapped" in text and "🔁 telat bangun" in text and "4/7" in text


async def test_weekly_mini_when_few_days():
    mini = {"headline": "Quiet week", "lesson": "Rest", "side_quest": "Write once more"}
    c = await generate_weekly(sm(json.dumps(mini)), MON, [day(0), day(1)])
    assert c["mini"] is True and "awards" not in c
    assert "mini recap" in "\n".join(render_weekly(MdFmt(), MON, c))


async def test_weekly_missing_required_field_fails_after_retry():
    with pytest.raises(SummaryError):
        await generate_weekly(sm('{"headline": "x"}', '{"headline": "x"}'), MON, [day(0)])


async def test_weekly_prompt_uses_narrative_model_and_voice():
    llm = FakeLLM(json.dumps(FULL))
    await generate_weekly(Summarizer(llm, "small", "big"), MON, [day(0)] * 1 + [day(1), day(2)])
    assert llm.calls[0]["model"] == "big"
    assert format_for_week(MON)[1] in llm.calls[0]["system"]


# -- monthly ---------------------------------------------------------------

VALID = {"2026-10-03", "2026-10-09", "2026-10-20"}


def test_repeated_mistakes_need_two_distinct_real_days():
    obj = {
        "opener": "Great month",
        "repeated_mistakes": [
            {"theme": "late", "dates": ["2026-10-03", "2026-10-09", "2026-10-09"]},  # 2 distinct: keep
            {"theme": "once", "dates": ["2026-10-03", "2026-10-03"]},                # 1 distinct: drop
            {"theme": "fake", "dates": ["2026-10-03", "2026-12-25"]},                # 1 real: drop
        ],
        "growth": [{"experiment": "phone-free hour", "linked_to": "late", "check": "in bed by 23:00"}] * 5,
    }
    out = validate_monthly(obj, VALID, sparse=False)
    assert [r["theme"] for r in out["repeated_mistakes"]] == ["late"]
    assert out["repeated_mistakes"][0]["dates"] == ["2026-10-03", "2026-10-09"]
    assert len(out["growth"]) == 3


def test_sparse_month_has_no_progress():
    out = validate_monthly({"opener": "hi", "progress": ["better!"]}, VALID, sparse=True)
    assert out["progress"] == []


async def test_monthly_end_to_end_and_render():
    reply = {
        "opener": "Bulan yang kuat",
        "progress": ["Lebih konsisten"],
        "achievements": ["Selesai proyek"],
        "repeated_mistakes": [{"theme": "telat bangun", "dates": ["2026-10-01", "2026-10-02"], "note": "alarm"}],
        "growth": [{"experiment": "Alarm di luar kamar", "linked_to": "telat bangun", "check": "bangun jam 6"}],
        "gratitude_wall": ["ibu"],
        "note_to_future_me": "Lanjut terus",
    }
    days = [(date(2026, 10, d), DailySummary(grateful=["g"], mistakes=["m"], language="id")) for d in range(1, 9)]
    c = await generate_monthly(sm(json.dumps(reply)), "2026-10", days, [], None)
    assert c["sparse"] is False and c["stats"]["days"] == 8 and c["stats"]["longest_streak"] == 8
    assert c["stats"]["days_in_month"] == 31
    text = "\n".join(render_monthly(HtmlFmt(), "2026-10", c))
    assert "October 2026" in text and "telat bangun (01 Oct, 02 Oct)" in text and "Alarm di luar kamar" in text


async def test_monthly_sparse_says_so_and_no_repeats_message():
    c = await generate_monthly(sm('{"opener": "ok"}'), "2026-10", [day(0), day(1)], [], None)
    text = "\n".join(render_monthly(MdFmt(), "2026-10", c))
    assert c["sparse"] and "Not enough journaled days" in text and "No mistake showed up" in text
