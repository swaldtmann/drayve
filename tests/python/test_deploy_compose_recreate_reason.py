"""Tests for the deploy role's recreate-reason diagnostics
(roles/deploy/tasks/main.yml).

A CI flake showed Compose recreating `traefik` in the idempotence run. The
stderr names the container but not the reason. Compose recreates when (a) the
service config hash differs from the container label, (b) the container's image
ID differs from the local image ID, or (c) networks do not match. So the role
captures read-only state right before "Docker compose up" and prints it only
when that step reports a change.

Run: ``pytest tests/python/test_deploy_compose_recreate_reason.py``
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_TASKS = REPO_ROOT / "ansible" / "roles" / "deploy" / "tasks" / "main.yml"
PREFIX = "_compose_diag_"


def _tasks() -> list[dict]:
    return yaml.safe_load(DEPLOY_TASKS.read_text())


def _names(tasks):
    return [t.get("name") for t in tasks]


def _capture_tasks():
    return [t for t in _tasks() if str(t.get("register", "")).startswith(PREFIX)]


def _cmd(t: dict) -> str:
    for mod in ("ansible.builtin.command", "ansible.builtin.shell"):
        if mod in t:
            v = t[mod]
            return v if isinstance(v, str) else v.get("cmd", "")
    raise AssertionError(f"no command/shell module in {t.get('name')}")


def test_three_capture_tasks_exist():
    regs = {t["register"] for t in _capture_tasks()}
    assert regs == {PREFIX + "hash", PREFIX + "containers", PREFIX + "images"}


def test_capture_tasks_directly_before_compose_up():
    tasks = _tasks()
    idx = _names(tasks).index("Docker compose up")
    cap = _capture_tasks()
    assert tasks[idx - len(cap) : idx] == cap


def test_capture_tasks_never_change_never_fail_same_context_as_compose_up():
    up = next(t for t in _tasks() if t.get("name") == "Docker compose up")
    for t in _capture_tasks():
        assert t.get("changed_when") is False, t["name"]
        assert t.get("failed_when") is False, t["name"]
        assert t.get("tags") == up["tags"], t["name"]
        assert t.get("become") == up["become"], t["name"]
        assert t.get("become_user") == up["become_user"], t["name"]
        cmd = t.get("ansible.builtin.command") or t.get("ansible.builtin.shell")
        assert cmd["chdir"] == up["ansible.builtin.command"]["chdir"], t["name"]


def test_capture_commands_leak_no_secrets():
    cmds = {t["register"]: _cmd(t) for t in _capture_tasks()}
    assert 'config --hash "*"' in cmds[PREFIX + "hash"]
    for c in cmds.values():
        # `docker compose config` only with --hash/--images, never full dump
        if "compose config" in c:
            assert "--hash" in c or "--images" in c
        # `docker inspect` only with an explicit --format
        if "inspect" in c:
            assert "--format" in c
        assert "Config.Env" not in c and ".Env" not in c


def test_output_task_only_when_compose_up_changed():
    tasks = _tasks()
    idx = _names(tasks).index("Show docker compose up output")
    out = tasks[idx + 1]
    assert "ansible.builtin.debug" in out, out
    assert out.get("when") == "compose_up is changed"
    assert "deploy" in out.get("tags", [])
    body = str(out["ansible.builtin.debug"])
    for reg in (PREFIX + "hash", PREFIX + "containers", PREFIX + "images"):
        assert reg in body
