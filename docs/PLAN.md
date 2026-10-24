# Journaling Bot — Implementation Plan (v2)

Companion to [SPEC.md](./SPEC.md). Each milestone ends in something runnable and
testable, so problems surface early instead of at the end.

## Status

| Milestone | Status |
|---|---|
| M0 Scaffold | done |
| M1 Storage and time logic | done |
| M2 Summarizer and formatter | done (`scripts/try_summary.py` for prompt tuning) |
| M3 Bot loop (text) | done |
| M4 Voice notes | **deferred to phase 2** |
| M5 Scheduling | done |
| M6 Export and backups | done |
| M7 Weekly Wrapped | done |
| M8 Monthly Review | done |
| M9 Deploy | Dockerfile + README done; **real-Telegram smoke test is the user's step** (README checklist), because the build sandbox cannot reach api.telegram.org |

## Proposed layout

```
journalingbot/
├── pyproject.toml
├── Dockerfile
├── .env.example            # variable NAMES only, never real secrets
├── .gitignore              # .env, *.db, data/, backups/, __pycache__
├── README.md               # setup, privacy notes, smoke checklist
├── docs/{SPEC,PLAN}.md
├── src/journalbot/
│   ├── config.py           # env loading + validation (fail fast)
│   ├── db.py               # schema, migrations, Store
│   ├── models.py
│   ├── timeutil.py         # local_date, due-checks, week/month boundaries
│   ├── transcriber.py      # Transcriber interface: OpenAI + local
│   ├── summarizer.py       # daily summary (schema-validated, tone-mirroring)
│   ├── stats.py            # streaks, counts, repeated-mistake detection (pure code)
│   ├── recaps.py           # weekly + monthly generation, rotating formats
│   ├── formatting.py       # Telegram HTML, escaping, splitting
│   ├── exporter.py         # Markdown/JSON/CSV folder + zip
│   ├── methods/nightly.py  # NightlyReflection
│   ├── handlers.py         # commands, text, voice, buttons
│   ├── scheduler.py        # the per-minute tick
│   └── main.py
└── tests/
```

## Milestones

### M0 — Scaffold
`pyproject.toml`, `.gitignore`, `.env.example`, config loader.
**Done when:** the app starts, and exits with a clear message naming any
missing variable.
*Why first:* fail-fast config prevents confusing errors in every later step.

### M1 — Storage and time logic (no Telegram)
Schema + migrations + `Store`; `timeutil` (local date, due checks, week and
month boundaries in Asia/Jakarta).
**Done when:** tests pass for one-entry-per-day, cascade delete, stale-marking,
and midnight boundaries.
*Why before the bot:* date logic is the most bug-prone part, and pure functions
are easy to test without Telegram.

### M2 — Daily summarizer + formatter (tone-mirroring)
Schema-validated output, order Grateful → Mistakes → Learned, style samples,
retry-once/failure path.
**Done when:** fake-client tests pass, and a manual script summarizes sample
Indonesian, English, and mixed journals with no invented content.
*Why isolated:* summary quality is the core of the product; iterating on the
prompt via a script is far faster than via Telegram.

### M3 — Bot loop with text
Allowlist guard, `/start`, collection, Done/Skip, `/done`, `/undo`, `/fix`,
state persisted in SQLite.
**Done when:** `/now` → messages → Done → summary works (criteria 2, 4, 10).

### M4 — Voice notes (DEFERRED to phase 2; skip for now)
`Transcriber` interface + OpenAI implementation (+ `local` option), download →
transcribe → delete audio, show transcript, duration limit, failure messages.
**Done when:** a real voice note becomes a stored transcript and joins the
summary (criteria 2, 3, 5).

### M5 — Scheduling
Per-minute tick: 20:00 prompt, auto-finalize, expiry, retries, `/time`,
`/timezone`, `/pause`, `/resume`.
**Done when:** fake-clock tests cover prompt/finalize/expire and restart
mid-session (criteria 1, 4).

### M6 — Export and backups
`exporter.py` (folder layout, front-matter, JSON, CSV, zip), `/export`, nightly
DB backup with 14-copy rotation, `/history`, `/today`, `/delete`.
**Done when:** golden-file export tests pass; criteria 6, 9.

### M7 — Weekly Wrapped
`stats.py` (computed numbers), weekly generation with rotating formats, mini
recap for sparse weeks, Monday 08:00 delivery, `/week`.
**Done when:** stats unit-tested (streaks, counts); a sample week renders in
each format; criterion 7.
*Why stats before prose:* numbers computed in code can't be hallucinated.

### M8 — Monthly Review
Repeated-mistake detection (≥ 2 distinct days), progress vs. last month,
growth experiments, 1st-of-month delivery, `/month`.
**Done when:** tests cover the ≥ 2 rule and sparse-month handling; criterion 8.

### M9 — Hardening and deploy
Dockerfile, README (privacy, hosting, smoke checklist), log audit, final pass
over every acceptance criterion in SPEC §16.
**Done when:** runs in a container with a mounted volume, survives restart, and
all criteria are checked off.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Wrong transcript becomes wrong journal | Show transcript; `/fix` and `/undo` |
| Model invents content or wrong tone | Strict prompts, empty-section rule, sample-journal checks (M2) |
| Model miscounts in recaps | Numbers computed in code (M7) |
| Recap goes stale after edits/deletes | `status = stale` + `/week redo`, `/month redo` |
| Transcription/LLM outage | Raw saved first; `/retry`; auto-retry on tick |
| Leaked keys | Env vars only; `.env` git-ignored; revoke instructions below |
| Timezone/date bugs | Explicit `local_date`, boundary tests (M1) |
| Cost surprises | Haiku for daily, 10-min voice cap, one weekly + one monthly call |
| One-file DB corruption | Nightly rotated backups (M6) |

## Getting your keys (what you asked)

You need **four** things. **Never paste any of them into this chat**, and never
commit them to git.

### 1. Telegram bot token (from BotFather)
1. In Telegram, search for **@BotFather** (the one with the blue verified tick).
2. Send `/newbot`.
3. Give it a display name (e.g. "My Journal"), then a username that ends in
   `bot` (e.g. `farel_journal_bot`).
4. BotFather replies with a token like `123456789:AA...`. That's
   `TELEGRAM_BOT_TOKEN`.
*If it ever leaks:* send BotFather `/revoke` to invalidate it.

### 2. Your numeric Telegram user ID
Message **@userinfobot** (or @RawDataBot); it replies with your numeric `Id`.
That's `ALLOWED_USER_IDS`.
*Why the number, not your @username:* usernames can be changed or reassigned;
the numeric ID is permanent and can't be spoofed.

### 3. Anthropic API key
1. Go to **console.anthropic.com** and sign in.
2. Add credit under Billing (API usage is billed separately from any Claude.ai
   subscription; a few dollars lasts a long time at one entry a day).
3. **API Keys → Create Key**; copy it immediately (it is shown once).
That's `ANTHROPIC_API_KEY`.

### 4. OpenAI API key — phase 2 only (voice notes), skip for now
Go to **platform.openai.com → API keys → Create new secret key** and add a few
dollars of credit. That's `OPENAI_API_KEY`.
*Why a second provider:* Anthropic's API doesn't transcribe audio. If you'd
rather avoid it, say so and I'll default to the free local transcriber instead
(needs a host with ~1–2 GB RAM).

### Where to put them

| Where the bot runs | Where the keys go |
|---|---|
| **This cloud session (building and testing)** | Cloud environment menu in the session title bar → **Edit** → add them under *Network secrets* / API credentials if shown, otherwise as **environment variables**, using exactly these names: `TELEGRAM_BOT_TOKEN`, `ALLOWED_USER_IDS`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`. A **new session** picks them up. |
| **Your own laptop** | Copy `.env.example` to `.env` in the project folder and fill in the values. `.env` is git-ignored. |
| **Production host** (VPS, Fly.io, Railway…) | The host's *Secrets / Environment variables* page, same names. |

*Why environment variables:* the code reads them at startup, so keys never
appear in source files, git history, or screenshots of the repo.

## Developing at zero cost

| Cost item | Free way | Trade-off |
|---|---|---|
| Telegram bot | Always free | None |
| Building and tests | All tests use fake summarizers and a fake clock | None; this covers ~90% of the work |
| AI summaries (development) | A free-tier provider (`LLM_PROVIDER=gemini`/`groq`/`openrouter`) or a local model via `ollama` | Rate limits; quality in casual Indonesian is lower; free tiers can change or end, so check current terms |
| Privacy on free tiers | Some free tiers may use your prompts to improve their products | Use fake/sample journals while developing; only put real entries through a provider whose terms you accept |
| Hosting (development) | Run on your own laptop/PC | Bot only works while that machine is on |
| Hosting (always on) | An old PC/Raspberry Pi at home, or a provider's free-forever VM tier if one is available to you | Free tiers change and often need a card for sign-up; verify before relying on one |
| AI summaries (production) | Anthropic Haiku | Not free, but one short entry a day plus a weekly and monthly call should be well under a dollar a month (estimate; check current pricing) |

### Model recommendation

| Stage | Provider | Why |
|---|---|---|
| Tuning prompts on **sample** journals | **Groq free tier** (`LLM_PROVIDER=groq`) | No card needed; reported ~30 requests/min and ~1,000/day per model, far more than one user needs. Pick a current Llama- or gpt-oss-class model from their console (names change, so `SUMMARY_MODEL` has no default). |
| Real journal, cheapest and best | **Claude Haiku 5.5** (the default) | Best fit for mirroring casual Indonesian/English tone. Reported price $0.10 / $0.50 per million input/output tokens (prompts under 100k tokens). One entry is roughly 2k tokens in and 300 out, so about **1 cent per month** for daily summaries. |
| Weekly and monthly | Same Haiku via `NARRATIVE_MODEL=claude-haiku-5-5` | They run rarely, so even a pricier model would be cheap, but start with Haiku and only upgrade if the writing feels flat. |
| Fully private and free | `ollama` on your own computer | Nothing leaves your machine, but small local models are noticeably worse at Indonesian slang and at not inventing details. |

Avoid for real entries: **Gemini's free tier**. Sources report that free-tier
content may be used to improve Google's products, which is the wrong trade for
a private journal. Paid usage reportedly is not.

Numbers above come from third-party price trackers, not the official pages.
Confirm on each provider's pricing/limits page before relying on them.

Recommended path: build with fakes, tune prompts with a free provider on
sample journals, run on your laptop to try it for real, and only then decide
whether to pay for a better model or an always-on host.

## What happens next
I can build M0–M3 and most tests with **no keys at all** (fakes in tests). Keys
are only needed for the manual end-to-end checks (M2 prompt tuning, M4 voice).
