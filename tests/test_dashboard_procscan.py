"""dashboard/procscan.py — instance counting with the venv-shim collapse.

Fixture shapes are the REAL process data from 2026-08-24 (the day raw
cmdline counting false-alarmed "fleet doubled" on a healthy fleet): every
unit is a shim parent + real child with the identical command line.
"""
from __future__ import annotations

from dashboard import procscan

VENV = "C:\\Source\\Repos\\p300\\venv\\Scripts\\python.exe"
BASE = "C:\\Python\\Python313\\python.exe"
NOW = 2_000_000.0


def _p(pid, ppid, cmdline, name="python.exe", create_time=1_000_000.0,
       username="PC\\TJ5"):
    return {"pid": pid, "ppid": ppid, "name": name, "cmdline": cmdline,
            "create_time": create_time, "username": username}


def _scan(procs, **kw):
    kw.setdefault("own_pid", 99999)
    kw.setdefault("now", NOW)
    return procscan.scan(procs, **kw)


def test_healthy_shim_pair_is_one_instance():
    # verbatim adx shape from 2026-08-24 (incl. lowercase drive letter)
    procs = [
        _p(30348, 42728, ["C:\\source\\repos\\p300\\venv\\Scripts\\python.exe",
                          "bots/adx/runner.py"]),
        _p(63216, 30348, ["C:\\source\\repos\\p300\\venv\\Scripts\\python.exe",
                          "bots/adx/runner.py"]),
    ]
    res = _scan(procs)
    assert res.count("adx") == 1
    inst = res.instances["adx"][0]
    assert inst.rep_pid == 63216          # the leaf = real interpreter
    assert inst.pids == (30348, 63216)    # shim first, leaf last
    assert res.all_pids("adx") == {30348, 63216}


def test_full_healthy_fleet_all_single():
    procs = []
    pid = 100
    for script in procscan.UNIT_SCRIPTS.values():
        procs.append(_p(pid, 1, [VENV, script]))
        procs.append(_p(pid + 1, pid, [VENV, script]))
        pid += 10
    res = _scan(procs)
    for unit in procscan.UNIT_SCRIPTS:
        assert res.count(unit) == 1, unit


def test_two_chains_is_duplicate():
    procs = [
        _p(10, 1, [VENV, "bots/carry/runner.py"], create_time=1_000_000.0),
        _p(11, 10, [VENV, "bots/carry/runner.py"], create_time=1_000_000.0),
        _p(20, 2, [VENV, "bots/carry/runner.py"], create_time=1_500_000.0),
        _p(21, 20, [VENV, "bots/carry/runner.py"], create_time=1_500_000.0),
    ]
    res = _scan(procs)
    assert res.count("carry") == 2
    # oldest instance first (operator kill guidance)
    assert [i.rep_pid for i in res.instances["carry"]] == [11, 21]


def test_bare_base_python_is_one_instance():
    res = _scan([_p(50, 1, [BASE, "feed.py"])])
    assert res.count("feed") == 1
    assert res.instances["feed"][0].pids == (50,)


def test_shim_with_two_children_counts_two():
    # hypothetical: one launcher spawning two real interpreters — the leaf
    # rule must see through the shared parent.
    procs = [
        _p(10, 1, [VENV, "feed.py"]),
        _p(11, 10, [VENV, "feed.py"]),
        _p(12, 10, [VENV, "feed.py"]),
    ]
    res = _scan(procs)
    assert res.count("feed") == 2


def test_absolute_script_path_and_case_variants_match():
    procs = [
        _p(10, 1, [VENV, "C:\\Source\\Repos\\p300\\feed.py"]),
        _p(20, 1, ["c:\\source\\repos\\p300\\venv\\scripts\\python.exe",
                   "BOTS/CHENTO_V3/RUNNER.PY"]),
    ]
    res = _scan(procs)
    assert res.count("feed") == 1
    assert res.count("chento_v3") == 1


def test_force_start_visible_in_cmdline():
    res = _scan([_p(10, 1, [VENV, "feed.py", "--force-start"])])
    assert "--force-start" in res.instances["feed"][0].cmdline


def test_non_fleet_python_ignored_but_counted():
    procs = [
        _p(10, 1, [VENV, "c:\\Users\\x\\.vscode\\extensions\\ms-python"
                         ".isort-2026.6.0\\bundled\\tool\\lsp_server.py"]),
        _p(11, 1, [VENV, "-"]),
        _p(12, 1, [BASE]),                      # bare REPL
        _p(13, 1, ["C:\\other\\notepad.exe"], name="notepad.exe"),
    ]
    res = _scan(procs)
    assert res.instances == {}
    assert res.scanned_python == 3              # notepad not python


def test_access_denied_counted_not_fatal():
    res = _scan([_p(10, 1, None), _p(11, 1, [])])
    assert res.access_denied == 2
    assert res.instances == {}


def test_legacy_bot_py_flagged_not_counted():
    res = _scan([_p(10, 1, [VENV, "bot.py"]),
                 _p(11, 1, [VENV, "robot.py"])])
    assert res.legacy_bot_py == [10]
    assert res.instances == {}


def test_own_pid_excluded():
    res = _scan([_p(10, 1, [VENV, "feed.py"])], own_pid=10)
    assert res.instances == {}


def test_dash_c_duplicate_simulation_matches():
    # the documented safe live test: a trailing argv token that names the
    # script without executing it must count as an instance.
    res = _scan([_p(10, 1, [BASE, "-c", "import time; time.sleep(120)",
                            "bots/adx/runner.py"])])
    assert res.count("adx") == 1
