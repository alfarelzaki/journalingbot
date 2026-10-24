"""Minimal LLM clients over plain HTTPS.

Two wire formats cover every supported provider: Anthropic's Messages API,
and the OpenAI-compatible chat API that Groq, Gemini, OpenRouter and Ollama
all expose. Using httpx directly keeps the dependency list short and lets the
provider be switched with one setting.

Error messages never include request or response bodies: journal text must
not end up in logs.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from journalbot.config import Config

BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}


class LLMError(Exception):
    """The provider call failed (network, HTTP status, or unusable reply)."""


class LLMClient(Protocol):
    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 1500) -> str: ...


class AnthropicClient:
    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com", timeout: float = 60.0):
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 1500) -> str:
        try:
            resp = await self._http.post(
                "/v1/messages",
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
            resp.raise_for_status()
            blocks = resp.json()["content"]
            return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"Anthropic returned HTTP {exc.response.status_code}") from None
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            raise LLMError(f"Anthropic call failed ({type(exc).__name__})") from None


class OpenAICompatClient:
    def __init__(self, name: str, base_url: str, api_key: str | None, timeout: float = 60.0):
        self._name = name
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, headers=headers)

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 1500) -> str:
        try:
            resp = await self._http.post(
                "/chat/completions",
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "temperature": 0.4,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"] or ""
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"{self._name} returned HTTP {exc.response.status_code}") from None
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
            raise LLMError(f"{self._name} call failed ({type(exc).__name__})") from None


def build_client(config: Config) -> LLMClient:
    if config.llm_provider == "anthropic":
        return AnthropicClient(config.llm_api_key or "", config.llm_base_url or "https://api.anthropic.com")
    base = config.llm_base_url or BASE_URLS[config.llm_provider]
    return OpenAICompatClient(config.llm_provider, base, config.llm_api_key)
