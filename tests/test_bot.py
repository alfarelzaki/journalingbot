"""Adapter tests with stand-in Telegram objects (no network)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from journalbot.bot import TelegramMessenger, arg_text, build_application, guarded
from journalbot.config import load_config

CFG = load_config({"TELEGRAM_BOT_TOKEN": "123456:ABCdef", "ALLOWED_USER_IDS": "7", "LLM_API_KEY": "k"})


def update(user_id=7, chat_type="private", text="hi"):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id) if user_id is not None else None,
        effective_chat=SimpleNamespace(type=chat_type),
        effective_message=SimpleNamespace(text=text),
    )


async def test_guard_ignores_strangers_groups_and_anonymous_updates():
    calls = []

    async def inner(u, c):
        calls.append(u)

    g = guarded(frozenset({7}), inner)
    await g(update(user_id=8), None)
    await g(update(user_id=None), None)
    await g(update(chat_type="group"), None)
    assert calls == []
    await g(update(), None)
    assert len(calls) == 1


def test_arg_text():
    assert arg_text(update(text="/fix  the   real text ")) == "the   real text"
    assert arg_text(update(text="/done")) == ""


async def test_messenger_sends_html_with_inline_buttons():
    bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
    m = TelegramMessenger(bot)
    await m.send(7, "<b>x</b>", [[("Done", "done"), ("Skip", "skip")]])
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 7 and kwargs["parse_mode"] == "HTML"
    row = kwargs["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in row] == ["done", "skip"]
    await m.send_document(7, b"zip", "a.zip", "cap")
    assert bot.send_document.await_args.kwargs["document"].filename == "a.zip"


def test_application_builds_with_all_handlers():
    app = build_application(CFG, lambda messenger: SimpleNamespace(messenger=messenger))
    handlers = [h for group in app.handlers.values() for h in group]
    names = {c for h in handlers for c in getattr(h, "commands", [])}
    assert {"start", "now", "done", "export", "week", "month", "delete", "time", "timezone"} <= names
    assert "service" in app.bot_data


async def test_ok_button_calls_accept_summary_and_strangers_are_ignored():
    from telegram.ext import CallbackQueryHandler

    svc = SimpleNamespace(messenger=None, accept_summary=AsyncMock())
    app = build_application(CFG, lambda messenger: svc)
    handler = next(h for g in app.handlers.values() for h in g if isinstance(h, CallbackQueryHandler))

    def press(user_id):
        query = SimpleNamespace(data="ok", answer=AsyncMock(), edit_message_reply_markup=AsyncMock())
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(type="private"),
            callback_query=query,
        )

    await handler.callback(press(8), None)  # not on the allowlist
    svc.accept_summary.assert_not_awaited()
    await handler.callback(press(7), None)
    svc.accept_summary.assert_awaited_once_with(7)
