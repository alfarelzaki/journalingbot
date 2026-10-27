# Journaling Bot

A private Telegram bot for nightly journaling.

Every night at 20:00 (Asia/Jakarta by default) it asks what you did today. You
answer in as many messages as you like, tap **Done**, and it replies with a
short summary in your own words and tone:

1. 🙏 **Grateful for**
2. ⚠️ **Mistakes**
3. 💡 **What I learned**

Every **Monday 08:00** you get a fun *Weekly Wrapped*; on the **1st** a
*Month in Review* with progress, achievements, repeated mistakes and concrete
ways to grow. Everything is stored in one SQLite file and can be exported as an
organized folder of Markdown, JSON and CSV.

Design details and the reasons behind each rule: [`docs/SPEC.md`](docs/SPEC.md).
Build plan: [`docs/PLAN.md`](docs/PLAN.md).

> **Not in this version:** voice notes (planned for phase 2). Send text for now.

## Privacy: read this first

- Your journal text is sent to the **AI provider you choose** to write
  summaries. Telegram bot chats are also **not end-to-end encrypted**.
- Some free AI tiers may use prompts to improve their products. Check your
  provider's terms before putting real entries through it, and use the made-up
  `samples/` while experimenting.
- Weekly and monthly recaps are built only from the stored daily summaries,
  not from your raw text.
- Only the Telegram IDs in `ALLOWED_USER_IDS` can use the bot; everyone else is
  ignored silently. *Why:* bots are publicly discoverable.
- Logs contain event types and error classes, never your journal text, and the
  HTTP libraries are silenced so the bot token can't appear in a log line.

## Setup

You need three things (never paste them in chat or commit them):

| Setting | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | In Telegram, message **@BotFather**, send `/newbot`, pick a name and a username ending in `bot`. It replies with the token. |
| `ALLOWED_USER_IDS` | Message **@userinfobot**; it replies with your numeric `Id`. *Why a number:* usernames can change; the ID can't be spoofed. |
| `LLM_API_KEY` | From your AI provider (below). |

### Choose an AI provider

| Goal | Settings |
|---|---|
| **Free, for trying it out** (Groq, no card) | `LLM_PROVIDER=groq`, `LLM_API_KEY=<key from console.groq.com>`, `SUMMARY_MODEL=<a current model from their console>` |
| **Cheap and best tone-matching** (Anthropic Haiku, about a cent a month for one entry a day) | `LLM_PROVIDER=anthropic`, `LLM_API_KEY=<key from console.anthropic.com>` (models default to `claude-haiku-5-5` / `claude-sonnet-5-5`; set `NARRATIVE_MODEL=claude-haiku-5-5` to keep recaps cheap) |
| **Very cheap** (DeepSeek Flash) | `LLM_PROVIDER=deepseek`, `LLM_API_KEY=<key from platform.deepseek.com>`, `SUMMARY_MODEL=<Flash model name shown in their live docs>`. Your text goes to DeepSeek; read their privacy policy first. |
| **Free and fully private** | `LLM_PROVIDER=ollama`, `SUMMARY_MODEL=<a model you pulled>`; no key. Small local models are weaker at casual Indonesian and more likely to invent details. |
| Others | `gemini`, `openrouter` (set `SUMMARY_MODEL`). Prices, limits and data policies change; check them. |

Free-tier model names change often, so non-Anthropic providers have no default
model: you must set `SUMMARY_MODEL`.

### Run it on your computer

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env        # then fill in the values; .env is git-ignored
.venv/bin/python -m journalbot
```

Then open your bot in Telegram and send `/start`.

*Keep it running:* the 20:00 prompt only arrives while the program is running
and online. If you miss the window (3 hours) the prompt is skipped for that
night; recaps catch up later.

### Step-by-step server setup

For a full walkthrough with DeepSeek Flash on a Hostinger VPS, including
security, testing and updates, see [`docs/DEPLOY_HOSTINGER.md`](docs/DEPLOY_HOSTINGER.md).

### Run it in Docker

```bash
docker build -t journalbot .
docker run -d --restart unless-stopped --name journalbot \
  --env-file .env -v journalbot-data:/data journalbot
```

The volume holds `journal.db` and `backups/`. *Why a volume:* without it the
journal disappears when the container is replaced.

### Hosting for always-on use

Any small machine that can run Python or Docker works (a home server or
Raspberry Pi, a small VPS, or a platform's secrets/environment-variable page
for the same variables). The bot uses long polling, so it needs no public URL.

## Try the summary quality before using Telegram

```bash
.venv/bin/python scripts/try_summary.py              # runs samples/*.txt
.venv/bin/python scripts/try_summary.py my_notes.txt
```

The mixed sample includes a prompt-injection line to confirm it is treated as
ordinary journal text.

## Commands

| Command | What it does |
|---|---|
| `/now` · `/done` · `/skip` | Start / finish / skip today's entry |
| `/undo` · `/fix <text>` | Remove / replace your last message |
| `/today` · `/history [n]` | Show today's / the last n summaries |
| `/week` · `/month` (+ `redo`) | Show or regenerate the latest weekly / monthly recap |
| `/export [raw] [YYYY-MM]` | Zip of your journal (summaries only unless `raw`) |
| `/delete [YYYY-MM-DD]` | Delete a day (asks to confirm) |
| `/retry` | Retry failed summaries |
| `/time HH:MM` · `/timezone Area/City` | Nightly time / timezone |
| `/pause` · `/resume` | Stop / restart prompts |

How it behaves, and why:
- **Done / auto-finalize:** tap Done, or after 60 minutes of silence it
  summarizes automatically, so falling asleep mid-entry loses nothing.
- **No reply:** one prompt a night, never nagging; unanswered after 4 hours the
  day is marked skipped.
- **Correct the summary:** after each summary the bot asks if anything is off.
  Just tell it, for example what your real mistake was or what you're actually
  grateful for, and it rewrites the summary and shows it again. Repeat until it
  fits, then tap ✅ (or say "ok"). It only changes what you ask, and asks a
  question rather than guessing. The chat closes by itself after an hour.
- **If the AI is down:** your raw text is saved first, the bot tells you, and
  it retries every 30 minutes (or `/retry`).
- **Edits:** deleting or re-summarizing a day marks the covering weekly/monthly
  recap as out of date; `/week redo` or `/month redo` rebuilds it.

## Your data

- Everything lives in `journal.db` (`./data/` locally, `/data` in Docker).
- A daily copy of the database goes to `backups/`. The newest 14 are kept by
  default; set `BACKUPS_KEPT=30` (any number from 1 to 3650) in `.env` to keep
  more. This only limits backup *snapshots*: your summaries themselves are
  never deleted automatically. Copy that folder
  somewhere safe: a single file on a single machine is a single point of failure.
- `/export` builds:
  ```
  journal-export/
  ├── README.md
  ├── daily/2026/10/2026-10-24.md   (YAML front-matter, easy for Obsidian/Notion)
  ├── weekly/2026-W43.md
  ├── monthly/2026-10.md
  └── data/{entries.json, summaries.csv}
  ```

## Manual smoke checklist (needs real keys)

- [ ] `/start` replies; a second Telegram account gets **no** reply.
- [ ] `/now`, send two messages, tap **Done**: summary arrives in order
      Grateful → Mistakes → Learned, in your language, nothing invented.
- [ ] A section you didn't mention shows the "nothing mentioned" line.
- [ ] Stop the program mid-entry, restart, keep writing, **Done**: nothing lost.
- [ ] Break the API key temporarily: Done saves the entry and says so; fix the
      key, `/retry` produces the summary.
- [ ] `/export` and `/export raw` open and look right; default has no raw text.
- [ ] `/delete` asks to confirm and removes the day.
- [ ] Set `/time` to two minutes from now: the prompt arrives once.
- [ ] Check the logs contain no journal text and no bot token.

## Development

```bash
.venv/bin/python -m pytest        # 80+ tests, no network or keys needed
```

Telegram, the AI provider and the clock are all replaced by fakes in tests.

```
src/journalbot/
  config.py      env + .env loading, fail-fast validation
  db.py          SQLite schema, migrations, queries
  timeutil.py    timezone/date logic (explicit local_date, week/month bounds)
  llm.py         Anthropic + OpenAI-compatible clients (httpx)
  summarizer.py  daily summary prompt, JSON validation, retry
  recaps.py      Weekly Wrapped + Month in Review
  stats.py       streaks and counts (computed in code, never by the model)
  formatting.py  Telegram HTML / Markdown rendering, message splitting
  exporter.py    zip export
  service.py     conversation + scheduler logic (Telegram-independent)
  bot.py         Telegram adapter, allowlist guard, minute ticker
  main.py        wiring
```
