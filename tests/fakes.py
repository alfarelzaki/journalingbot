"""Shared test doubles. No network, no real clock."""

from __future__ import annotations

from journalbot.llm import LLMError


class FakeLLM:
    """Returns queued replies in order. A reply that is an Exception is raised."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 1500) -> str:
        self.calls.append({"system": system, "user": user, "model": model})
        if not self.replies:
            raise LLMError("no more fake replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


GOOD_DAILY = (
    '{"grateful": ["Aku bersyukur ibu sehat"], "mistakes": ["Aku telat bangun"],'
    ' "learned": ["Alarm harus jauh dari kasur"], "empty_note": "Nggak ada", "language": "id"}'
)
