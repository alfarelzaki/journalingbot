"""Try the daily summarizer on sample journals, without Telegram.

Use this to tune the prompt: it reads LLM_PROVIDER / LLM_API_KEY / models from
the environment or .env, exactly like the bot.

    python scripts/try_summary.py                 # all files in samples/
    python scripts/try_summary.py my_notes.txt    # your own file

Use made-up text with free-tier providers: some may use prompts to improve
their products.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from pathlib import Path

from journalbot.config import ConfigError, load_config, load_dotenv
from journalbot.formatting import MdFmt, render_daily
from journalbot.llm import build_client
from journalbot.summarizer import Summarizer, SummaryError


async def main(paths: list[Path]) -> int:
    env = dict(os.environ)
    load_dotenv(Path(".env"), env)
    # This script never touches Telegram, so placeholders are fine.
    env.setdefault("TELEGRAM_BOT_TOKEN", "unused")
    env.setdefault("ALLOWED_USER_IDS", "1")
    try:
        config = load_config(env)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    summarizer = Summarizer(build_client(config), config.summary_model, config.narrative_model)
    for path in paths:
        print(f"\n=== {path} ===")
        try:
            summary = await summarizer.summarize_day([path.read_text(encoding="utf-8")], [])
        except SummaryError as exc:
            print(f"FAILED: {exc}")
            continue
        print("\n".join(render_daily(MdFmt(), date.today(), summary)))
        print(f"(language: {summary.language}, empty note: {summary.empty_note!r})")
    return 0


if __name__ == "__main__":
    args = [Path(a) for a in sys.argv[1:]] or sorted(Path("samples").glob("*.txt"))
    raise SystemExit(asyncio.run(main(args)))
