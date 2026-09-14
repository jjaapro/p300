"""Process-level stand-down flag for duplicate bot instances.

`botlib.heartbeat` already detects that a second live process is writing the
same name-keyed heartbeat row: it forces ``status='error'``, logs loudly and
returns False. Until 2026-09-12 every runner discarded that return value, so
the detection annotated the incident without stopping it — which is what the
2026-08-15..24 doubled fleet did for nine days.

This module turns that detection into a refusal. The flag lives here rather
than in ``botlib`` because the reader is ``strategies.trades.open_paper_trade``
and ``botlib`` already imports ``strategies.trades``; both may depend on
``strategies.support``, neither may depend on the other.

Semantics, deliberately asymmetric:

  ENTRIES are refused while standing down. A second process opening the same
  signal is the actual harm, and it is the one action that cannot be undone
  by the other process.

  EXITS are NOT refused. Both instances keep sweeping stops, targets and time
  stops. A double close is idempotent: ``persist_close`` updates ``WHERE id = ?
  AND status = 'open'`` and, when that UPDATE matches no row because the other
  instance closed first, returns None without recording a CLOSE event. Until
  2026-09-14 it recorded one anyway, and ``close_carry_trade``, which holds no
  write lock across its read and its UPDATE, could book a second CLOSE row.
  (The losing ``close_carry_trade`` call still logs its receipt line.)
  Suspending exit management on both instances would leave open positions
  unmanaged — strictly worse than the duplication being guarded against.

Both instances typically detect each other and both stand down, which is
intended: with entries refused and exits still running, the fleet degrades to
"manages what is open, opens nothing new" and says so in every heartbeat,
rather than silently double-sizing.
"""
from __future__ import annotations

import logging

log = logging.getLogger("dashboard.instance_guard")

_reason: str | None = None


def stand_down(reason: str) -> None:
    """Refuse new entries from this process until `resume` is called."""
    global _reason
    if _reason != reason:
        log.error("STANDING DOWN — new entries refused: %s", reason)
    _reason = reason


def resume() -> None:
    """Clear the stand-down. Called on any heartbeat write that sees no
    competing instance."""
    global _reason
    if _reason is not None:
        log.warning("stand-down cleared — entries allowed again")
    _reason = None


def is_standing_down() -> bool:
    return _reason is not None


def reason() -> str | None:
    return _reason


class DuplicateInstanceError(RuntimeError):
    """Raised instead of writing a trade while this process is standing down."""


def check_entry_allowed() -> None:
    """Raise if this process must not open new trades."""
    if _reason is not None:
        raise DuplicateInstanceError(
            f"refusing to open a trade while standing down: {_reason}"
        )
