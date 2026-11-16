"""Role apt tasks survive a busy dpkg lock and a stale package index.

A fresh VM can run apt-daily / unattended-upgrades for minutes (release gate
v0.11.2: ``authelia`` failed twice on ``Unable to acquire the dpkg frontend
lock`` after the module's default ``lock_timeout`` of 60 s; ``auth`` once on a
404 for a ``.deb`` that the mirror had already replaced). Every role task that
installs packages therefore needs a generous ``lock_timeout`` and a retry that
refreshes the index again (``update_cache`` without ``cache_valid_time``,
otherwise the retry would hit the same stale index).

Run: ``pytest tests/python/test_apt_lock_robust.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROLES = Path(__file__).resolve().parents[2] / "ansible" / "roles"
APT = "ansible.builtin.apt"


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _apt_install_tasks():
    found = []
    for f in sorted(ROLES.glob("*/tasks/*.yml")):
        for t in _walk(yaml.safe_load(f.read_text())):
            args = t.get(APT)
            if isinstance(args, dict) and args.get("name"):
                found.append(pytest.param(t, id=f"{f.parent.parent.name}/{f.name}:{t['name']}"))
    return found


APT_TASKS = _apt_install_tasks()


def test_apt_install_tasks_found():
    assert len(APT_TASKS) >= 10


@pytest.mark.parametrize("task", APT_TASKS)
def test_lock_timeout_set_and_generous(task):
    lock_timeout = task[APT].get("lock_timeout")
    assert lock_timeout is not None, "apt default lock_timeout is 60 s"
    assert "drayve_apt_lock_timeout" in str(lock_timeout)
    assert "default(600)" in str(lock_timeout)


@pytest.mark.parametrize("task", APT_TASKS)
def test_retry_refreshes_the_index(task):
    args = task[APT]
    assert args.get("update_cache") is True
    assert "cache_valid_time" not in args, "a valid cache would make the retry reuse the stale index"
    assert task.get("retries", 0) >= 2
    reg = task.get("register")
    assert reg, "until needs a registered result"
    assert reg in str(task.get("until")) and "succeeded" in str(task.get("until"))
