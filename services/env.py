"""Stdlib-only `.env` loader for CLI entry points and the long-running bot.

Multiple tools in this repo (fetch_coinalyze.py historically; now also
the AI_QUANT decision CLI and run.py) need to pull values from a
top-level `.env` file at startup. Rather than duplicate the parser
across modules, callers import `services.env.load_env_file()` once at
their main() / startup boundary.

Properties of the loader (kept identical to fetch_coinalyze.py's
historical behaviour so we can refactor that file to use this without
behavioural change):

  • Stdlib-only — no python-dotenv dependency.
  • Handles `KEY=VALUE` lines, comments (lines starting with `#`),
    blank lines, and surrounding `'` or `"` quotes on the value.
  • Does NOT overwrite values already in os.environ. An explicit
    shell `export` always wins over what's in `.env`.
  • Silent no-op when the file doesn't exist — testable / portable.

We don't auto-call this on import. CLI tools call it from main(), and
run.py calls it once at startup. Library code (services that get
imported by tests) must NOT call it on import — that would mutate
os.environ during a pytest run.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env_file(env_path: Path | str | None = None) -> int:
    """Read `env_path` (defaults to repo-root `.env`) and merge KEY=VALUE
    pairs into os.environ. Existing env values are preserved. Returns
    the count of keys actually set on this call (0 if the file is
    missing, all keys were already set, or the file is empty)."""
    p = Path(env_path) if env_path else DEFAULT_ENV_PATH
    if not p.exists():
        return 0
    set_count = 0
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            set_count += 1
    return set_count
