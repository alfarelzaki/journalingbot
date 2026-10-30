"""Telegram adapter: turns Telegram updates into JournalService calls.

Everything that is not an allow-listed user in a private chat is ignored
silently, before any other code runs: bots are publicly discoverable.
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Awaitable, Callable

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from journalbot.config import Config
from journalbot.service import Buttons, JournalService
from journalbot.timeutil import utcnow

log = logging.getLogger(__name__)

COMMANDS = [
    ("now", "Start a journal entry now"),
    ("yesterday", "Write yesterday's entry"),
    ("done", "Finish and summarize"),
    ("skip", "Skip today"),
    ("today", "Today's summary"),
    ("history", "Recent summaries"),
    ("week", "Latest weekly recap"),
    ("month", "Latest monthly review"),
    ("export", "Export your journal"),
    ("undo", "Remove your last message"),
    ("fix", "Replace your last message"),
    ("delete", "Delete a day"),
    ("time", "Set nightly prompt time"),
    ("timezone", "Set your timezone"),
    ("pause", "Pause prompts"),
    ("resume", "Resume prompts"),
    ("help", "All commands"),
]


class TelegramMessenger:
    def __init__(self, bot):
        self.bot = bot

    async def send(self, user_id: int, text: str, buttons: Buttons | None = None) -> None:
        markup = None
        if buttons:
            markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in buttons]
            )
        await self.bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.HTML, reply_markup=markup)

    async def send_document(self, user_id: int, data: bytes, filename: str, caption: str = "") -> None:
        await self.bot.send_document(
            chat_id=user_id, document=InputFile(io.BytesIO(data), filename=filename), caption=caption
        )


Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def arg_text(update: Update) -> str:
    """Everything after the command word, preserving the user's spacing."""
    text = (update.effective_message.text or "") if update.effective_message else ""
    return text.partition(" ")[2].strip()


def guarded(allowed: frozenset[int], fn: Handler) -> Handler:
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user, chat = update.effective_user, update.effective_chat
        if user is None or user.id not in allowed or chat is None or chat.type != "private":
            return  # silent: strangers learn nothing, not even that the bot is alive
        await fn(update, context)

    return wrapper


async def tick_loop(service: JournalService) -> None:
    """Run the scheduler once a minute, on the minute, forever."""
    first = True
    while True:
        if not first:
            now = utcnow()
            await asyncio.sleep(60 - now.second - now.microsecond / 1_000_000)
        first = False
        try:
            await service.tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("tick failed: %s", type(exc).__name__)


def build_application(config: Config, service_factory: Callable[[TelegramMessenger], JournalService]) -> Application:
    allowed = config.allowed_user_ids

    async def post_init(app: Application) -> None:
        await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])
        app.bot_data["tick_task"] = asyncio.create_task(tick_loop(app.bot_data["service"]))

    async def post_shutdown(app: Application) -> None:
        task = app.bot_data.get("tick_task")
        if task:
            task.cancel()

    app = ApplicationBuilder().token(config.telegram_bot_token).post_init(post_init).post_shutdown(post_shutdown).build()
    service = service_factory(TelegramMessenger(app.bot))
    app.bot_data["service"] = service

    def uid(update: Update) -> int:
        return update.effective_user.id  # guarded() guarantees it exists

    async def start(update, context):
        await service.on_start(uid(update))

    async def help_(update, context):
        await service.help(uid(update))

    async def now(update, context):
        await service.start_session(uid(update))

    async def yesterday(update, context):
        await service.start_session(uid(update), yesterday=True)

    async def done(update, context):
        await service.finish(uid(update))

    async def skip(update, context):
        await service.skip(uid(update))

    async def undo(update, context):
        await service.undo(uid(update))

    async def fix(update, context):
        await service.fix(uid(update), arg_text(update))

    async def today(update, context):
        await service.today(uid(update))

    async def history(update, context):
        await service.history(uid(update), arg_text(update))

    async def week(update, context):
        await service.week(uid(update), arg_text(update))

    async def month(update, context):
        await service.month(uid(update), arg_text(update))

    async def export(update, context):
        await service.export(uid(update), arg_text(update))

    async def retry(update, context):
        await service.retry(uid(update))

    async def delete(update, context):
        await service.delete_request(uid(update), arg_text(update))

    async def set_time(update, context):
        await service.set_time(uid(update), arg_text(update))

    async def set_timezone(update, context):
        await service.set_timezone(uid(update), arg_text(update))

    async def pause(update, context):
        await service.pause(uid(update))

    async def resume(update, context):
        await service.resume(uid(update))

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await service.add_text(uid(update), update.effective_message.text or ""):
            try:  # a reaction confirms receipt without cluttering the chat
                await update.effective_message.set_reaction("👍")
            except Exception:
                pass

    async def on_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            "Voice notes and media aren't supported yet. Please type your entry for now."
        )

    async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        try:  # remove the buttons so a double tap can't fire twice
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        data, user_id = query.data or "", uid(update)
        if data == "done":
            await service.finish(user_id)
        elif data == "skip":
            await service.skip(user_id)
        elif data == "ok":
            await service.accept_summary(user_id)
        elif data == "now":
            await service.start_session(user_id)
        elif data.startswith("del:"):
            await service.delete_confirmed(user_id, data[4:])
        elif data == "delno":
            await service.messenger.send(user_id, "Cancelled. Nothing was deleted.")

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Type only: exception text could contain journal content.
        log.error("handler error: %s", type(context.error).__name__)

    commands: dict[str, Handler] = {
        "start": start, "help": help_, "now": now, "yesterday": yesterday, "done": done, "skip": skip, "undo": undo,
        "fix": fix, "today": today, "history": history, "week": week, "month": month,
        "export": export, "retry": retry, "delete": delete, "time": set_time,
        "timezone": set_timezone, "pause": pause, "resume": resume,
    }
    for name, fn in commands.items():
        app.add_handler(CommandHandler(name, guarded(allowed, fn)))
    app.add_handler(CallbackQueryHandler(guarded(allowed, on_button)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guarded(allowed, on_text)))
    app.add_handler(
        MessageHandler(
            (filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE | filters.PHOTO | filters.VIDEO | filters.Document.ALL)
            & ~filters.COMMAND,
            guarded(allowed, on_unsupported),
        )
    )
    app.add_error_handler(on_error)
    return app
