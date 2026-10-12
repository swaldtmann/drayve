"""Tests for the lldap-first ordering in the deploy role.

Problem (measured on a test VM, first deploy): the Authelia LDAP bind user was
created in a post-deploy step AFTER the full `docker compose up`. Authelia's
startup check failed with LDAP code 49 and the container crashed four times
until the user existed. `depends_on: lldap: service_healthy` does not help,
lldap is healthy before the bind user exists.

Fix: when `auth_provider == 'authelia'` and lldap is on, a pre-step starts only
lldap, waits for it, verifies the admin password (readable message) and ensures
the bind user. Only then the full `docker compose up` runs.

Static checks parse the real task files; the `when` expressions are evaluated
by ansible-playbook (Jinja, not string comparison).

Run: ``pytest tests/python/test_deploy_lldap_bind_before_authelia.py``
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLES = REPO_ROOT / "ansible" / "roles"
DEPLOY_TASKS = ROLES / "deploy" / "tasks" / "main.yml"
LLDAP_READY = ROLES / "lldap" / "tasks" / "ready.yml"
LLDAP_POST = ROLES / "lldap" / "tasks" / "post-deploy.yml"
AUTHELIA_BIND = ROLES / "authelia" / "tasks" / "bind-user.yml"

START_LLDAP = "Start lldap first (bind user must exist before Authelia starts)"
PRE_LLDAP = "Pre-deploy lldap (wait healthy, verify admin password)"
PRE_AUTHELIA = "Pre-deploy authelia (LLDAP bind user)"
COMPOSE_UP = "Docker compose up"
PRE_STEPS = [START_LLDAP, PRE_LLDAP, PRE_AUTHELIA]


def _load(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text())


def _names(tasks: list[dict]) -> list[str]:
    return [t.get("name") for t in tasks]


def _task(tasks: list[dict], name: str) -> dict:
    for t in tasks:
        if t.get("name") == name:
            return t
    raise AssertionError(f"task not found: {name}")


def _uses_admin_password(task: dict) -> bool:
    return "lldap_admin_password" in json.dumps(task)


# --- (a) order in the deploy role ------------------------------------------


def test_prestep_order_before_full_compose_up():
    names = _names(_load(DEPLOY_TASKS))
    idx = [names.index(n) for n in PRE_STEPS]
    assert idx == sorted(idx), f"pre-steps out of order: {dict(zip(PRE_STEPS, idx))}"
    assert idx[-1] < names.index(COMPOSE_UP), "bind user must be ensured before the full compose up"
    assert names.index("Docker compose pull") < idx[0]


def test_first_prestep_starts_only_lldap():
    cmd = _task(_load(DEPLOY_TASKS), START_LLDAP)["ansible.builtin.command"]["cmd"]
    assert cmd.strip() == "docker compose up -d lldap"


def test_prestep_includes_point_to_expected_task_files():
    tasks = _load(DEPLOY_TASKS)
    lldap = _task(tasks, PRE_LLDAP)["ansible.builtin.include_role"]
    auth = _task(tasks, PRE_AUTHELIA)["ansible.builtin.include_role"]
    assert (lldap["name"], lldap["tasks_from"]) == ("lldap", "ready")
    assert (auth["name"], auth["tasks_from"]) == ("authelia", "bind-user")


def test_authelia_post_deploy_include_is_gone():
    """The bind user is ensured once, before compose up, not again afterwards."""
    for t in _load(DEPLOY_TASKS):
        inc = t.get("ansible.builtin.include_role") or {}
        assert not (inc.get("name") == "authelia" and inc.get("tasks_from") == "post-deploy"), t["name"]
    assert not (ROLES / "authelia" / "tasks" / "post-deploy.yml").exists()


# --- (b) admin password check before first use ------------------------------


def test_admin_password_verified_before_first_use_in_ready():
    tasks = _load(LLDAP_READY)
    names = _names(tasks)
    verify = names.index("Verify LLDAP admin password matches secrets.yml")
    first_use = next(i for i, t in enumerate(tasks) if _uses_admin_password(t))
    assert verify == first_use, "the verify task must be the first one that touches the password"
    assert names.index("Wait for lldap to be healthy") < verify


def test_ready_runs_before_bind_user_tasks_in_deploy():
    names = _names(_load(DEPLOY_TASKS))
    assert names.index(PRE_LLDAP) < names.index(PRE_AUTHELIA)


def test_bind_user_file_has_no_password_use_before_check_tasks():
    """In bind-user.yml no task other than the check/create pair may use the password."""
    tasks = _load(AUTHELIA_BIND)
    assert _names(tasks) == [
        "Check if authelia bind user exists in lldap",
        "Create authelia bind user in lldap",
    ]


def test_post_deploy_reimports_ready_before_first_password_use():
    """`--tags lldap` runs still verify the password first (static import, same source)."""
    tasks = _load(LLDAP_POST)
    first = tasks[0]
    assert first.get("ansible.builtin.import_tasks") == "ready.yml"
    first_use = next(i for i, t in enumerate(tasks) if _uses_admin_password(t))
    assert first_use > 0


# --- (c) conditions ----------------------------------------------------------


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


def _would_run(tmp_path: Path, when, extra_vars: dict) -> bool:
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "tasks": [
                        {
                            "name": "probe",
                            "ansible.builtin.debug": {"msg": "WOULD_RUN_MARKER"},
                            "when": when,
                        }
                    ],
                }
            ]
        )
    )
    vars_file = tmp_path / "vars.json"
    vars_file.write_text(json.dumps(extra_vars))
    res = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(wrapper), "-e", f"@{vars_file}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    return "WOULD_RUN_MARKER" in res.stdout


@pytest.mark.parametrize("step", PRE_STEPS)
@pytest.mark.parametrize(
    "provider,lldap,expected",
    [
        ("authelia", True, True),
        ("authelia", False, False),  # file backend: no pre-step
        ("authentik", True, False),
        ("authentik", False, False),
    ],
)
def test_prestep_condition_matrix(ansible_available, tmp_path, step, provider, lldap, expected):
    when = _task(_load(DEPLOY_TASKS), step)["when"]
    got = _would_run(tmp_path, when, {"auth_provider": provider, "auth_lldap": lldap})
    assert got is expected


def test_prestep_condition_missing_auth_lldap_does_not_run(ansible_available, tmp_path):
    for step in PRE_STEPS:
        when = _task(_load(DEPLOY_TASKS), step)["when"]
        assert _would_run(tmp_path, when, {"auth_provider": "authelia"}) is False


# --- (d) single source of the bind-user logic ---------------------------------


def test_bind_user_logic_defined_exactly_once():
    needles = ['createUser(user: {id: \\"authelia\\"', "lldap_strict_readonly"]
    for needle in needles:
        hits = [
            str(p.relative_to(REPO_ROOT))
            for p in (REPO_ROOT / "ansible").rglob("*")
            if p.is_file() and p.suffix in {".yml", ".yaml", ".j2", ".sh"} and needle in p.read_text(errors="ignore")
        ]
        assert hits == ["ansible/roles/authelia/tasks/bind-user.yml"], f"{needle!r}: {hits}"


# --- (e) tags ------------------------------------------------------------------


def test_prestep_tags_cover_compose_up_tags():
    tasks = _load(DEPLOY_TASKS)
    up_tags = set(_task(tasks, COMPOSE_UP)["tags"])
    for step in PRE_STEPS:
        assert up_tags <= set(_task(tasks, step)["tags"]), step


def test_inner_tasks_carry_deploy_tag():
    """include_role tags do not propagate to inner tasks: `--tags secrets,deploy`
    only reaches them when they carry `deploy` themselves."""
    for path in (LLDAP_READY, AUTHELIA_BIND):
        for t in _load(path):
            assert "deploy" in t["tags"], f"{path.name}: {t['name']}"
