"""Default Grafana OIDC client carries a ``claims_policy`` (Authelia escape hatch).

Authelia 4.39 puts only ``amr, azp, sub`` into the ID token. Grafana evaluates
``ROLE_ATTRIBUTE_PATH`` (``contains(groups, 'lldap_admin') ...``) against the ID
token, so without a claims policy every admin becomes Viewer. The official
Grafana guide prescribes a ``claims_policies`` entry and ``claims_policy`` on
the client:
https://www.authelia.com/integration/openid-connect/clients/grafana/

These tests run the real shared resolution tasks
(``ansible/tasks/authelia_oidc_resolve.yml`` with the real role defaults) and
render the real ``config/authelia/configuration.yml.j2`` with the result.

Run: ``pytest tests/python/test_authelia_grafana_claims_policy.py``
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
AUTHELIA_TEMPLATE = REPO_ROOT / "config" / "authelia" / "configuration.yml.j2"
RESOLVE_TASKS = REPO_ROOT / "ansible" / "tasks" / "authelia_oidc_resolve.yml"
AUTH_DEFAULTS = REPO_ROOT / "ansible" / "roles" / "auth" / "defaults" / "main.yml"

DOMAIN = "example.com"
# Claim names exactly as in the official Grafana guide (id_token list).
OFFICIAL_CLAIMS = {"email", "name", "groups", "preferred_username"}

FORGEJO = {
    "id": "forgejo",
    "redirect_uris": ["https://git.example.com/user/oauth2/Authelia/callback"],
    "claims_policy": "forgejo_policy",
}
GRAFANA_WITH_POLICY = {
    "id": "grafana",
    "redirect_uris": [f"https://grafana.{DOMAIN}/login/generic_oauth"],
    "claims_policy": "grafana",
}
OTHER_POLICY = {"other": {"id_token": ["email"]}}
FORGEJO_POLICY = {"forgejo_policy": {"id_token": ["groups"]}}


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


@pytest.fixture
def resolve(ansible_available, tmp_path):
    """Run the shared resolve tasks; return (clients, claims_policies)."""
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            ---
            - name: resolve wrapper
              hosts: localhost
              connection: local
              gather_facts: false
              vars_files:
                - {AUTH_DEFAULTS}
              tasks:
                - ansible.builtin.import_tasks: {RESOLVE_TASKS}
                - ansible.builtin.copy:
                    dest: "{{{{ out_file }}}}"
                    content: >-
                      {{{{ {{'clients': authelia_oidc_clients,
                            'claims_policies': authelia_oidc_claims_policies}} | to_json }}}}
            """
        )
    )

    def _run(auth: dict | None = None) -> tuple[list[dict], dict]:
        out = tmp_path / "resolved.json"
        extra = {
            "auth_provider": "authelia",
            "drayve_domain": DOMAIN,
            "out_file": str(out),
            "host_secrets": {
                "authelia_oidc_grafana_secret": "g-plain",
                "authelia_oidc_forgejo_secret": "f-plain",
            },
        }
        if auth is not None:
            extra["auth"] = auth
        else:
            extra["auth"] = {}
        r = subprocess.run(
            ["ansible-playbook", "-i", "localhost,", str(wrapper), "-e", json.dumps(extra)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        data = json.loads(out.read_text())
        return data["clients"], data["claims_policies"]

    return _run


@pytest.fixture
def render_cfg(ansible_jinja_env):
    env = ansible_jinja_env(AUTHELIA_TEMPLATE.parent)
    template = env.get_template(AUTHELIA_TEMPLATE.name)

    def _render(clients: list[dict], policies: dict) -> tuple[str, dict]:
        ctx = {
            "drayve_domain": DOMAIN,
            "auth_lldap": False,
            "lldap_port": 3890,
            "lldap_base_dn": "DC=drayve,DC=local",
            "authelia_ldap_password": "x",
            "authelia_session_secret": "s",
            "authelia_storage_encryption_key": "k",
            "authelia_jwt_secret": "j",
            "authelia_oidc_hmac_secret": "h",
            "authelia_jwks_pem": "-----BEGIN RSA PRIVATE KEY-----\nstub\n-----END RSA PRIVATE KEY-----\n",
            "authelia_oidc_clients": clients,
            "authelia_oidc_authorization_policies": {},
            "authelia_oidc_claims_policies": policies,
            "authelia_oidc_client_secret_hashes": {c["id"]: "$pbkdf2-sha512$1$a$b" for c in clients},
        }
        text = template.render(**ctx)
        return text, yaml.safe_load(text)  # (f) must stay valid YAML

    return _render


def _client(cfg: dict, cid: str) -> dict:
    return next(c for c in cfg["identity_providers"]["oidc"]["clients"] if c["client_id"] == cid)


def _policies(cfg: dict) -> dict:
    return cfg["identity_providers"]["oidc"].get("claims_policies", {})


# (a) default configuration
def test_default_renders_grafana_client_with_claims_policy(resolve, render_cfg):
    _, cfg = render_cfg(*resolve())
    assert _client(cfg, "grafana")["claims_policy"] == "grafana"
    assert set(_policies(cfg)["grafana"]["id_token"]) == OFFICIAL_CLAIMS


# (b) own policies without the built-in one: built-in is still rendered
def test_own_claims_policies_keep_builtin_policy(resolve, render_cfg):
    _, cfg = render_cfg(*resolve({"claims_policies": OTHER_POLICY}))
    pols = _policies(cfg)
    assert set(pols["grafana"]["id_token"]) == OFFICIAL_CLAIMS
    assert pols["other"]["id_token"] == ["email"]
    assert _client(cfg, "grafana")["claims_policy"] == "grafana"


# (c) own policy with the same name wins
def test_own_policy_of_same_name_wins(resolve, render_cfg):
    _, cfg = render_cfg(*resolve({"claims_policies": {"grafana": {"id_token": ["groups"]}}}))
    assert _policies(cfg)["grafana"]["id_token"] == ["groups"]


# (d) no client points at a policy that is not rendered
SCENARIOS = {
    "default": None,
    "own_policies_only": {"claims_policies": OTHER_POLICY},
    "own_grafana_policy": {"claims_policies": {"grafana": {"id_token": ["groups"]}}},
    "own_clients_builtin_policy_name": {"oidc_clients": [GRAFANA_WITH_POLICY]},
    "own_clients_and_policies": {
        "oidc_clients": [GRAFANA_WITH_POLICY, FORGEJO],
        "claims_policies": FORGEJO_POLICY,
    },
}


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_every_referenced_claims_policy_is_rendered(resolve, render_cfg, scenario):
    text, cfg = render_cfg(*resolve(SCENARIOS[scenario]))
    pols = _policies(cfg)
    for c in cfg["identity_providers"]["oidc"]["clients"]:
        if "claims_policy" in c:
            assert c["claims_policy"] in pols, (c["client_id"], text)


def test_own_client_list_does_not_get_policy_automatically(resolve, render_cfg):
    own = {k: v for k, v in GRAFANA_WITH_POLICY.items() if k != "claims_policy"}
    _, cfg = render_cfg(*resolve({"oidc_clients": [own]}))
    assert "claims_policy" not in _client(cfg, "grafana")


# Documentation: copyable example carries the policy, rule is described.
def test_docs_example_and_rule():
    doc = (REPO_ROOT / "docs" / "auth-oidc-clients.md").read_text()
    assert "claims_policy: grafana" in doc
    assert "Viewer" in doc
