"""deploy.yml must warn when the kedge checkout on a host lags the release.

deploy.yml deliberately does not run the backup role, so a host can keep an
old kedge for weeks. The hint reads the version at the end of the deploy,
read-only, never failing, and points to `make backup-deploy`.

Run: ``pytest tests/python/test_kedge_version_hint.py``
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
HINT = ROOT / "ansible/roles/backup/tasks/kedge_version_hint.yml"
DEPLOY = ROOT / "ansible/playbooks/deploy.yml"


def _flat(tasks):
    for t in tasks:
        if "block" in t:
            yield t
            yield from _flat(t["block"])
        else:
            yield t


@pytest.fixture(scope="module")
def tasks():
    assert HINT.exists(), f"missing {HINT}"
    return list(_flat(yaml.safe_load(HINT.read_text())))


def _find(tasks, module):
    found = [t for t in tasks if module in t]
    assert found, f"no task using {module}"
    return found


def _cond(task):
    when = task["when"]
    when = when if isinstance(when, list) else [when]
    return " and ".join(f"({c})" for c in when)


def _eval(expr, **ctx):
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    env.filters["bool"] = lambda v: str(v).lower() in ("1", "true", "yes", "on")
    return env.from_string("{{ " + expr + " }}").render(**ctx) == "True"


def test_read_task_is_read_only_and_cannot_fail(tasks):
    t = _find(tasks, "ansible.builtin.command")[0]
    assert t["changed_when"] is False
    assert t["failed_when"] is False
    assert t["check_mode"] is False
    cmd = t["ansible.builtin.command"]["cmd"]
    assert "git -C {{ backup_kedge_install_dir }}" in cmd
    assert "describe" in cmd


@pytest.mark.parametrize(
    "ctx,expected",
    [
        ({"backup": {"enabled": True, "target": "kedge"}}, True),
        ({"backup": {"enabled": True, "target": "local"}}, False),
        ({"backup": {"enabled": False, "target": "kedge"}}, False),
        ({"backup_enabled": True, "backup_target": "kedge"}, True),
        ({}, False),
    ],
)
def test_active_only_when_backup_enabled_and_target_kedge(tasks, ctx, expected):
    block = [t for t in tasks if "block" in t][0]
    assert _eval(_cond(block), **ctx) is expected


def _warn(tasks):
    return [
        t
        for t in _find(tasks, "ansible.builtin.debug")
        if "when" in t and "backup-deploy" in str(t)
    ][0]


@pytest.mark.parametrize(
    "probe,expected",
    [
        ({"rc": 0, "stdout": "v0.5.1\n"}, False),
        ({"rc": 0, "stdout": "v0.3.4\n"}, True),
        ({"rc": 128, "stdout": ""}, True),
    ],
)
def test_warns_on_mismatch_or_missing_checkout(tasks, probe, expected):
    cond = _cond(_warn(tasks))
    assert _eval(cond, kedge_hint_probe=probe, backup_kedge_version="v0.5.1") is expected


def test_site_override_of_version_is_respected(tasks):
    cond = _cond(_warn(tasks))
    probe = {"rc": 0, "stdout": "v0.4.0\n"}
    assert _eval(cond, kedge_hint_probe=probe, backup_kedge_version="v0.4.0") is False
    assert "backup_kedge_version" in cond


def test_message_names_the_fix(tasks):
    msg = str(_warn(tasks)["ansible.builtin.debug"]["msg"])
    assert "make backup-deploy" in msg
    assert "backup_kedge_version" in msg


def test_deploy_playbook_includes_hint_after_roles_without_backup_role():
    plays = yaml.safe_load(DEPLOY.read_text())
    deploy = [p for p in plays if p.get("name") == "Deploy"][0]
    roles = [r if isinstance(r, str) else r.get("role") for r in deploy["roles"]]
    assert "backup" not in roles
    inc = [t for t in deploy["post_tasks"] if "ansible.builtin.include_role" in t]
    assert inc
    args = inc[0]["ansible.builtin.include_role"]
    assert args["name"] == "backup"
    assert args["tasks_from"] == "kedge_version_hint"
