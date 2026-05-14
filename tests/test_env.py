"""Tests for strategies.support.env.load_env_file."""
from __future__ import annotations

import os

import pytest

from strategies.support import env as env_mod


def test_load_env_file_sets_unset_keys(tmp_path, monkeypatch):
    """KEY=VALUE lines from a .env land in os.environ when not already set."""
    monkeypatch.delenv("TEST_KEY_A", raising=False)
    monkeypatch.delenv("TEST_KEY_B", raising=False)
    p = tmp_path / ".env"
    p.write_text("TEST_KEY_A=alpha\nTEST_KEY_B=beta\n", encoding="utf-8")
    n = env_mod.load_env_file(p)
    assert n == 2
    assert os.environ["TEST_KEY_A"] == "alpha"
    assert os.environ["TEST_KEY_B"] == "beta"


def test_load_env_file_preserves_existing_environment(tmp_path, monkeypatch):
    """Already-set env vars must not be overwritten — shell export wins."""
    monkeypatch.setenv("TEST_KEY", "from_shell")
    p = tmp_path / ".env"
    p.write_text("TEST_KEY=from_dotenv\n", encoding="utf-8")
    n = env_mod.load_env_file(p)
    assert os.environ["TEST_KEY"] == "from_shell"
    assert n == 0


def test_load_env_file_strips_quotes_and_whitespace(tmp_path, monkeypatch):
    """Single + double quotes around values get stripped; surrounding spaces too."""
    monkeypatch.delenv("TKA", raising=False)
    monkeypatch.delenv("TKB", raising=False)
    monkeypatch.delenv("TKC", raising=False)
    p = tmp_path / ".env"
    p.write_text("TKA = \"alpha-val\"\nTKB= 'beta-val'\nTKC=  bare-val  \n",
                 encoding="utf-8")
    env_mod.load_env_file(p)
    assert os.environ["TKA"] == "alpha-val"
    assert os.environ["TKB"] == "beta-val"
    assert os.environ["TKC"] == "bare-val"


def test_load_env_file_skips_comments_and_blank_lines(tmp_path, monkeypatch):
    monkeypatch.delenv("REAL_KEY", raising=False)
    p = tmp_path / ".env"
    p.write_text("# This is a comment\n\n\nREAL_KEY=value\n# trailing comment\n",
                 encoding="utf-8")
    n = env_mod.load_env_file(p)
    assert n == 1
    assert os.environ["REAL_KEY"] == "value"


def test_load_env_file_silent_noop_when_file_missing(tmp_path):
    """Defensive: tools that call this on every invocation must not crash
    on a fresh checkout with no .env."""
    n = env_mod.load_env_file(tmp_path / "definitely_does_not_exist.env")
    assert n == 0


def test_load_env_file_handles_lines_without_equals(tmp_path, monkeypatch):
    """Lines that lack '=' get skipped, not crashed on."""
    monkeypatch.delenv("VALID", raising=False)
    p = tmp_path / ".env"
    p.write_text("not a kv pair\nVALID=ok\nanother garbage\n", encoding="utf-8")
    n = env_mod.load_env_file(p)
    assert n == 1
    assert os.environ["VALID"] == "ok"


def test_load_env_file_default_path_is_repo_root(monkeypatch):
    """Calling with no args reads from the repo's top-level .env."""
    # We can't write into the real repo root in a test; just assert the
    # default path resolution lands on the right file.
    from strategies.support.env import DEFAULT_ENV_PATH
    assert DEFAULT_ENV_PATH.name == ".env"
    assert DEFAULT_ENV_PATH.parent.name == "p300"
