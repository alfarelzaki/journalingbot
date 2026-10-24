from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from journalbot.bot import TelegramMessenger, build_application
from journalbot.config import ConfigError, load_config, load_dotenv
from journalbot.db import Store
from journalbot.llm import build_client
from journalbot.service import JournalService
from journalbot.summarizer import Summarizer

PROVIDER_NAMES = {
    "anthropic": "Anthropic",
    "groq": "Groq",
    "gemini": "Google Gemini",
    "openrouter": "OpenRouter",
    "deepseek": "DeepSeek",
    "ollama": "your local Ollama server",
}


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs full request URLs at INFO, and Telegram's URL contains the
    # bot token. Keep these loggers quiet so the token never reaches the logs.
    for noisy in ("httpx", "httpcore", "telegram", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> int:
    setup_logging()
    env = dict(os.environ)
    load_dotenv(Path(".env"), env)
    try:
        config = load_config(env)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    store = Store(config.db_path)
    summarizer = Summarizer(build_client(config), config.summary_model, config.narrative_model)
    backup_dir = config.db_path.parent / "backups"

    def make_service(messenger: TelegramMessenger) -> JournalService:
        return JournalService(
            store,
            summarizer,
            messenger,
            style_samples=config.style_samples,
            provider_name=PROVIDER_NAMES.get(config.llm_provider, config.llm_provider),
            backup_dir=backup_dir,
            backups_kept=config.backups_kept,
        )

    app = build_application(config, make_service)
    # Only non-sensitive facts are printed: never tokens or keys.
    print(
        f"Journal bot starting: provider={config.llm_provider}, model={config.summary_model}, "
        f"allowed_users={len(config.allowed_user_ids)}, db={config.db_path}"
    )
    app.run_polling()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
