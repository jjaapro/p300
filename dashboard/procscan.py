"""Ground-truth process scan for the p300 fleet.

psutil enumeration of python processes whose command line names one of the
fleet scripts (feed.py, bots/<name>/runner.py). Heartbeats alone cannot
answer "how many instances are running" — the 2026-08-15→24 incident ran
every bot doubled for 9 days (chento double-sizing each signal) while
monitor.py read all-green, because the two instances overwrite each other's
name-keyed heartbeat row and the DUPLICATE note is visible only between
their alternating ticks.

The one non-obvious rule here is the venv-shim collapse, learned 2026-08-24
when raw cmdline counting produced a false fleet-doubled alarm twice in one
planning session: on Windows, Python 3.13's ``venv\\Scripts\\python.exe`` is
a small redirector that spawns the base interpreter as a CHILD with the
IDENTICAL command line, so a healthy bot is always TWO matched processes
(shim parent + real child; ``bot_heartbeats.pid`` records the child). An
instance is therefore a LEAF of the parent-child graph among a unit's
matches — leaves per unit is the instance count, and only a count > 1 is a
real duplicate. A leaf's representative pid is what heartbeats show.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

# unit name -> script path suffix that identifies it on a command line.
# Matching is against individual argv tokens (normalized to lowercase,
# forward slashes), never the interpreter path — the same venv python also
# runs VS Code language servers.
UNIT_SCRIPTS: dict[str, str] = {
    "feed":          "feed.py",
    "chento_v3":     "bots/chento_v3/runner.py",
    "chento_v3_eth": "bots/chento_v3_eth/runner.py",
    "short_squeeze": "bots/short_squeeze/runner.py",
    "adx":           "bots/adx/runner.py",
    "carry":         "bots/carry/runner.py",
}

# The legacy monolith embeds its own feed thread — a running bot.py means
# double-fetching regardless of instance counts. Flagged, never counted.
_LEGACY_SCRIPT = "bot.py"


@dataclass(frozen=True)
class Instance:
    """One logical running copy of a unit: the real interpreter (leaf) plus
    any launcher ancestry (venv shim) above it."""
    pids: tuple[int, ...]        # ancestry order: shim first, real leaf last
    rep_pid: int                 # the leaf — what bot_heartbeats.pid records
    create_time: float           # epoch seconds of the leaf
    age_s: float
    cmdline: str
    username: str | None


@dataclass
class ScanResult:
    instances: dict[str, list[Instance]] = field(default_factory=dict)
    scanned_python: int = 0      # python-named processes seen (incl. non-fleet)
    access_denied: int = 0       # processes whose cmdline was unreadable
    legacy_bot_py: list[int] = field(default_factory=list)

    def count(self, unit: str) -> int:
        return len(self.instances.get(unit, []))

    def all_pids(self, unit: str) -> set[int]:
        return {pid for inst in self.instances.get(unit, ())
                for pid in inst.pids}


def _norm(token: str) -> str:
    return token.lower().replace("\\", "/")


def _match_unit(cmdline: list[str]) -> str | None:
    for token in cmdline[1:]:            # [0] is the interpreter
        t = _norm(token)
        for unit, script in UNIT_SCRIPTS.items():
            if t == script or t.endswith("/" + script):
                return unit
    return None


def _is_legacy(cmdline: list[str]) -> bool:
    return any(_norm(t) == _LEGACY_SCRIPT or _norm(t).endswith("/" + _LEGACY_SCRIPT)
               for t in cmdline[1:])


def _iter_psutil():
    import psutil
    yield from psutil.process_iter(
        attrs=["pid", "ppid", "name", "cmdline", "create_time", "username"],
        ad_value=None)


def scan(procs=None, own_pid: int | None = None,
         now: float | None = None) -> ScanResult:
    """Scan running processes and group fleet matches into Instances.

    `procs` (tests): iterable of dicts shaped like psutil's info dicts
    ({pid, ppid, name, cmdline, create_time, username}); None values model
    AccessDenied. Default: live psutil enumeration.
    """
    own_pid = os.getpid() if own_pid is None else own_pid
    now = time.time() if now is None else now
    result = ScanResult()

    matched: dict[str, list[dict]] = {}
    for p in (procs if procs is not None else _iter_psutil()):
        info = p if isinstance(p, dict) else p.info
        name = info.get("name") or ""
        if "python" not in name.lower():
            continue
        result.scanned_python += 1
        if info.get("pid") == own_pid:
            continue
        cmdline = info.get("cmdline")
        if not cmdline:                  # AccessDenied / zombie / bare REPL
            result.access_denied += 1
            continue
        if _is_legacy(cmdline):
            result.legacy_bot_py.append(info["pid"])
            continue
        unit = _match_unit(cmdline)
        if unit is not None:
            matched.setdefault(unit, []).append(info)

    for unit, infos in matched.items():
        by_pid = {i["pid"]: i for i in infos}
        parent_pids = {i["ppid"] for i in infos}
        instances = []
        for leaf in infos:
            if leaf["pid"] in parent_pids:
                continue                 # a shim/launcher, not the interpreter
            chain = [leaf]
            cur = leaf
            while cur["ppid"] in by_pid and by_pid[cur["ppid"]] is not cur:
                cur = by_pid[cur["ppid"]]
                if cur in chain:         # cycle guard (pid reuse)
                    break
                chain.append(cur)
            created = leaf.get("create_time") or now
            instances.append(Instance(
                pids=tuple(i["pid"] for i in reversed(chain)),
                rep_pid=leaf["pid"],
                create_time=created,
                age_s=round(max(0.0, now - created), 1),
                cmdline=" ".join(leaf["cmdline"]),
                username=leaf.get("username"),
            ))
        instances.sort(key=lambda i: i.create_time)
        result.instances[unit] = instances

    return result
