"""Round 3 of fix/authentik-389ds: admin pages only for one LDAP group (``auth.admin_group``).

Before, every authenticated account passed Authentik's proxy applications (no policy binding = allow all) and
Grafana gave Viewer to everybody. Now the bootstrap binds Landing, Traefik dashboard, whoami and the Grafana
application to a group policy on ``auth.admin_group`` (name = cn of the synced LDAP group); an empty value is
rejected (no default allow). Grafana: Admin for members of that group, no role = no access.

Run: ``pytest tests/python/test_authentik_admin_group.py``
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "ansible" / "roles" / "authentik"
COMPOSE = REPO_ROOT / "ansible" / "templates" / "docker-compose.yml.j2"
SCHEMA = REPO_ROOT / "config" / "stack.schema.yaml"
PREFLIGHT = REPO_ROOT / "ansible" / "tasks" / "auth_preflight.yml"
VALIDATOR = REPO_ROOT / "scripts" / "validate-stack.py"

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


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()

    def _patch(mod):
        monkeypatch.setattr(mod.time, "time", c.time)
        monkeypatch.setattr(mod.time, "sleep", c.sleep)
        return c

    return _patch


class Rec:
    def __init__(self, gets=None):
        self.gets = gets or {}
        self.get_paths, self.posts, self.patches = [], [], []

    def get(self, path):
        self.get_paths.append(path)
        for prefix, payload in self.gets.items():
            if path.startswith(prefix):
                return payload(len(self.get_paths)) if callable(payload) else payload
        return {"results": []}

    def post(self, path, data):
        self.posts.append((path, data))
        return {"pk": "new-pk"}

    def patch(self, path, data):
        self.patches.append((path, data))
        return {}


# --- 1. group lookup + application bindings ---------------------------------------

def test_admin_group_is_looked_up_by_name(load):
    b = load()
    api = Rec({"/core/groups/?name=": {"results": [{"pk": "g1", "name": "infra-admins"}]}})
    assert b.ensure_admin_group(api, "infra-admins") == "g1"


def test_admin_group_appears_after_ldap_sync(load, clock):
    b = load()
    c = clock(b)
    api = Rec({"/core/groups/?name=": lambda n: {"results": [{"pk": "g1", "name": "infra-admins"}]} if n >= 3
               else {"results": []}})
    assert b.ensure_admin_group(api, "infra-admins") == "g1"
    assert len(c.sleeps) == 2


def test_missing_admin_group_with_ldap_fails_clearly(load, clock):
    b = load()
    clock(b)
    api = Rec()
    with pytest.raises(RuntimeError, match="infra-admins"):
        b.ensure_admin_group(api, "infra-admins", timeout=30, interval=5)
    assert api.posts == []


def test_missing_admin_group_without_ldap_is_created_locally(load, clock):
    b = load(authentik_ldap_enabled=False)
    clock(b)
    api = Rec()
    assert b.ensure_admin_group(api, "infra-admins") == "new-pk"
    assert api.posts == [("/core/groups/", {"name": "infra-admins"})]


def test_binding_is_created_for_application_and_group(load):
    b = load()
    api = Rec()
    b.ensure_group_binding(api, "app1", "g1")
    (path, data), = api.posts
    assert path == "/policies/bindings/"
    assert data["target"] == "app1" and data["group"] == "g1"
    assert data["enabled"] is True and data["negate"] is False and data["order"] == 0


def test_binding_is_idempotent(load):
    b = load()
    api = Rec({"/policies/bindings/?": {"results": [{"pk": "b1", "target": "app1", "group": "g1"}]}})
    b.ensure_group_binding(api, "app1", "g1")
    assert api.posts == []


def test_ensure_application_returns_pk(load):
    b = load()
    api = Rec({"/core/applications/?slug=": {"results": [{"pk": "a1", "slug": "landing"}]}})
    assert b.ensure_application(api, "Landing", "landing", 2) == "a1"
    api = Rec()
    assert b.ensure_application(api, "Landing", "landing", 2) == "new-pk"


def _wire_main(b, monkeypatch, api):
    monkeypatch.setattr(b, "get_container_ip", lambda n: "127.0.0.1")
    monkeypatch.setattr(b, "wait_for_ready", lambda *a, **k: None)
    monkeypatch.setattr(b, "ensure_api_token", lambda: "tok")
    monkeypatch.setattr(b, "AuthentikAPI", lambda base, tok: api)
    monkeypatch.setattr(b, "wait_for_flows", lambda a: ("af", "if"))
    monkeypatch.setattr(b, "get_embedded_outpost", lambda a: None)
    monkeypatch.setattr(b, "ensure_proxy_provider", lambda *a, **k: 1)
    monkeypatch.setattr(b, "assign_to_outpost", lambda *a, **k: None)
    monkeypatch.setattr(b, "ensure_oauth2_provider", lambda *a, **k: 1)
    monkeypatch.setattr(b, "get_oauth2_scope_mappings", lambda a: ["s"])
    monkeypatch.setattr(b, "ensure_ldap_source", lambda a, pw: "drayve-ldap")
    monkeypatch.setenv("AUTHENTIK_LDAP_BIND_PASSWORD", "pw")


def test_main_binds_every_application_to_the_admin_group(load, monkeypatch):
    b = load()
    api = Rec()
    _wire_main(b, monkeypatch, api)
    monkeypatch.setattr(b, "ensure_application", lambda a, name, slug, pk: f"app-{slug}")
    monkeypatch.setattr(b, "ensure_admin_group", lambda a, name, **k: "grp")
    bound = []
    monkeypatch.setattr(b, "ensure_group_binding", lambda a, app, grp: bound.append((app, grp)))
    b.main()
    assert sorted(bound) == [("app-grafana", "grp"), ("app-landing", "grp"),
                             ("app-traefik-dashboard", "grp"), ("app-whoami", "grp")]


def test_main_binds_before_ldap_sync_is_awaited_only_after_source_exists(load, monkeypatch):
    # The group comes from the LDAP sync, so the source must be saved before the group lookup.
    b = load()
    order = []
    api = Rec()
    _wire_main(b, monkeypatch, api)
    monkeypatch.setattr(b, "ensure_application", lambda a, name, slug, pk: f"app-{slug}")
    monkeypatch.setattr(b, "ensure_ldap_source", lambda a, pw: order.append("ldap") or "drayve-ldap")
    monkeypatch.setattr(b, "ensure_admin_group", lambda a, name, **k: order.append("group") or "grp")
    monkeypatch.setattr(b, "ensure_group_binding", lambda *a: None)
    b.main()
    assert order == ["ldap", "group"]


def test_empty_admin_group_aborts_before_any_api_call(load, monkeypatch):
    b = load(authentik_admin_group="")
    api = Rec()
    _wire_main(b, monkeypatch, api)
    with pytest.raises(RuntimeError, match="admin_group"):
        b.main()
    assert api.posts == [] and api.patches == [] and api.get_paths == []


# --- Preflight / validator / schema -----------------------------------------------

def _preflight_task():
    tasks = yaml.safe_load(PREFLIGHT.read_text())
    return next(t for t in tasks if "admin_group" in t["name"])


@pytest.mark.parametrize("auth,fails", [
    ({"provider": "authentik"}, True),
    ({"provider": "authentik", "admin_group": ""}, True),
    ({"provider": "authentik", "admin_group": "infra-admins"}, False),
    ({"provider": "authentik", "admin_group": "it's"}, True),
    ({"provider": "basic"}, False),
])
def test_preflight_requires_admin_group_for_authentik(auth, fails):
    t = _preflight_task()
    env = Environment()
    env.tests["match"] = lambda v, pat: re.match(pat, v) is not None  # Ansible test
    ctx = {"auth": auth}
    if not env.compile_expression(t["when"])(**ctx):
        assert not fails
        return
    ok = all(env.compile_expression(c)(**ctx) for c in t["ansible.builtin.assert"]["that"])
    assert ok is not fails


def _validate(auth):
    import importlib.util as u
    spec = u.spec_from_file_location("validate_stack", VALIDATOR)
    mod = u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cfg = {"drayve_version": "0.1.0",
           "stack": {"name": "x", "domain": "example.org", "acme_email": "ops@example.org"}, "auth": auth}
    return [e for e in mod.validate(cfg)[0] if "admin_group" in e]


def test_validator_rejects_authentik_without_admin_group():
    assert _validate({"provider": "authentik"})
    assert _validate({"provider": "authentik", "admin_group": "it's"})
    assert not _validate({"provider": "authentik", "admin_group": "infra-admins"})
    assert not _validate({"provider": "basic"})


def test_schema_and_example_know_admin_group():
    props = yaml.safe_load(SCHEMA.read_text())["properties"]["auth"]["properties"]
    assert props["admin_group"]["type"] == "string"
    ex = yaml.safe_load((REPO_ROOT / "examples" / "stack-authentik.yaml").read_text())
    assert ex["auth"]["admin_group"]
    d = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
    assert "auth.admin_group" in d["authentik_admin_group"]


# --- 2. Grafana role -----------------------------------------------------------------

def test_grafana_admin_role_follows_admin_group_and_is_strict():
    text = COMPOSE.read_text()
    line = next(l for l in text.splitlines() if "GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_PATH" in l
                and "authentik" in l.lower() or "ROLE_ATTRIBUTE_PATH" in l and "groups[*]" in l)
    assert "admin_group" in line and "authentik Admins" not in line, line
    assert "'Viewer'" not in line, line
    assert "GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_STRICT=true" in text


# --- Application lookup must see applications hidden by a group binding ------------------

def test_application_lookup_asks_for_the_full_superuser_list(load):
    # Without superuser_full_list the list only holds applications the API user may access:
    # once bound to the admin group, akadmin no longer sees them, the POST then fails with 400.
    b = load()
    api = Rec({"/core/applications/?slug=": {"results": [{"pk": "a1", "slug": "landing"}]}})
    b.ensure_application(api, "Landing", "landing", 2)
    assert "superuser_full_list=true" in api.get_paths[0]


# --- Grafana needs the groups claim: OAuth2 scope mappings -------------------------------

SCOPES = [
    {"pk": "s-openid", "managed": "goauthentik.io/providers/oauth2/scope-openid"},
    {"pk": "s-email", "managed": "goauthentik.io/providers/oauth2/scope-email"},
    {"pk": "s-profile", "managed": "goauthentik.io/providers/oauth2/scope-profile"},
    {"pk": "s-api", "managed": "goauthentik.io/providers/oauth2/scope-authentik_api"},
]


def test_oauth2_scope_mappings_are_openid_email_profile_only(load):
    b = load()
    api = Rec({"/propertymappings/provider/scope/": {"results": SCOPES}})
    assert sorted(b.get_oauth2_scope_mappings(api)) == ["s-email", "s-openid", "s-profile"]


def test_oauth2_scope_mappings_missing_one_fails_after_timeout(load, clock):
    b = load()
    clock(b)
    api = Rec({"/propertymappings/provider/scope/": {"results": SCOPES[:2]}})
    with pytest.raises(RuntimeError, match="profile"):
        b.get_oauth2_scope_mappings(api)


def test_oauth2_scope_mappings_are_awaited_like_the_flows(load, clock):
    """Blueprints create the scope mappings asynchronously after /-/health/ready/ (release gate
    authentik-ldap: 'OAuth2 scope mappings missing: openid, email, profile' 31s after ready)."""
    b = load()
    c = clock(b)
    api = Rec({"/propertymappings/provider/scope/":
               lambda n: {"results": [] if n < 3 else SCOPES}})
    assert sorted(b.get_oauth2_scope_mappings(api)) == ["s-email", "s-openid", "s-profile"]
    assert len(c.sleeps) == 2


def test_new_oauth2_provider_gets_scope_mappings(load):
    # Without them the userinfo has no groups claim: Grafana (role_attribute_strict) rejects every login.
    b = load()
    api = Rec()
    b.ensure_oauth2_provider(api, "grafana", "grafana", "sec", "https://g/cb", "af", "if",
                             scope_mappings=["s-openid", "s-profile"])
    (path, data), = api.posts
    assert path == "/providers/oauth2/" and data["property_mappings"] == ["s-openid", "s-profile"]


def test_existing_oauth2_provider_without_scope_mappings_is_patched(load):
    b = load()
    api = Rec({"/providers/oauth2/?name=": {"results": [{"pk": 4, "name": "grafana", "property_mappings": []}]}})
    assert b.ensure_oauth2_provider(api, "grafana", "grafana", "sec", "https://g/cb", "af", "if",
                                    scope_mappings=["s-openid", "s-profile"]) == 4
    assert api.patches == [("/providers/oauth2/4/", {"property_mappings": ["s-openid", "s-profile"]})]


def test_existing_oauth2_provider_with_scope_mappings_is_left_alone(load):
    b = load()
    api = Rec({"/providers/oauth2/?name=": {"results": [
        {"pk": 4, "name": "grafana", "property_mappings": ["s-profile", "s-openid"]}]}})
    b.ensure_oauth2_provider(api, "grafana", "grafana", "sec", "https://g/cb", "af", "if",
                             scope_mappings=["s-openid", "s-profile"])
    assert api.patches == []
