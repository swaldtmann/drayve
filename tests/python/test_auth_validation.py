"""Fail-closed validation of ``auth.access_control``, ``auth.users`` and ``auth.remote``.

Origin: external review of v0.8.12 (findings F1, F2, F3, F5, F6, F7, N1-N4, N6).
Several plausible one-mistake configurations used to deploy something that
protects LESS than the input promises (string instead of list, typo'd group,
portal URL as forward-auth URL, ...). Each rule is enforced in two places that
share one implementation (``ansible/playbooks/filter_plugins/drayve_auth.py``):

  - ``scripts/validate-stack.py`` (``make validate``)
  - ``ansible/tasks/auth_preflight.yml`` (deploy.yml / validate.yml, before any write)

The password-hash format check lives in ``ansible/tasks/authelia_file_users_check.yml``
because hashes are in secrets.yml, not in stack.yaml.

Run: ``pytest tests/python/test_auth_validation.py``
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate-stack.py"
PREFLIGHT_TASKS = REPO_ROOT / "ansible" / "tasks" / "auth_preflight.yml"
CHECK_TASKS = REPO_ROOT / "ansible" / "tasks" / "authelia_file_users_check.yml"
AUTHELIA_TPL_DIR = REPO_ROOT / "config" / "authelia"
EXAMPLES = sorted((REPO_ROOT / "examples").glob("stack-*.yaml"))

DOMAIN = "example.com"
FWD = "https://auth.node01.example.org/api/authz/forward-auth"
PORTAL = "https://auth.node01.example.org"

# Obviously fake argon2id digest (test data, not a credential).
HASH_OK = "$argon2id$v=19$m=65536,t=3,p=4$ZmFrZXNhbHQ$ZmFrZWRpZ2VzdC1hbGljZQ"
PLAINTEXT = "correct-horse-battery-staple"

USERS = [
    {"name": "alice", "email": "alice@example.com", "groups": ["admins"]},
    {"name": "bob", "email": "bob@example.com", "groups": ["viewers"]},
]
RULES = [
    {"domain": ["example.com", "*.example.com"], "policy": "two_factor", "groups": ["admins"]},
    {"domain": "grafana.example.com", "policy": "one_factor", "groups": ["viewers"]},
]


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_stack", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def _auth(rules=None, default_policy="deny", users=None, **extra) -> dict:
    ac: dict = {}
    if default_policy is not None:
        ac["default_policy"] = default_policy
    if rules is not None:
        ac["rules"] = rules
    return {
        "provider": "authelia",
        "lldap": False,
        "access_control": ac,
        "users": USERS if users is None else users,
        **extra,
    }


def _result(auth: dict):
    config = {
        "drayve_version": "0.1.0",
        "stack": {"name": "x", "domain": DOMAIN, "acme_email": "ops@example.org"},
        "auth": auth,
    }
    return validator.validate(config)


def _errors(auth: dict) -> list[str]:
    return _result(auth)[0]


def _warnings(auth: dict) -> list[str]:
    return _result(auth)[1]


def _rule(**over) -> dict:
    return {"domain": "app.example.com", "policy": "two_factor", "groups": ["admins"], **over}


# ---- clean baselines (must hold before and after) ----------------------------------


def test_correct_rule_set_is_clean():
    assert _errors(_auth(RULES)) == []


def test_lldap_mode_does_not_check_group_existence():
    auth = _auth([_rule(groups=["ghosts"])], lldap=True, users=[])
    assert _errors(auth) == []


def test_no_rules_without_default_policy_keeps_old_behaviour():
    assert _errors(_auth(None, default_policy=None)) == []


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_stacks_validate_cleanly(path):
    r = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH), str(path)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr


# ---- 1. types (A1/F1) ------------------------------------------------------------


def test_string_resources_with_bypass_is_rejected():
    auth = _auth([_rule(policy="bypass", groups=[], resources="^/public")])
    errors = _errors(auth)
    assert any("access_control.rules[0].resources" in e for e in errors), errors


def test_string_groups_is_rejected():
    errors = _errors(_auth([_rule(groups="admins")]))
    assert any("access_control.rules[0].groups" in e for e in errors), errors


@pytest.mark.parametrize("key", ["networks", "resources"])
def test_string_networks_and_resources_are_rejected(key):
    errors = _errors(_auth([_rule(**{key: "10.0.0.0/8"})]))
    assert any(f"rules[0].{key}" in e for e in errors), errors


@pytest.mark.parametrize("domain", [5, True, ["ok.example.com", 3], []])
def test_bad_domain_type_is_rejected(domain):
    errors = _errors(_auth([_rule(domain=domain)]))
    assert any("rules[0].domain" in e for e in errors), errors


def test_domain_as_string_or_list_is_accepted():
    assert _errors(_auth([_rule(domain="a.example.com")])) == []
    assert _errors(_auth([_rule(domain=["a.example.com", "b.example.com"])])) == []


def test_user_groups_as_string_is_rejected():
    users = [{"name": "alice", "email": "a@example.com", "groups": "admins"}]
    errors = _errors(_auth([_rule()], users=users))
    assert any("auth.users[0].groups" in e for e in errors), errors


def test_rules_not_a_list_is_rejected():
    errors = _errors(_auth("nope"))
    assert any("access_control.rules" in e for e in errors), errors


# ---- 2. policy enum (A1/F5/N1) -----------------------------------------------------


@pytest.mark.parametrize("policy", ["denny", "Deny", "BYPASS", "allow", "", "two_factor ", 3])
def test_bad_rule_policy_is_rejected(policy):
    errors = _errors(_auth([_rule(policy=policy)]))
    assert any("rules[0].policy" in e for e in errors), errors


@pytest.mark.parametrize("policy", ["allow-all", "ONE_FACTOR", "", 1])
def test_bad_default_policy_is_rejected(policy):
    errors = _errors(_auth([_rule()], default_policy=policy))
    assert any("access_control.default_policy" in e for e in errors), errors


@pytest.mark.parametrize("policy", ["deny", "one_factor", "two_factor", "bypass"])
def test_all_four_policies_accepted_as_default(policy):
    assert _errors(_auth([_rule()], default_policy=policy)) == []


INJECTION = "two_factor\n  - domain: ['*.example.com']\n    policy: bypass"


def test_policy_with_newline_is_rejected():
    errors = _errors(_auth([_rule(policy=INJECTION)]))
    assert any("rules[0].policy" in e for e in errors), errors


def test_template_renders_policy_so_newline_cannot_add_a_rule(ansible_jinja_env):
    """Defence in depth: even if validation were skipped, no second rule appears."""
    env = ansible_jinja_env(AUTHELIA_TPL_DIR)
    env.trim_blocks = True
    tpl = env.get_template("configuration.yml.j2")
    rendered = tpl.render(
        drayve_domain=DOMAIN,
        auth_lldap=True,
        authelia_ldap_password="x",
        authelia_session_secret="s",
        authelia_storage_encryption_key="k",
        authelia_jwt_secret="j",
        authelia_oidc_hmac_secret="h",
        authelia_jwks_pem="stub\n",
        authelia_oidc_clients=[],
        authelia_oidc_clients_default=[],
        authelia_oidc_authorization_policies={},
        authelia_oidc_claims_policies={},
        authelia_oidc_client_secret_hashes={},
        authelia_access_control_default_policy="deny",
        authelia_access_control_rules=[_rule(policy=INJECTION)],
    )
    rules = yaml.safe_load(rendered)["access_control"]["rules"]
    assert len(rules) == 1, rules


# ---- 3. no silent 2FA downgrade (A2/F2) ---------------------------------------------


def test_rules_without_default_policy_abort_and_recommend_deny():
    errors = _errors(_auth([_rule()], default_policy=None))
    hit = [e for e in errors if "access_control.default_policy" in e]
    assert hit, errors
    assert "deny" in hit[0]
    assert "no rule" in hit[0].lower() or "does not match" in hit[0].lower()


# ---- 4. group existence in file mode (A2) -------------------------------------------


def test_rule_group_not_in_any_user_is_rejected():
    errors = _errors(_auth([_rule(groups=["admin"])]))  # users only have "admins"
    hit = [e for e in errors if "admin" in e and "rules[0].groups" in e]
    assert hit, errors


def test_group_case_difference_gets_a_hint():
    errors = _errors(_auth([_rule(groups=["Admins"])]))
    hit = [e for e in errors if "Admins" in e]
    assert hit and "admins" in hit[0], errors
    assert "case" in hit[0].lower()


def test_group_may_come_from_any_single_user():
    users = [
        {"name": "alice", "email": "a@example.com", "groups": []},
        {"name": "bob", "email": "b@example.com", "groups": ["viewers", "ops"]},
    ]
    assert _errors(_auth([_rule(groups=["ops"])], users=users)) == []


# ---- 5. user list (F5/N4/F6) ----------------------------------------------------------


def test_duplicate_user_names_case_insensitive_are_rejected():
    users = USERS + [{"name": "Alice", "email": "x@example.com"}]
    errors = _errors(_auth([_rule()], users=users))
    assert any("Alice" in e and "alice" in e.lower() and "auth.users" in e for e in errors), errors


def test_unknown_user_key_disabled_is_rejected_with_removal_hint():
    users = [{"name": "alice", "email": "a@example.com", "groups": ["admins"], "disabled": True}]
    errors = _errors(_auth([_rule()], users=users))
    hit = [e for e in errors if "disabled" in e]
    assert hit and "remove" in hit[0].lower(), errors


# ---- 6. remote (A3/F3/N5/N6) ----------------------------------------------------------


def _remote(url=FWD, portal=PORTAL) -> dict:
    return {"provider": "authelia_remote", "remote": {"forward_auth_url": url, "portal_url": portal}}


def test_correct_remote_is_clean():
    assert _errors(_remote()) == []
    assert _errors(_remote(url=FWD + "?rd=https%3A%2F%2Fx")) == []


@pytest.mark.parametrize(
    "url",
    [
        "https://auth.node01.example.org",
        "https://auth.node01.example.org/",
        "https://auth.node01.example.org/api/health",
        "https://auth.node01.example.org/api/authz/forward-auth/extra",
        "https://auth.node01.example.org/api/authz/auth-request",
    ],
)
def test_forward_auth_url_must_have_the_forward_auth_path(url):
    errors = _errors(_remote(url=url))
    hit = [e for e in errors if "forward_auth_url" in e]
    assert hit and "/api/authz/forward-auth" in hit[0], errors


def test_remote_portal_url_must_be_https():
    errors = _errors(_remote(portal="http://auth.node01.example.org"))
    assert any("remote.portal_url" in e and "https://" in e for e in errors), errors


def test_forward_host_outside_portal_domain_warns_without_abort():
    auth = _remote(url="https://auth.evil.example.net/api/authz/forward-auth")
    assert _errors(auth) == []
    assert any("forward_auth_url" in w for w in _warnings(auth))


def test_forward_host_equal_to_portal_host_does_not_warn():
    assert not any("forward_auth_url" in w for w in _warnings(_remote()))


# ---- 7. Jinja in inputs (N2) ------------------------------------------------------------


@pytest.mark.parametrize("bad", ["{{ 7*7 }}.example.com", "{% if x %}a{% endif %}"])
def test_template_syntax_in_domain_is_rejected(bad):
    errors = _errors(_auth([_rule(domain=bad)]))
    assert any("rules[0].domain" in e for e in errors), errors


def test_template_syntax_in_group_resource_and_user_is_rejected():
    errors = _errors(_auth([_rule(groups=["{{ x }}"], resources=["{{ y }}"])]))
    assert any("rules[0].groups" in e for e in errors), errors
    assert any("rules[0].resources" in e for e in errors), errors
    users = [{"name": "{% raw %}", "email": "a@example.com"}]
    assert any("auth.users[0].name" in e for e in _errors(_auth([_rule()], users=users)))


def test_template_syntax_error_does_not_echo_the_value():
    errors = _errors(_auth([_rule(domain="{{ lookup('pipe','id') }}.example.com")]))
    assert errors and not any("lookup" in e for e in errors)


# ---- 8. warnings without abort (N3/N9/F7) --------------------------------------------------


def test_bypass_wildcard_without_restriction_warns():
    auth = _auth([_rule(domain=["*.example.com", "example.com"], policy="bypass", groups=[])])
    assert _errors(auth) == []
    assert any("bypass" in w and "wildcard" in w.lower() for w in _warnings(auth))


def test_wildcard_without_apex_warns():
    auth = _auth([_rule(domain="*.sub.example.com")])
    assert _errors(auth) == []
    assert any("sub.example.com" in w for w in _warnings(auth))


def test_wildcard_with_apex_in_same_rule_does_not_warn():
    assert _warnings(_auth(RULES)) == []


def test_unanchored_bypass_regex_warns_anchored_does_not():
    bad = _auth([_rule(policy="bypass", groups=[], resources=["/public"])])
    good = _auth([_rule(policy="bypass", groups=[], resources=["^/public/"])])
    assert any("resources" in w and "^" in w for w in _warnings(bad))
    assert not any("resources" in w for w in _warnings(good))


# ---- Ansible preflight: same rules, before the first write ----------------------------------


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


def _run(tasks_file: Path, tmp_path: Path, extra_vars: dict):
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            ---
            - name: wrapper
              hosts: localhost
              connection: local
              gather_facts: false
              tasks:
                - ansible.builtin.import_tasks: {tasks_file}
            """
        )
    )
    vars_file = tmp_path / "vars.json"
    vars_file.write_text(json.dumps({"drayve_domain": DOMAIN, **extra_vars}))
    return subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(wrapper), "-e", f"@{vars_file}"],
        capture_output=True,
        text=True,
        check=False,
    )


def _preflight(tmp_path, auth):
    return _run(PREFLIGHT_TASKS, tmp_path, {"auth": auth})


def test_preflight_passes_for_correct_rule_set(ansible_available, tmp_path):
    r = _preflight(tmp_path, _auth(RULES))
    assert r.returncode == 0, r.stdout + r.stderr


def test_preflight_rejects_string_resources(ansible_available, tmp_path):
    r = _preflight(tmp_path, _auth([_rule(policy="bypass", groups=[], resources="^/public")]))
    assert r.returncode != 0, r.stdout
    assert "access_control.rules[0].resources" in r.stdout + r.stderr


def test_preflight_rejects_rules_without_default_policy(ansible_available, tmp_path):
    r = _preflight(tmp_path, _auth([_rule()], default_policy=None))
    assert r.returncode != 0, r.stdout
    assert "default_policy" in r.stdout + r.stderr


def test_preflight_rejects_unknown_group(ansible_available, tmp_path):
    r = _preflight(tmp_path, _auth([_rule(groups=["admin"])]))
    assert r.returncode != 0, r.stdout
    assert "admin" in r.stdout + r.stderr


def test_preflight_rejects_portal_root_as_forward_auth_url(ansible_available, tmp_path):
    r = _preflight(tmp_path, _remote(url=PORTAL))
    assert r.returncode != 0, r.stdout
    assert "/api/authz/forward-auth" in r.stdout + r.stderr


def test_preflight_rejects_template_syntax_that_survives_ansible_templating(
    ansible_available, tmp_path
):
    # Ansible evaluates "{{ 7*7 }}" in extra vars before any task sees it (so the
    # raw form is only catchable by scripts/validate-stack.py); a value that
    # evaluates TO template syntax does reach the filter.
    r = _preflight(tmp_path, _auth([_rule(domain="{{ '{{' }}x.example.com")]))
    assert r.returncode != 0, r.stdout
    assert "rules[0].domain" in r.stdout + r.stderr


def test_preflight_warning_does_not_abort(ansible_available, tmp_path):
    r = _preflight(tmp_path, _auth([_rule(domain="*.sub.example.com")]))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARNING" in r.stdout


def test_preflight_ignores_access_control_for_other_providers(ansible_available, tmp_path):
    r = _preflight(tmp_path, {"provider": "basic", "access_control": {"rules": "garbage"}})
    assert r.returncode == 0, r.stdout + r.stderr


# ---- password hash format (users check) --------------------------------------------------------


def _check_vars(hashes: dict) -> dict:
    return {
        "auth_provider": "authelia",
        "auth_lldap": False,
        "auth": {"users": USERS},
        "host_secrets": {"authelia_user_password_hashes": hashes},
    }


def test_check_accepts_argon2_hashes(ansible_available, tmp_path):
    r = _run(CHECK_TASKS, tmp_path, _check_vars({"alice": HASH_OK, "bob": HASH_OK}))
    assert r.returncode == 0, r.stdout + r.stderr


def test_check_rejects_plaintext_password_and_does_not_print_it(ansible_available, tmp_path):
    r = _run(CHECK_TASKS, tmp_path, _check_vars({"alice": HASH_OK, "bob": PLAINTEXT}))
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "bob" in out and "argon2" in out
    assert PLAINTEXT not in out


# ---- unknown keys in access rules and the remote block (fail-open by typo) -----------------


@pytest.mark.parametrize(
    "key",
    ["resource", "group", "Groups", "network", "subject", "methods", "foo"],
)
def test_unknown_rule_key_is_rejected_and_named(key):
    rule = _rule(policy="bypass", **{key: ["^/public"]})
    errors = _errors(_auth([rule]))
    assert any(f"rules[0].{key}" in e and "domain, policy, groups, resources, networks" in e for e in errors), errors


def test_unknown_access_control_key_is_rejected_and_named():
    auth = _auth(RULES)
    auth["access_control"]["default_polcy"] = "bypass"
    errors = _errors(auth)
    assert any("auth.access_control.default_polcy" in e and "default_policy, rules" in e for e in errors), errors


def test_unknown_remote_key_is_rejected_and_named():
    auth = _remote()
    auth["remote"]["forward_url"] = FWD
    errors = _errors(auth)
    assert any("auth.remote.forward_url" in e and "forward_auth_url, portal_url" in e for e in errors), errors


def test_preflight_rejects_unknown_rule_key(ansible_available, tmp_path):
    r = _preflight(tmp_path, _auth([_rule(policy="bypass", resource=["^/public"])]))
    assert r.returncode != 0, r.stdout
    assert "rules[0].resource" in r.stdout + r.stderr


def test_preflight_rejects_unknown_access_control_key(ansible_available, tmp_path):
    auth = _auth(RULES)
    auth["access_control"]["default_polcy"] = "bypass"
    r = _preflight(tmp_path, auth)
    assert r.returncode != 0, r.stdout
    assert "access_control.default_polcy" in r.stdout + r.stderr


# ---- auth / security that are not mappings -------------------------------------------------


@pytest.mark.parametrize("auth", [[], "foo", 5, True], ids=repr)
def test_auth_that_is_not_a_mapping_is_rejected(auth):
    errors = _errors(auth)
    assert any(e.startswith("auth must be a mapping") for e in errors), errors


@pytest.mark.parametrize("security", [[], "foo", 5, True], ids=repr)
def test_security_that_is_not_a_mapping_is_rejected(security):
    config = {
        "drayve_version": "0.1.0",
        "stack": {"name": "x", "domain": DOMAIN, "acme_email": "ops@example.org"},
        "security": security,
    }
    errors = validator.validate(config)[0]
    assert any(e.startswith("security must be a mapping") for e in errors), errors


@pytest.mark.parametrize("auth", [[], "foo"], ids=repr)
def test_preflight_rejects_auth_that_is_not_a_mapping(ansible_available, tmp_path, auth):
    r = _preflight(tmp_path, auth)
    assert r.returncode != 0, r.stdout
    assert "auth must be a mapping" in r.stdout + r.stderr
