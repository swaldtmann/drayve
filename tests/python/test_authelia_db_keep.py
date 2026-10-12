"""Static checks on roles/authelia/tasks/main.yml: the Authelia DB (db.sqlite3)
must survive a configuration.yml change. It is only deleted when the
storage_encryption_key demonstrably changed (sha256 marker file next to the DB)
AND the operator opted in via ``authelia_reset_db_on_key_change``.

The checks are static (parsed task list) -- the wrapper-based runtime test for
the cross-tag guard lives in test_authelia_db_secrets_scope_guard.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_FILE = REPO_ROOT / "ansible/roles/authelia/tasks/main.yml"
DEFAULTS_FILE = REPO_ROOT / "ansible/roles/authelia/defaults/main.yml"
DOCS_FILE = REPO_ROOT / "docs/configuration.md"


@pytest.fixture(scope="module")
def tasks() -> list[dict]:
    return yaml.safe_load(TASKS_FILE.read_text())


def _when(task: dict) -> str:
    w = task.get("when", "")
    return " ".join(w) if isinstance(w, list) else str(w)


def _deletes_db(task: dict) -> bool:
    file_mod = task.get("ansible.builtin.file")
    return bool(
        file_mod
        and file_mod.get("state") == "absent"
        and "db.sqlite3" in str(file_mod.get("path", ""))
    )


def _delete_tasks(tasks: list[dict]) -> list[dict]:
    found = [t for t in tasks if _deletes_db(t)]
    assert found, "no task deleting db.sqlite3 found (reset path missing)"
    return found


def test_no_delete_depends_only_on_config_changed(tasks):
    for t in _delete_tasks(tasks):
        w = _when(t)
        assert w.replace(" ", "") != "authelia_config.changed|default(false)", (
            f"task {t['name']!r} deletes db.sqlite3 on config change alone"
        )
        assert "key_changed" in w, f"task {t['name']!r} not tied to key change: {w!r}"


def test_delete_requires_key_change_and_opt_in(tasks):
    for t in _delete_tasks(tasks):
        w = _when(t)
        assert "authelia_reset_db_on_key_change" in w, w
        assert "_authelia_key_changed" in w, w


def test_abort_task_for_key_change_without_switch(tasks):
    fails = [
        t
        for t in tasks
        if ("ansible.builtin.fail" in t or "ansible.builtin.assert" in t)
        and "_authelia_key_changed" in (_when(t) + str(t.get("ansible.builtin.assert", "")))
    ]
    assert fails, "no abort task for key change without authelia_reset_db_on_key_change"
    assert any("authelia_reset_db_on_key_change" in str(t) for t in fails)


def test_checksum_write_skipped_on_empty_key(tasks):
    writers = [
        t
        for t in tasks
        if ".storage_encryption_key.sha256" in str(t.get("ansible.builtin.copy", ""))
    ]
    assert writers, "no task writes the key checksum file"
    for t in writers:
        assert "authelia_storage_encryption_key" in _when(t) and "length" in _when(t), (
            f"task {t['name']!r} does not guard against an empty key"
        )
        assert t["ansible.builtin.copy"].get("mode") == "0600"


def test_key_changed_fact_requires_non_empty_key(tasks):
    facts = [
        t
        for t in tasks
        if "_authelia_key_changed" in str(t.get("ansible.builtin.set_fact", ""))
    ]
    assert facts
    for t in facts:
        assert "authelia_storage_encryption_key" in str(t) and "length" in str(t)


def test_key_touching_tasks_use_no_log(tasks):
    for t in tasks:
        if "authelia_storage_encryption_key" in str(t) or "_authelia_key_sha256" in str(
            t.get("ansible.builtin.set_fact", "")
        ):
            if "ansible.builtin.assert" in t or "ansible.builtin.fail" in t:
                # message must not print the key; hashes only
                assert "authelia_storage_encryption_key }}" not in str(t.get("fail_msg", ""))
                continue
            assert t.get("no_log") is True, f"task {t['name']!r} touches the key without no_log"


def test_default_and_docs():
    defaults = yaml.safe_load(DEFAULTS_FILE.read_text())
    assert defaults.get("authelia_reset_db_on_key_change") is False
    assert "authelia_reset_db_on_key_change" in DOCS_FILE.read_text()
