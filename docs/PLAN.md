# Journaling Bot — Implementation Plan

Companion to [SPEC.md](./SPEC.md). Each milestone ends in something runnable
and testable, so problems surface early instead of at the end.

## Proposed layout

```
journalingbot/
├── pyproject.toml
├── Dockerfile
├── .env.example            # variable names only, never real secrets
├── .gitignore              # .env, *.db, __pycache__
├── README.md               # setup, privacy notes, manual smoke checklist
├── docs/{SPEC,PLAN}.md
├── src/journalbot/
│   ├── config.py           # env loading + validation (fail fast on missing vars)
│   ├── db.py               # schema + migrations + Store
│   ├── models.py           # dataclasses: Entry, Summary, User
│   ├── summarizer.py       # Summarizer interface + Anthropic implementation
│   ├── formatting.py       # summary -> Telegram HTML, escaping, splitting
│   ├── timeutil.py         # local_date, prompt-due logic, DST-safe
│   ├── methods/
│   │   └── nightly.py      # NightlyReflection method (prompt text + schema)
│   ├── handlers.py         # commands, messages, button callbacks
│   ├── scheduler.py        # the per-minute tick
│   └── main.py             # wiring + run_polling
└── tests/
```

## Milestones

### M0 — Scaffold and BotFather setup
- Create `pyproject.toml`, `.gitignore`, `.env.example`, config loader.
- User creates the bot with @BotFather and gets their numeric Telegram ID
  (e.g. from @userinfobot) for `ALLOWED_USER_IDS`.
- **Done when:** `python -m journalbot` starts, and fails with a clear message
  if a required variable is missing.
- *Why first:* a config loader that fails fast prevents confusing errors later.

### M1 — Storage and time logic (no Telegram yet)
- Implement schema, `Store`, and `timeutil` (local date, "is prompt due").
- **Done when:** unit tests pass for one-entry-per-day, cascade delete, and
  midnight/DST boundaries.
- *Why before the bot:* the date logic is the most bug-prone part. Pure
  functions are far easier to test without Telegram in the way.

### M2 — Summarizer and formatter
- Implement `Summarizer` with schema-validated output, retry-once, and the
  failure path; implement `formatting.py`.
- **Done when:** tests pass with a fake client (good / malformed / error),
  and a manual script summarizes a sample journal paragraph with the real API.
- *Why isolated:* summary quality is the product's core value. Iterating on the
  prompt with a script is far faster than doing it through Telegram.

### M3 — Bot conversation loop
- Allowlist guard, `/start`, message collection, Done/Skip buttons, `/done`,
  summary reply, session state persisted in SQLite.
- **Done when:** a manual end-to-end run works (prompt via `/now` → messages →
  Done → summary), and acceptance criteria 2, 3, 7 hold.

### M4 — Scheduling
- Per-minute tick: send nightly prompt, auto-finalize at 60 min idle, expire at
  2 h, retry failed summaries. `/time`, `/timezone`, `/pause`, `/resume`.
- **Done when:** tests with a fake clock cover prompt/finalize/expire and
  restart-mid-session (criterion 4).

### M5 — Review and data-control commands
- `/today`, `/history`, `/retry`, `/delete` (with confirm), `/export`.
- **Done when:** criteria 5 and 6 hold.

### M6 — Hardening and deploy
- Dockerfile, README (privacy notes, hosting, smoke checklist), log audit for
  criterion 8, DB backup note (copy the one `.db` file).
- **Done when:** the bot runs in a container with a mounted volume, survives a
  restart, and all acceptance criteria in SPEC §13 are checked off.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Timezone/date bugs misfile entries | Explicit `local_date`, pure functions, boundary tests (M1) |
| LLM invents content | Strict system prompt + empty-section rule + schema; spot-check with sample journals (M2) |
| LLM outage loses data | Raw messages saved before any API call; `/retry` (M2/M5) |
| Bot token or API key leaks | Env vars only, `.env` git-ignored, `.env.example` has names only |
| Bot restarts mid-entry | All session state in SQLite, not memory (M1/M3) |
| Telegram message limits / bad HTML | Escape all text, split at 4096 chars (M2) |

## What I need from you before M0

1. A bot token from @BotFather (kept as an env var; **don't paste it in chat or commit it**).
2. Your numeric Telegram user ID.
3. An Anthropic API key.
4. Your timezone (e.g. `Asia/Jakarta`) and preferred nightly time.
5. Confirmation, or changes, on the defaults in SPEC §14.

I can build M0–M3 without real credentials (using fakes in tests); the real
keys are only needed for the manual end-to-end checks.
