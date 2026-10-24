"""Rendering for Telegram (HTML) and for exports (Markdown).

Both targets share the same render functions by swapping a tiny formatter,
so the chat message and the exported file can never drift apart.
"""

from __future__ import annotations

import html
from datetime import date

from journalbot.models import DailySummary

TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 3900  # headroom under Telegram's hard limit


class HtmlFmt:
    """Telegram HTML. Every piece of user/model text is escaped: an unescaped
    '<' or '&' in a journal entry would make Telegram reject the message."""

    def esc(self, text: str) -> str:
        return html.escape(text, quote=False)

    def title(self, text: str) -> str:
        return f"<b>{self.esc(text)}</b>"

    def section(self, text: str) -> str:
        return f"<b>{self.esc(text)}</b>"

    def bullet(self, text: str) -> str:
        return f"• {self.esc(text)}"

    def line(self, text: str) -> str:
        return self.esc(text)

    def italic(self, text: str) -> str:
        return f"<i>{self.esc(text)}</i>"


class MdFmt:
    def title(self, text: str) -> str:
        return f"# {text}"

    def section(self, text: str) -> str:
        return f"## {text}"

    def bullet(self, text: str) -> str:
        return f"- {text}"

    def line(self, text: str) -> str:
        return text

    def italic(self, text: str) -> str:
        return f"*{text}*"


def nice_date(d: date) -> str:
    return d.strftime("%a %d %b %Y")


def render_daily(fmt: HtmlFmt | MdFmt, d: date, s: DailySummary) -> list[str]:
    """Order is Grateful -> Mistakes -> Learned, ending on the lesson."""
    blocks = [fmt.title(f"📓 Journal — {nice_date(d)}")]
    for heading, items in (
        ("🙏 Grateful for", s.grateful),
        ("⚠️ Mistakes", s.mistakes),
        ("💡 What I learned", s.learned),
    ):
        lines = [fmt.section(heading)]
        if items:
            lines += [fmt.bullet(i) for i in items]
        else:
            lines.append(fmt.italic(s.empty_note or "Nothing mentioned today."))
        blocks.append("\n".join(lines))
    return blocks


def split_blocks(blocks: list[str], limit: int = SAFE_LIMIT) -> list[str]:
    """Pack blocks into messages under the limit, splitting only between
    blocks (sections) and, for an oversized single block, between lines."""
    messages: list[str] = []
    current = ""
    for block in blocks:
        for piece in _fit(block, limit):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                messages.append(current)
                current = piece
    if current:
        messages.append(current)
    return messages


def _fit(block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]
    pieces, current = [], ""
    for line in block.split("\n"):
        while len(line) > limit:  # a single enormous line: hard cut
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
        else:
            pieces.append(current)
            current = line
    if current:
        pieces.append(current)
    return pieces
