"""Round 2 of fix/authentik-389ds: findings from the restore-box rehearsal.

1. The landing proxy provider must use ``services.landing.domain`` (the apex is often not ours),
   falling back to ``drayve_domain``; an existing provider with another host is corrected.
2. With ``auth.provider: authentik`` Grafana is NOT behind the forwardAuth middleware (login via OIDC only).
3. The embedded outpost needs ``authentik_host`` = https://auth.<domain>, else the forwardAuth redirect
   points at http://localhost.
4. ``POST /sources/ldap/{pk}/sync/`` does not exist in authentik 2026.8 (404); authentik syncs on source save.

Run: ``pytest tests/python/test_authentik_round2.py``
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "ansible" / "roles" / "authentik"
COMPOSE = REPO_ROOT / "ansible" / "templates" / "docker-compose.yml.j2"

BASE_VARS = dict(
    drayve_domain="example.test",
    authentik_oidc_grafana_secret="dummy",
    authentik_ldap_enabled=True,
    authentik_ldap_host="ldap.example.test",
    authentik_ldap_port=636,
    authentik_ldap_base_dn="dc=example,dc=test",
    authentik_ldap_bind_dn="uid=bind,dc=example,dc=test",
    authentik_ldap_sync_interval=60,
    authentik_ldap_start_tls=False,
    authentik_ldap_user_dn="ou=people",
    authentik_ldap_group_dn="ou=groups",
    authentik_ldap_user_filter="(|(objectClass=inetOrgPerson)(objectClass=nsOrgPerson))",
    authentik_ldap_group_filter="(objectClass=groupOfNames)",
    authentik_ldap_uniqueness_field="entryUUID",
    authentik_ldap_group_membership_field="member",
    authentik_ldap_user_mappings=["openldap-uid", "openldap-cn", "default-mail", "default-dn-path"],
    authentik_ldap_group_name_attr="cn",
    authentik_ldap_tls_ca_name="",
    authentik_admin_group="infra-admins",
)


@pytest.fixture
def load(ansible_jinja_env, tmp_path):
    env = ansible_jinja_env(ROLE / "templates")
    env.filters["ternary"] = lambda cond, yes, no=None: yes if cond else no

    def _load(**overrides):
        rendered = env.get_template("bootstrap.py.j2").render(
            **{**BASE_VARS, "authentik_landing_domain": "dash.example.test", **overrides})
        path = tmp_path / "bootstrap_rendered.py"
        path.write_text(rendered)
        spec = importlib.util.spec_from_file_location("bootstrap_rendered", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    return _load


class Rec:
    """API double that records calls; answers from ``gets`` (path prefix -> payload)."""

    def __init__(self, gets=None):
        self.gets = gets or {}
        self.get_paths, self.posts, self.patches = [], [], []

    def get(self, path):
        self.get_paths.append(path)
        for prefix, payload in self.gets.items():
            if path.startswith(prefix):
                return payload
        return {"results": [{"pk": "x"}]}

    def post(self, path, data):
        self.posts.append((path, data))
        return {"pk": "x"}

    def patch(self, path, data):
        self.patches.append((path, data))
        return {}


# --- 1. Landing domain ------------------------------------------------------

def test_landing_provider_uses_landing_domain(load, monkeypatch):
    b = load(authentik_ldap_enabled=False)
    seen = {}
    monkeypatch.setattr(b, "get_container_ip", lambda n: "127.0.0.1")
    monkeypatch.setattr(b, "wait_for_ready", lambda *a, **k: None)
    monkeypatch.setattr(b, "ensure_api_token", lambda: "tok")
    monkeypatch.setattr(b, "AuthentikAPI", lambda base, tok: Rec())
    monkeypatch.setattr(b, "wait_for_flows", lambda api: ("af", "if"))
    monkeypatch.setattr(b, "get_embedded_outpost", lambda api: None)
    monkeypatch.setattr(b, "ensure_proxy_provider",
                        lambda api, name, host, a, i: seen.setdefault(name, host) and 1)
    monkeypatch.setattr(b, "ensure_application", lambda *a, **k: None)
    monkeypatch.setattr(b, "assign_to_outpost", lambda *a, **k: None)
    monkeypatch.setattr(b, "ensure_oauth2_provider", lambda *a, **k: 1)
    monkeypatch.setattr(b, "get_oauth2_scope_mappings", lambda a: ["s"])
    monkeypatch.setattr(b, "ensure_admin_group", lambda *a, **k: "grp")
    monkeypatch.setattr(b, "ensure_group_binding", lambda *a, **k: None)
    b.main()
    assert seen["landing"] == "https://dash.example.test"
    assert seen["traefik-dashboard"] == "https://traefik.example.test"


def _default(name):
    return yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())[name]


@pytest.mark.parametrize("ctx,expected", [
    ({"drayve_domain": "example.test"}, "example.test"),
    ({"drayve_domain": "example.test", "services": {}}, "example.test"),
    ({"drayve_domain": "example.test", "services": {"landing": {"domain": ""}}}, "example.test"),
    ({"drayve_domain": "example.test", "services": {"landing": {"domain": "dash.example.test"}}},
     "dash.example.test"),
    ({"drayve_domain": "example.test", "landing_domain": "dash.example.test"}, "dash.example.test"),
])
def test_landing_domain_default_falls_back_to_drayve_domain(ctx, expected):
    assert Environment().from_string(_default("authentik_landing_domain")).render(**ctx) == expected


def test_existing_proxy_provider_with_wrong_host_is_corrected(load):
    b = load()
    api = Rec({"/providers/proxy/?name=": {"results": [
        {"pk": 2, "name": "landing", "external_host": "https://example.test",
         "mode": "forward_single"}]}})
    assert b.ensure_proxy_provider(api, "landing", "https://dash.example.test", "af", "if") == 2
    # mode must be sent too: authentik validates a partial update as proxy mode otherwise
    # (400 "Internal host cannot be empty when forward auth is disabled", seen live on 2026.8.6).
    assert api.patches == [("/providers/proxy/2/", {"external_host": "https://dash.example.test",
                                                     "mode": "forward_single"})]


def test_existing_proxy_provider_with_right_host_is_left_alone(load):
    b = load()
    api = Rec({"/providers/proxy/?name=": {"results": [
        {"pk": 2, "name": "landing", "external_host": "https://dash.example.test"}]}})
    b.ensure_proxy_provider(api, "landing", "https://dash.example.test", "af", "if")
    assert api.patches == []


# --- 2. Grafana without forwardAuth under authentik ---------------------------

def test_grafana_router_is_not_gated_for_authentik():
    text = COMPOSE.read_text()
    line = '      - "traefik.http.routers.grafana.middlewares=auth@file"\n'
    before = text[:text.index(line)].rstrip().splitlines()[-1]
    assert before.startswith("{% if") and "authentik" in before, before


# --- 3. Embedded outpost host -----------------------------------------------

def test_outpost_authentik_host_is_set_and_config_preserved(load):
    b = load()
    api = Rec({"/outposts/instances/5/": {"config": {"log_level": "info",
                                                      "authentik_host": "http://localhost"}}})
    b.ensure_outpost_host(api, 5)
    (path, data), = api.patches
    assert path == "/outposts/instances/5/"
    assert data["config"]["authentik_host"] == "https://auth.example.test"
    assert data["config"]["log_level"] == "info"


def test_outpost_host_is_idempotent(load):
    b = load()
    api = Rec({"/outposts/instances/5/": {"config": {"authentik_host": "https://auth.example.test"}}})
    b.ensure_outpost_host(api, 5)
    assert api.patches == []


# --- 4. No dead sync trigger ----------------------------------------------------

def test_bootstrap_never_posts_to_a_sync_endpoint(load, monkeypatch, capsys):
    b = load()
    api = Rec({"/outposts/": {"results": []}})
    monkeypatch.setattr(b, "get_container_ip", lambda n: "127.0.0.1")
    monkeypatch.setattr(b, "wait_for_ready", lambda *a, **k: None)
    monkeypatch.setattr(b, "ensure_api_token", lambda: "tok")
    monkeypatch.setattr(b, "AuthentikAPI", lambda base, tok: api)
    monkeypatch.setattr(b, "wait_for_flows", lambda a: ("af", "if"))
    monkeypatch.setattr(b, "get_embedded_outpost", lambda a: None)
    monkeypatch.setattr(b, "ensure_proxy_provider", lambda *a, **k: 1)
    monkeypatch.setattr(b, "ensure_application", lambda *a, **k: None)
    monkeypatch.setattr(b, "assign_to_outpost", lambda *a, **k: None)
    monkeypatch.setattr(b, "ensure_oauth2_provider", lambda *a, **k: 1)
    monkeypatch.setattr(b, "get_oauth2_scope_mappings", lambda a: ["s"])
    monkeypatch.setattr(b, "ensure_admin_group", lambda *a, **k: "grp")
    monkeypatch.setattr(b, "ensure_group_binding", lambda *a, **k: None)
    monkeypatch.setattr(b, "ensure_ldap_source", lambda a, pw: "drayve-ldap")
    monkeypatch.setenv("AUTHENTIK_LDAP_BIND_PASSWORD", "pw")
    b.main()
    assert [p for p, _ in api.posts if "/sync" in p] == []
    assert not hasattr(b, "trigger_ldap_sync")
    assert "sync trigger returned" not in capsys.readouterr().err
