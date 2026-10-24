from __future__ import annotations

import os
import sys
from pathlib import Path

from journalbot.config import ConfigError, load_config, load_dotenv


def main() -> int:
    env = dict(os.environ)
    load_dotenv(Path(".env"), env)
    try:
        config = load_config(env)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    # Never print secrets: only non-sensitive facts about the setup.
    print(
        f"Config OK: provider={config.llm_provider}, "
        f"summary_model={config.summary_model}, "
        f"allowed_users={len(config.allowed_user_ids)}."
    )
    print("Bot loop is not implemented yet (milestone M3).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
