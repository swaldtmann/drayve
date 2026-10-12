"""Tests for Authelia access-control rules and the file user backend.

Covers ``stack.yaml`` -> Authelia:

  - ``auth.access_control.default_policy`` / ``rules`` render into the
    ``access_control`` block of ``config/authelia/configuration.yml.j2``.
  - Without the new keys the rendered file is byte-identical to the previous
    release (golden file, lldap mode — file mode never worked before).
  - ``config/authelia/users_database.yml.j2`` renders ``auth.users`` plus the
    hashes from ``secrets.yml`` (``authelia_user_password_hashes``).
  - File mode switches password change/reset off and enables ``watch``.
  - Compose mounts the users file only when lldap is off.
  - The ``authelia`` role writes the users file (0600, no_log, lldap off only).
  - Fail-early check (``ansible/tasks/authelia_file_users_check.yml``) names the
    user with a missing hash and never prints a hash.
  - The example stack validates against ``config/stack.schema.yaml``.

Run: ``pytest tests/python/test_authelia_access_control_file_backend.py``
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTHELIA_TPL_DIR = REPO_ROOT / "config" / "authelia"
CONFIG_TEMPLATE = "configuration.yml.j2"
USERS_TEMPLATE = "users_database.yml.j2"
COMPOSE_TPL_DIR = REPO_ROOT / "ansible" / "templates"
ROLE_TASKS = REPO_ROOT / "ansible" / "roles" / "authelia" / "tasks" / "main.yml"
CHECK_TASKS = REPO_ROOT / "ansible" / "tasks" / "authelia_file_users_check.yml"
GOLDEN = Path(__file__).parent / "fixtures" / "authelia_configuration_lldap_legacy.yml"
EXAMPLE = REPO_ROOT / "examples" / "stack-authelia-file.yaml"
SCHEMA = REPO_ROOT / "config" / "stack.schema.yaml"

# Obviously fake argon2id digests (test data, not credentials).
HASH_ALICE = "$argon2id$v=19$m=65536,t=3,p=4$ZmFrZXNhbHQ$ZmFrZWRpZ2VzdC1hbGljZQ"
HASH_BOB = "$argon2id$v=19$m=65536,t=3,p=4$ZmFrZXNhbHQ$ZmFrZWRpZ2VzdC1ib2I"

GRAFANA = [
    {
        "id": "grafana",
        "name": "Grafana",
        "authorization_policy": "one_factor",
        "redirect_uris": ["https://grafana.example.com/login/generic_oauth"],
        "scopes": ["openid", "profile", "email", "groups"],
        "consent_mode": "implicit",
    }
]

USERS = [
    {"name": "alice", "displayname": "Alice Admin", "email": "alice@example.com",
     "groups": ["admins"]},
    {"name": "bob", "displayname": "Bob Viewer", "email": "bob@example.com",
     "groups": ["viewers", "dev"]},
]

# Target picture: admins reach every domain with 2FA, viewers reach exactly one
# domain, everything else is denied (default_policy deny).
RULES = [
    {"domain": ["example.com", "*.example.com"], "policy": "two_factor",
     "groups": ["admins"]},
    {"domain": "grafana.example.com", "policy": "one_factor", "groups": ["viewers"]},
]


@pytest.fixture
def jenv(ansible_jinja_env):
    """Ansible-like Jinja env: trim_blocks=True is Ansible's template default."""

    def _make(template_dir: Path):
        env = ansible_jinja_env(template_dir)
        env.trim_blocks = True
        return env

    return _make


@pytest.fixture
def render_config(jenv):
    env = jenv(AUTHELIA_TPL_DIR)
    tpl = env.get_template(CONFIG_TEMPLATE)
    base = {
        "drayve_domain": "example.com",
        "auth_lldap": True,
        "lldap_port": 3890,
        "lldap_base_dn": "DC=drayve,DC=local",
        "authelia_ldap_password": "x",
        "authelia_session_secret": "s",
        "authelia_storage_encryption_key": "k",
        "authelia_jwt_secret": "j",
        "authelia_oidc_hmac_secret": "h",
        "authelia_jwks_pem": "-----BEGIN RSA PRIVATE KEY-----\nstub\n-----END RSA PRIVATE KEY-----\n",
        "authelia_oidc_clients": GRAFANA,
        "authelia_oidc_clients_default": GRAFANA,
        "authelia_oidc_authorization_policies": {},
        "authelia_oidc_claims_policies": {},
        "authelia_oidc_client_secret_hashes": {"grafana": "$pbkdf2-sha512$310000$abc$def"},
    }

    def _render(**overrides: object) -> str:
        return tpl.render(**{**base, **overrides})

    return _render


@pytest.fixture
def render_users(jenv):
    tpl = jenv(AUTHELIA_TPL_DIR).get_template(USERS_TEMPLATE)

    def _render(**overrides: object) -> str:
        ctx = {
            "authelia_users": USERS,
            "authelia_user_password_hashes": {"alice": HASH_ALICE, "bob": HASH_BOB},
        }
        return tpl.render(**{**ctx, **overrides})

    return _render


@pytest.fixture
def render_compose(jenv):
    tpl = jenv(COMPOSE_TPL_DIR).get_template("docker-compose.yml.j2")

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


# ---- (a) access_control renders -------------------------------------------

def test_default_policy_two_factor_renders(render_config):
    cfg = yaml.safe_load(render_config(authelia_access_control_default_policy="two_factor"))
    assert cfg["access_control"]["default_policy"] == "two_factor"
    assert "rules" not in cfg["access_control"]


def test_rules_render_in_order_with_group_subjects(render_config):
    cfg = yaml.safe_load(render_config(
        authelia_access_control_default_policy="deny",
        authelia_access_control_rules=RULES,
    ))
    ac = cfg["access_control"]
    assert ac["default_policy"] == "deny"
    assert ac["rules"] == [
        {"domain": ["example.com", "*.example.com"], "policy": "two_factor",
         "subject": ["group:admins"]},
        {"domain": ["grafana.example.com"], "policy": "one_factor",
         "subject": ["group:viewers"]},
    ]


def test_rule_without_groups_has_no_subject_and_extra_fields_pass(render_config):
    cfg = yaml.safe_load(render_config(authelia_access_control_rules=[
        {"domain": "status.example.com", "policy": "bypass",
         "resources": ["^/health$"], "networks": ["10.0.0.0/8"]},
    ]))
    rule = cfg["access_control"]["rules"][0]
    assert "subject" not in rule
    assert rule["resources"] == ["^/health$"]
    assert rule["networks"] == ["10.0.0.0/8"]


def test_multiple_groups_are_or_alternatives(render_config):
    cfg = yaml.safe_load(render_config(authelia_access_control_rules=[
        {"domain": "x.example.com", "policy": "one_factor", "groups": ["a", "b"]},
    ]))
    assert cfg["access_control"]["rules"][0]["subject"] == ["group:a", "group:b"]


# ---- (b) backward compatibility -------------------------------------------

def test_without_new_keys_lldap_render_is_byte_identical(render_config):
    assert render_config() == GOLDEN.read_text()


# ---- (c) users database ---------------------------------------------------

def test_users_database_renders_authelia_format(render_users):
    doc = yaml.safe_load(render_users())
    assert doc["users"]["alice"] == {
        "disabled": False,
        "displayname": "Alice Admin",
        "password": HASH_ALICE,
        "email": "alice@example.com",
        "groups": ["admins"],
    }
    assert doc["users"]["bob"]["groups"] == ["viewers", "dev"]
    assert doc["users"]["bob"]["password"] == HASH_BOB


def test_users_database_defaults_displayname_and_groups(render_users):
    doc = yaml.safe_load(render_users(
        authelia_users=[{"name": "carol", "email": "carol@example.com"}],
        authelia_user_password_hashes={"carol": HASH_ALICE},
    ))
    assert doc["users"]["carol"]["displayname"] == "carol"
    assert doc["users"]["carol"]["groups"] == []


# ---- (e) file backend options ---------------------------------------------

def test_file_mode_disables_password_change_and_reset_and_watches(render_config):
    cfg = yaml.safe_load(render_config(auth_lldap=False))
    be = cfg["authentication_backend"]
    assert be["file"]["path"] == "/config/users/users_database.yml"
    assert be["file"]["watch"] is True
    assert be["password_change"] == {"disable": True}
    assert be["password_reset"] == {"disable": True}


def test_lldap_mode_has_no_file_backend_options(render_config):
    be = yaml.safe_load(render_config(auth_lldap=True))["authentication_backend"]
    assert "file" not in be
    assert "password_change" not in be
    assert "password_reset" not in be


# ---- (d) compose mount ----------------------------------------------------

def _authelia_volumes(compose: dict) -> list[str]:
    return compose["services"]["authelia"]["volumes"]


def test_compose_mounts_users_file_when_lldap_off(render_compose):
    vols = _authelia_volumes(render_compose(auth_lldap=False))
    assert "./authelia/users:/config/users:ro" in vols


def test_compose_does_not_mount_users_file_when_lldap_on(render_compose):
    vols = _authelia_volumes(render_compose(auth_lldap=True))
    assert not any("/config/users" in v for v in vols)


# ---- role task: users file is written -------------------------------------

def _role_tasks() -> list[dict]:
    return yaml.safe_load(ROLE_TASKS.read_text())


def test_role_writes_users_file_before_config_deploy():
    tasks = _role_tasks()
    names = [t.get("name") for t in tasks]
    users_idx = next(i for i, t in enumerate(tasks)
                     if "users_database.yml" in json.dumps(t))
    assert users_idx < names.index("Deploy Authelia configuration")
    task = tasks[users_idx]
    tpl = task["ansible.builtin.template"]
    assert tpl["dest"].endswith("/users_database.yml")
    assert tpl["mode"] == "0600"
    assert tpl["owner"] == "{{ drayve_user }}"
    assert tpl["group"] == "{{ drayve_group }}"
    assert task["no_log"] is True
    assert "auth_lldap" in task["when"]
    assert "authelia" in task["tags"]


# ---- (f) fail-early check -------------------------------------------------

def _run_check(tmp_path: Path, extra_vars: dict) -> subprocess.CompletedProcess[str]:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(textwrap.dedent(f"""\
        ---
        - name: Test authelia file-users check
          hosts: localhost
          connection: local
          gather_facts: false
          tasks:
            - ansible.builtin.import_tasks: {CHECK_TASKS}
        """))
    vars_file = tmp_path / "vars.json"
    vars_file.write_text(json.dumps(extra_vars))
    return subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(wrapper), "-e", f"@{vars_file}"],
        capture_output=True, text=True, check=False,
    )


def _check_vars(**over) -> dict:
    base = {
        "auth_provider": "authelia",
        "auth_lldap": False,
        "auth": {"users": USERS},
        "host_secrets": {"authelia_user_password_hashes": {
            "alice": HASH_ALICE, "bob": HASH_BOB}},
    }
    base.update(over)
    return base


def test_check_passes_with_all_hashes(tmp_path):
    r = _run_check(tmp_path, _check_vars())
    assert r.returncode == 0, r.stdout + r.stderr


def test_check_fails_naming_user_with_missing_hash_and_leaks_no_hash(tmp_path):
    r = _run_check(tmp_path, _check_vars(host_secrets={
        "authelia_user_password_hashes": {"alice": HASH_ALICE}}))
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "bob" in out
    assert HASH_ALICE not in out
    assert "argon2id" not in out


def test_check_fails_when_hash_is_empty_string(tmp_path):
    r = _run_check(tmp_path, _check_vars(host_secrets={
        "authelia_user_password_hashes": {"alice": HASH_ALICE, "bob": ""}}))
    assert r.returncode != 0
    assert "bob" in r.stdout + r.stderr


def test_check_fails_without_any_user(tmp_path):
    r = _run_check(tmp_path, _check_vars(auth={}))
    assert r.returncode != 0
    assert "auth.users" in r.stdout + r.stderr


def test_check_skipped_when_lldap_on_or_other_provider(tmp_path):
    r1 = _run_check(tmp_path, _check_vars(auth_lldap=True, auth={}, host_secrets={}))
    assert r1.returncode == 0, r1.stdout + r1.stderr
    r2 = _run_check(tmp_path, _check_vars(auth_provider="basic", auth={}, host_secrets={}))
    assert r2.returncode == 0, r2.stdout + r2.stderr


# ---- schema + example -----------------------------------------------------

def test_example_stack_validates_against_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = yaml.safe_load(SCHEMA.read_text())
    jsonschema.validate(yaml.safe_load(EXAMPLE.read_text()), schema)


def test_schema_rejects_bad_policy():
    jsonschema = pytest.importorskip("jsonschema")
    schema = yaml.safe_load(SCHEMA.read_text())
    doc = yaml.safe_load(EXAMPLE.read_text())
    doc["auth"]["access_control"]["default_policy"] = "allow"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, schema)


def test_example_matches_target_picture():
    ac = yaml.safe_load(EXAMPLE.read_text())["auth"]["access_control"]
    assert ac["default_policy"] == "deny"
    assert ac["rules"][0]["policy"] == "two_factor"
    assert ac["rules"][0]["groups"] == ["admins"]
