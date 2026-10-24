import pytest

from journalbot.config import ConfigError, load_config, load_dotenv

BASE = {"TELEGRAM_BOT_TOKEN": "t", "ALLOWED_USER_IDS": "123"}


def test_anthropic_defaults_and_legacy_key():
    cfg = load_config({**BASE, "ANTHROPIC_API_KEY": "k"})
    assert cfg.llm_provider == "anthropic"
    assert cfg.llm_api_key == "k"
    assert cfg.summary_model == "claude-haiku-5-5"
    assert cfg.narrative_model == "claude-sonnet-5-5"
    assert cfg.allowed_user_ids == frozenset({123})
    assert cfg.style_samples == 3


def test_all_problems_reported_together():
    with pytest.raises(ConfigError) as exc:
        load_config({})
    msg = str(exc.value)
    assert "TELEGRAM_BOT_TOKEN" in msg
    assert "ALLOWED_USER_IDS" in msg
    assert "LLM_API_KEY" in msg


def test_usernames_rejected_in_allowlist():
    with pytest.raises(ConfigError, match="numeric"):
        load_config({**BASE, "ALLOWED_USER_IDS": "@farel", "LLM_API_KEY": "k"})


def test_multiple_ids():
    cfg = load_config({**BASE, "ALLOWED_USER_IDS": "1, 2,3", "LLM_API_KEY": "k"})
    assert cfg.allowed_user_ids == frozenset({1, 2, 3})


def test_non_anthropic_requires_model_and_narrative_falls_back():
    env = {**BASE, "LLM_PROVIDER": "groq", "LLM_API_KEY": "k"}
    with pytest.raises(ConfigError, match="SUMMARY_MODEL"):
        load_config(env)
    cfg = load_config({**env, "SUMMARY_MODEL": "some-model"})
    assert cfg.narrative_model == "some-model"


def test_ollama_needs_no_key():
    cfg = load_config({**BASE, "LLM_PROVIDER": "ollama", "SUMMARY_MODEL": "m"})
    assert cfg.llm_api_key is None


def test_unknown_provider_and_bad_style_samples():
    with pytest.raises(ConfigError) as exc:
        load_config({**BASE, "LLM_PROVIDER": "nope", "STYLE_SAMPLES": "9"})
    assert "LLM_PROVIDER" in str(exc.value)
    assert "STYLE_SAMPLES" in str(exc.value)


def test_dotenv_does_not_override_real_env(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# c\nA=from_file\nB='quoted'\nEMPTY=\n", encoding="utf-8")
    env = {"A": "from_env"}
    load_dotenv(f, env)
    assert env == {"A": "from_env", "B": "quoted"}


def test_dotenv_missing_file_is_fine(tmp_path):
    env = {}
    load_dotenv(tmp_path / "nope", env)
    assert env == {}
