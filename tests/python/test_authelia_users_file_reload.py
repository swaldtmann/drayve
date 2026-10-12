"""A changed Authelia users file (file backend) must reach the running Authelia.

Why: ``template`` replaces a file by rename (new inode). A single-file bind mount
stays on the old inode, so a removed user / dropped group / swapped hash kept
working until the container was recreated for some other reason. Two measures,
both pinned here:

  1. The users file lives in a directory (``./authelia/users`` ->
     ``/config/users``), so the container sees the renamed file.
  2. A change of the users file triggers the Authelia recreate like a changed
     ``configuration.yml`` does (and the old single-file layout is removed).

Run: ``pytest tests/python/test_authelia_users_file_reload.py``
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTHELIA_TPL_DIR = REPO_ROOT / "config" / "authelia"
COMPOSE_TPL_DIR = REPO_ROOT / "ansible" / "templates"
ROLE_TASKS = REPO_ROOT / "ansible" / "roles" / "authelia" / "tasks" / "main.yml"
DEPLOY_TASKS = REPO_ROOT / "ansible" / "roles" / "deploy" / "tasks" / "main.yml"

OLD_MOUNT = "./authelia/users_database.yml:/config/users_database.yml:ro"
NEW_MOUNT = "./authelia/users:/config/users:ro"


@pytest.fixture
def render_compose(ansible_jinja_env):
    env = ansible_jinja_env(COMPOSE_TPL_DIR)
    env.trim_blocks = True
    tpl = env.get_template("docker-compose.yml.j2")

    def _render(**overrides: object) -> dict:
        ctx = {
            "auth_provider": "authelia",
            "auth_lldap": False,
            "drayve_domain": "example.com",
            "traefik_certresolver": "le",
            "mem_authelia": "256m",
            "mem_lldap": "128m",
            "monitoring_services": {},
        }
        return yaml.safe_load(tpl.render(**{**ctx, **overrides}))

    return _render


def _vols(compose: dict) -> list[str]:
    return compose["services"]["authelia"]["volumes"]


def _role() -> list[dict]:
    return yaml.safe_load(ROLE_TASKS.read_text())


def _deploy() -> list[dict]:
    return yaml.safe_load(DEPLOY_TASKS.read_text())


def _when(task: dict) -> str:
    w = task.get("when", "")
    return " ".join(str(x) for x in w) if isinstance(w, list) else str(w)


def _by_name(tasks: list[dict], name: str) -> dict:
    found = [t for t in tasks if t.get("name") == name]
    assert len(found) == 1, f"expected exactly one task {name!r}, got {len(found)}"
    return found[0]


def _users_template_task(tasks: list[dict]) -> dict:
    found = [t for t in tasks if "ansible.builtin.template" in t
             and str(t["ansible.builtin.template"].get("dest", "")).endswith("/users_database.yml")]
    assert len(found) == 1
    return found[0]


# ---- compose mount ---------------------------------------------------------

def test_compose_does_not_mount_users_file_as_single_file(render_compose):
    vols = _vols(render_compose(auth_lldap=False))
    assert OLD_MOUNT not in vols
    assert not any(v.startswith("./authelia/users_database.yml") for v in vols)


def test_compose_mounts_users_directory_read_only(render_compose):
    assert NEW_MOUNT in _vols(render_compose(auth_lldap=False))


def test_lldap_mode_compose_authelia_volumes_unchanged(render_compose):
    assert _vols(render_compose(auth_lldap=True)) == [
        "./authelia/configuration.yml:/config/configuration.yml:ro",
        "./authelia/data:/data",
        "./authelia/jwks.pem:/config/jwks.pem:ro",
    ]


# ---- role: directory, path, register, legacy cleanup ----------------------

def test_users_file_is_written_into_the_users_directory():
    tpl = _users_template_task(_role())["ansible.builtin.template"]
    assert tpl["dest"] == "{{ authelia_config_dir }}/users/users_database.yml"


def test_users_directory_is_created_0700_before_the_file():
    tasks = _role()
    names = [t.get("name") for t in tasks]
    d = _by_name(tasks, "Create Authelia users directory (file backend)")
    f = d["ansible.builtin.file"]
    assert f["path"] == "{{ authelia_config_dir }}/users"
    assert f["state"] == "directory"
    assert f["mode"] == "0700"
    assert f["owner"] == "{{ drayve_user }}"
    assert "auth_lldap" in _when(d)
    assert names.index(d["name"]) < tasks.index(_users_template_task(tasks))


def test_users_file_task_registers_its_result():
    assert _users_template_task(_role()).get("register") == "authelia_users_file"


def test_legacy_single_file_layout_is_removed():
    tasks = _role()
    t = _by_name(tasks, "Remove legacy Authelia users file (single-file layout)")
    f = t["ansible.builtin.file"]
    assert f["path"] == "{{ authelia_config_dir }}/users_database.yml"
    assert f["state"] == "absent"
    assert "auth_lldap" in _when(t)
    assert "authelia" in t["tags"]
    assert tasks.index(t) > tasks.index(_users_template_task(tasks))


# ---- deploy: recreate coupling ---------------------------------------------

def _recreate() -> dict:
    return _by_name(_deploy(), "Force-recreate Authelia on config or users change")


def test_recreate_fires_on_changed_users_file():
    w = _when(_recreate())
    assert "authelia_users_file.changed" in w
    assert "authelia_config.changed" in w
    assert "auth_provider == 'authelia'" in w


def test_recreate_is_skipped_when_compose_up_already_recreated_authelia():
    w = _when(_recreate())
    assert "compose_up" in w
    pattern = re.search(r"search\('([^']+)'\)", w)
    assert pattern, w
    rx = re.compile(pattern.group(1).replace("\\\\", "\\"))
    assert rx.search(" Container authelia  Recreated")
    assert rx.search(" Container authelia  Created")
    assert not rx.search(" Container traefik  Recreated")
    assert not rx.search(" Container authelia  Running")


# ---- partial deploys must say so -------------------------------------------

def test_partial_run_notice_when_users_file_changed_without_deploy_tag():
    t = _by_name(_role(), "Notice — Authelia files changed, container recreate needs the deploy tag")
    w = _when(t)
    assert "authelia_users_file.changed" in w
    assert "authelia_config.changed" in w
    assert "ansible_run_tags" in w
    assert "'deploy'" in w
    assert "authelia" in t["tags"]


# ---- authelia configuration: path inside the mounted directory -------------

def test_configuration_points_to_file_inside_users_directory(ansible_jinja_env):
    env = ansible_jinja_env(AUTHELIA_TPL_DIR)
    env.trim_blocks = True
    out = env.get_template("configuration.yml.j2").render(
        drayve_domain="example.com", auth_lldap=False,
        authelia_session_secret="s", authelia_storage_encryption_key="k",
        authelia_jwt_secret="j", authelia_oidc_hmac_secret="h",
        authelia_jwks_pem="x", authelia_oidc_clients=[],
        authelia_oidc_clients_default=[], authelia_oidc_authorization_policies={},
        authelia_oidc_claims_policies={}, authelia_oidc_client_secret_hashes={},
    )
    be = yaml.safe_load(out)["authentication_backend"]
    assert be["file"]["path"] == "/config/users/users_database.yml"
    assert be["file"]["watch"] is True
