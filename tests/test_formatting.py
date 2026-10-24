from datetime import date

from journalbot.formatting import HtmlFmt, MdFmt, render_daily, split_blocks
from journalbot.models import DailySummary


def test_daily_order_is_grateful_mistakes_learned():
    s = DailySummary(grateful=["g"], mistakes=["m"], learned=["l"])
    text = "\n".join(render_daily(HtmlFmt(), date(2026, 10, 24), s))
    assert text.index("Grateful") < text.index("Mistakes") < text.index("What I learned")
    assert "Sat 24 Oct 2026" in text


def test_html_escapes_user_text():
    s = DailySummary(grateful=["a <b>bold</b> & more"])
    text = "\n".join(render_daily(HtmlFmt(), date(2026, 10, 24), s))
    assert "<b>bold</b>" not in text and "&lt;b&gt;" in text and "&amp;" in text


def test_empty_section_uses_users_language_note():
    s = DailySummary(empty_note="Nggak ada yang disebut")
    text = "\n".join(render_daily(HtmlFmt(), date(2026, 10, 24), s))
    assert text.count("Nggak ada yang disebut") == 3


def test_markdown_rendering():
    text = "\n".join(render_daily(MdFmt(), date(2026, 10, 24), DailySummary(grateful=["g"])))
    assert text.startswith("# 📓") and "- g" in text


def test_split_keeps_every_message_under_limit_and_loses_nothing():
    blocks = ["x" * 50, ("line\n" * 400).strip(), "y" * 3000, "z" * 9000]
    parts = split_blocks(blocks, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert sum(p.count("x") for p in parts) == 50
    assert sum(p.count("y") for p in parts) == 3000
    assert sum(p.count("z") for p in parts) == 9000
    assert sum(p.count("line") for p in parts) == 400
