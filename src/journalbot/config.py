"""Environment-based configuration.

All problems are collected and reported together, so a half-configured bot
fails at startup with one clear message instead of failing later, one missing
variable at a time, in the middle of a nightly run.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PROVIDERS = ("anthropic", "groq", "gemini", "openrouter", "ollama")

ANTHROPIC_SUMMARY_MODEL = "claude-haiku-5-5"
ANTHROPIC_NARRATIVE_MODEL = "claude-sonnet-5-5"


class ConfigError(Exception):
    """Raised with every configuration problem found, one per line."""


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    allowed_user_ids: frozenset[int]
    llm_provider: str
    llm_api_key: str | None
    summary_model: str
    narrative_model: str
    style_samples: int
    db_path: Path


def load_dotenv(path: Path, env: dict[str, str]) -> None:
    """Merge KEY=VALUE lines from `path` into `env` without overriding.

    Real environment variables win over the file so a host's secret settings
    always take precedence over a stray local .env.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and value and key not in env:
            env[key] = value


def load_config(env: Mapping[str, str] | None = None) -> Config:
    source = dict(os.environ if env is None else env)
    problems: list[str] = []

    def get(name: str) -> str:
        return source.get(name, "").strip()

    token = get("TELEGRAM_BOT_TOKEN")
    if not token:
        problems.append("TELEGRAM_BOT_TOKEN is missing (create a bot with @BotFather).")

    allowed: set[int] = set()
    raw_ids = get("ALLOWED_USER_IDS")
    if not raw_ids:
        problems.append(
            "ALLOWED_USER_IDS is missing (your numeric ID from @userinfobot). "
            "Without it anyone who finds the bot could use it."
        )
    else:
        for part in raw_ids.split(","):
            part = part.strip()
            if not part:
                continue
            if part.lstrip("-").isdigit():
                allowed.add(int(part))
            else:
                problems.append(
                    f"ALLOWED_USER_IDS has {part!r}; use numeric IDs, not @usernames."
                )

    provider = get("LLM_PROVIDER").lower() or "anthropic"
    if provider not in PROVIDERS:
        problems.append(f"LLM_PROVIDER={provider!r} is not one of: {', '.join(PROVIDERS)}.")

    api_key = get("LLM_API_KEY") or None
    if api_key is None and provider == "anthropic":
        api_key = get("ANTHROPIC_API_KEY") or None
    if provider in PROVIDERS and provider != "ollama" and api_key is None:
        problems.append(f"LLM_API_KEY is missing (needed for provider {provider!r}).")

    summary_model = get("SUMMARY_MODEL")
    narrative_model = get("NARRATIVE_MODEL")
    if provider == "anthropic":
        summary_model = summary_model or ANTHROPIC_SUMMARY_MODEL
        narrative_model = narrative_model or ANTHROPIC_NARRATIVE_MODEL
    elif provider in PROVIDERS:
        if not summary_model:
            problems.append(
                f"SUMMARY_MODEL is required for provider {provider!r} "
                "(free-tier model names change, so there is no built-in default)."
            )
        narrative_model = narrative_model or summary_model

    raw_samples = get("STYLE_SAMPLES") or "3"
    style_samples = 3
    if raw_samples.isdigit() and 0 <= int(raw_samples) <= 3:
        style_samples = int(raw_samples)
    else:
        problems.append("STYLE_SAMPLES must be a whole number from 0 to 3.")

    if problems:
        raise ConfigError("Configuration problems:\n" + "\n".join(f"- {p}" for p in problems))

    return Config(
        telegram_bot_token=token,
        allowed_user_ids=frozenset(allowed),
        llm_provider=provider,
        llm_api_key=api_key,
        summary_model=summary_model,
        narrative_model=narrative_model,
        style_samples=style_samples,
        db_path=Path(get("DB_PATH") or "./data/journal.db"),
    )
