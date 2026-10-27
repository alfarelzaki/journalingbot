#!/bin/bash
# One-command setup for macOS (also works on Linux).
#
#   bash scripts/install-mac.sh
#
# What it does, in order:
#   1. finds Python 3.12+ (installs it with Homebrew if you have Homebrew)
#   2. creates a clean virtual environment in .venv and installs the bot
#   3. asks for your settings and writes a private .env file
#   4. tests the AI on the sample journals
#   5. offers to start the bot
#
# Secrets (bot token, API key) are typed with the screen hidden, are never
# printed, and are only written to .env, which only your user can read.

set -euo pipefail
cd "$(dirname "$0")/.."

say()  { printf '\n==> %s\n' "$*"; }
warn() { printf '\n!! %s\n' "$*" >&2; }
clean() { printf '%s' "$1" | tr -d '[:space:]"'"'"; }

# ---- 1. Python 3.12+ ------------------------------------------------------
find_python() {
  local c
  for c in python3.14 python3.13 python3.12 \
           /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 python3; do
    if command -v "$c" >/dev/null 2>&1 \
       && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      command -v "$c"
      return 0
    fi
  done
  return 1
}

say "Looking for Python 3.12 or newer"
if ! PY="$(find_python)"; then
  if command -v brew >/dev/null 2>&1; then
    say "Installing Python 3.12 with Homebrew"
    brew install python@3.12
    PY="$(find_python)" || { warn "Python 3.12 still not found after installing."; exit 1; }
  else
    warn "No Python 3.12+ found and Homebrew is not installed."
    warn "Install Python from https://www.python.org/downloads/ and run this script again."
    exit 1
  fi
fi
echo "Using: $PY ($("$PY" --version))"

# ---- 2. Environment and install ------------------------------------------
# A .venv remembers the Python that created it, so one built with an old
# Python must be replaced, not reused.
if [ -x .venv/bin/python ] \
   && ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
  say "Removing the old .venv (it was built with an older Python)"
  rm -rf .venv
fi
if [ ! -x .venv/bin/python ]; then
  say "Creating the virtual environment"
  "$PY" -m venv .venv
fi
say "Installing the bot (this takes a minute)"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e .
echo "Installed."

# ---- 3. Settings ---------------------------------------------------------
write_env=yes
if [ -f .env ]; then
  printf '\nA .env file already exists. Keep it? [Y/n] '
  read -r keep
  case "$keep" in n|N|no|NO) ;; *) write_env=no ;; esac
fi

if [ "$write_env" = yes ]; then
  say "Your settings (secret values are hidden as you type)"

  printf 'Telegram bot token from @BotFather (hidden): '
  read -r -s token; echo
  token="$(clean "$token")"
  [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] || { warn "That does not look like a bot token (expected 123456:ABC...)."; exit 1; }

  printf 'Your numeric Telegram ID from @userinfobot: '
  read -r uid
  uid="$(clean "$uid")"
  [[ "$uid" =~ ^[0-9]+$ ]] || { warn "The ID must be a number, not a @username."; exit 1; }

  printf 'AI provider [deepseek / anthropic / groq / gemini / openrouter / ollama] (default deepseek): '
  read -r provider
  provider="$(clean "$provider")"; provider="${provider:-deepseek}"
  case "$provider" in
    deepseek|anthropic|groq|gemini|openrouter|ollama) ;;
    *) warn "Unknown provider: $provider"; exit 1 ;;
  esac

  key=""
  if [ "$provider" != ollama ]; then
    printf 'API key for %s (hidden): ' "$provider"
    read -r -s key; echo
    key="$(clean "$key")"
    [ -n "$key" ] || { warn "An API key is required for $provider."; exit 1; }
  fi

  model=""
  if [ "$provider" = anthropic ]; then
    printf 'Model name (press Enter to use the built-in default): '
    read -r model
  else
    default_model=""
    [ "$provider" = deepseek ] && default_model="deepseek-flash"
    printf 'Model name%s: ' "${default_model:+ (default $default_model; check the provider docs for the current name)}"
    read -r model
    model="$(clean "$model")"; model="${model:-$default_model}"
    [ -n "$model" ] || { warn "A model name is required for $provider."; exit 1; }
  fi
  model="$(clean "$model")"

  printf 'How many daily database backups to keep? [14] '
  read -r backups
  backups="$(clean "$backups")"; backups="${backups:-14}"
  [[ "$backups" =~ ^[0-9]+$ ]] || { warn "Backups must be a number."; exit 1; }

  umask 077   # the file is private from the moment it is created
  {
    echo "TELEGRAM_BOT_TOKEN=$token"
    echo "ALLOWED_USER_IDS=$uid"
    echo "LLM_PROVIDER=$provider"
    [ -n "$key" ]   && echo "LLM_API_KEY=$key"
    [ -n "$model" ] && echo "SUMMARY_MODEL=$model" && echo "NARRATIVE_MODEL=$model"
    echo "BACKUPS_KEPT=$backups"
  } > .env
  chmod 600 .env
  say "Saved .env (readable only by you)"
fi

# ---- 4. Test the AI ------------------------------------------------------
say "Testing the AI on the sample journals (made-up text)"
if .venv/bin/python scripts/try_summary.py; then
  echo
  echo "Check the output above:"
  echo "  - sections in the order Grateful, Mistakes, Learned"
  echo "  - first person, in each sample's language"
  echo "  - the 'write a poem about cats' line in the mixed sample must NOT produce a poem"
else
  warn "The AI test failed. Reading the number in 'FAILED' above:"
  warn "  HTTP 401 = wrong API key; HTTP 400/404 = wrong model name; HTTP 402 = no balance."
  warn "Fix .env (or delete it and run this script again), then re-run."
  exit 1
fi

# ---- 5. Start ------------------------------------------------------------
cat <<'EOF'

All set. To start the bot (keep this Terminal window open; Ctrl+C stops it):

    caffeinate -i .venv/bin/python -m journalbot

Start it each day before 20:00, or whenever you want it running.
EOF
printf '\nStart the bot now? [y/N] '
read -r go || go=n
case "$go" in
  y|Y|yes|YES)
    if command -v caffeinate >/dev/null 2>&1; then
      exec caffeinate -i .venv/bin/python -m journalbot
    else
      exec .venv/bin/python -m journalbot
    fi
    ;;
esac
