"""Tests for ``scripts/reap-orphan-test-hosts.py``.

Covers the two pure functions (``load_allowlist``, ``select_orphans``). The
hcloud Client I/O in ``main`` is the boundary and is not exercised here.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "reap-orphan-test-hosts.py"


def _load_module():
    """Load the dash-named script as a module."""
    spec = importlib.util.spec_from_file_location("reap_orphan_test_hosts", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reaper = _load_module()


class FakeServer:
    def __init__(self, name, created, sid=1):
        self.name = name
        self.created = created
        self.id = sid
        self.deleted = False

    def delete(self):
        self.deleted = True


# --- load_allowlist ---------------------------------------------------------

def test_load_allowlist_reads_real_molecule_dir():
    names = reaper.load_allowlist(REPO_ROOT / "molecule")
    # All real molecule scenarios are named test-<scenario>.
    assert "test-basic" in names
    assert all(n.startswith("test-") for n in names)
    assert len(names) >= 5


def test_load_allowlist_parses_platform_names(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "molecule.yml").write_text(
        "platforms:\n  - name: test-alpha\n    server_type: cx23\n"
    )
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "molecule.yml").write_text(
        "platforms:\n  - name: test-beta\n"
    )
    assert reaper.load_allowlist(tmp_path) == {"test-alpha", "test-beta"}


def test_load_allowlist_skips_broken_yaml(tmp_path):
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "molecule.yml").write_text("platforms: [unterminated\n")
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "molecule.yml").write_text("platforms:\n  - name: test-good\n")
    assert reaper.load_allowlist(tmp_path) == {"test-good"}


def test_load_allowlist_empty_dir(tmp_path):
    assert reaper.load_allowlist(tmp_path) == set()


# --- select_orphans ---------------------------------------------------------

NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
THRESHOLD = timedelta(hours=4)
ALLOW = {"test-basic", "test-authelia"}


def test_select_orphans_old_and_in_allowlist_is_reaped():
    srv = FakeServer("test-basic", NOW - timedelta(hours=38))
    assert reaper.select_orphans([srv], ALLOW, THRESHOLD, NOW) == [srv]


def test_select_orphans_young_host_is_spared():
    srv = FakeServer("test-basic", NOW - timedelta(hours=1))
    assert reaper.select_orphans([srv], ALLOW, THRESHOLD, NOW) == []


def test_select_orphans_name_not_in_allowlist_is_spared():
    srv = FakeServer("prod-important", NOW - timedelta(days=10))
    assert reaper.select_orphans([srv], ALLOW, THRESHOLD, NOW) == []


def test_select_orphans_exactly_at_threshold_is_reaped():
    srv = FakeServer("test-authelia", NOW - timedelta(hours=4))
    assert reaper.select_orphans([srv], ALLOW, THRESHOLD, NOW) == [srv]


def test_select_orphans_naive_created_treated_as_utc():
    srv = FakeServer("test-basic", datetime(2026, 6, 15, 12, 0, 0))  # naive, 48h ago
    assert reaper.select_orphans([srv], ALLOW, THRESHOLD, NOW) == [srv]


def test_select_orphans_none_created_is_skipped():
    srv = FakeServer("test-basic", None)
    assert reaper.select_orphans([srv], ALLOW, THRESHOLD, NOW) == []


def test_select_orphans_mixed_set():
    keep_young = FakeServer("test-basic", NOW - timedelta(hours=1), sid=1)
    reap_old = FakeServer("test-authelia", NOW - timedelta(hours=10), sid=2)
    foreign = FakeServer("test-unknown", NOW - timedelta(days=5), sid=3)
    result = reaper.select_orphans([keep_young, reap_old, foreign], ALLOW, THRESHOLD, NOW)
    assert result == [reap_old]


# --- main (hcloud Client mocked) -------------------------------------------

def _install_fake_hcloud(monkeypatch, servers):
    """Inject a fake ``hcloud`` module whose Client.servers.get_all returns servers."""
    captured = {}

    class FakeServersClient:
        def get_all(self):
            return servers

    class FakeClient:
        def __init__(self, token):
            captured["token"] = token
            self.servers = FakeServersClient()

    mod = types.ModuleType("hcloud")
    mod.Client = FakeClient
    monkeypatch.setitem(sys.modules, "hcloud", mod)
    return captured


def test_main_missing_token(monkeypatch, capsys):
    monkeypatch.delenv("HCLOUD_TOKEN", raising=False)
    assert reaper.main() == 1
    assert "HCLOUD_TOKEN not set" in capsys.readouterr().err


def test_main_hcloud_lib_missing(monkeypatch, capsys):
    monkeypatch.setenv("HCLOUD_TOKEN", "tok")
    monkeypatch.setitem(sys.modules, "hcloud", None)  # forces ImportError
    assert reaper.main() == 1
    assert "hcloud python lib not available" in capsys.readouterr().err


def test_main_empty_allowlist(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HCLOUD_TOKEN", "tok")
    _install_fake_hcloud(monkeypatch, [])
    monkeypatch.setattr(reaper, "MOLECULE_ROOT", tmp_path)  # no molecule.yml -> empty
    assert reaper.main() == 0
    assert "nothing to reap" in capsys.readouterr().out


def test_main_no_orphans(monkeypatch, capsys):
    monkeypatch.setenv("HCLOUD_TOKEN", "tok")
    young = FakeServer("test-basic", datetime.now(timezone.utc) - timedelta(hours=1))
    _install_fake_hcloud(monkeypatch, [young])
    assert reaper.main() == 0
    assert "clean" in capsys.readouterr().out


def test_main_deletes_orphan(monkeypatch, capsys):
    monkeypatch.setenv("HCLOUD_TOKEN", "tok")
    monkeypatch.delenv("DRY_RUN", raising=False)
    old = FakeServer("test-basic", datetime.now(timezone.utc) - timedelta(hours=38))
    _install_fake_hcloud(monkeypatch, [old])
    assert reaper.main() == 0
    assert old.deleted is True
    assert "deleting orphan: test-basic" in capsys.readouterr().out


def test_main_dry_run_does_not_delete(monkeypatch, capsys):
    monkeypatch.setenv("HCLOUD_TOKEN", "tok")
    monkeypatch.setenv("DRY_RUN", "1")
    old = FakeServer("test-basic", datetime.now(timezone.utc) - timedelta(hours=38))
    _install_fake_hcloud(monkeypatch, [old])
    assert reaper.main() == 0
    assert old.deleted is False
    assert "DRY_RUN would delete: test-basic" in capsys.readouterr().out
