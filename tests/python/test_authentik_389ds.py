"""Tests for the Authentik module against a host-native 389-DS (fix/authentik-389ds).

Found live on a throwaway restore box (Authentik + 389-DS with nsPerson accounts):

1. ``authentik-server`` was OOM-killed at ``mem_authentik_server: 512m`` (real
   use ~700 MiB; the bootstrap's ``docker exec`` Django shell lives in the same cgroup).
2. Traefik: ``Router authentik cannot be linked automatically with multiple
   Services`` -- the routers carried no ``.service=`` label.
3. The LDAP mapping selection took everything with "ldap" in ``managed`` (also
   ``ms-samaccountname``, which yields ``username None`` on 389-DS), the user filter
   missed nsPerson accounts, and the group sync had no mapping that sets ``name``.

Run: ``pytest tests/python/test_authentik_389ds.py``
"""

from __future__ import annotations

import importlib.util
import re
import urllib.parse
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "ansible" / "roles" / "authentik"
TEMPLATE_DIR = ROLE / "templates"
VARS = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "all" / "vars.yml"
COMPOSE = REPO_ROOT / "ansible" / "templates" / "docker-compose.yml.j2"
SCHEMA = REPO_ROOT / "config" / "stack.schema.yaml"

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
)

MAPPINGS = [
    {"pk": "m-uid", "name": "OpenLDAP uid", "managed": "goauthentik.io/sources/ldap/openldap-uid"},
    {"pk": "m-cn", "name": "OpenLDAP cn", "managed": "goauthentik.io/sources/ldap/openldap-cn"},
    {"pk": "m-mail", "name": "mail", "managed": "goauthentik.io/sources/ldap/default-mail"},
    {"pk": "m-dn", "name": "dn path", "managed": "goauthentik.io/sources/ldap/default-dn-path"},
    {"pk": "m-ms", "name": "ms sam", "managed": "goauthentik.io/sources/ldap/ms-samaccountname"},
    {"pk": "m-name", "name": "name", "managed": "goauthentik.io/sources/ldap/default-name"},
]


@pytest.fixture
def load(ansible_jinja_env, tmp_path):
    env = ansible_jinja_env(TEMPLATE_DIR)
    env.filters["ternary"] = lambda cond, yes, no=None: yes if cond else no

    def _load(**overrides):
        rendered = env.get_template("bootstrap.py.j2").render(**{**BASE_VARS, **overrides})
        path = tmp_path / "bootstrap_rendered.py"
        path.write_text(rendered)
        spec = importlib.util.spec_from_file_location("bootstrap_rendered", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    return _load


class FakeAPI:
    def __init__(self, existing_group_mapping=False, keypairs=None):
        self.mappings = list(MAPPINGS)
        if existing_group_mapping:
            self.mappings.append({"pk": "gm-old", "name": "drayve LDAP Mapping: group name",
                                  "managed": None})
        self.keypairs = keypairs or {}
        self.posts = []
        self.patches = []

    def get(self, path):
        if path.startswith("/propertymappings/source/ldap/"):
            if "name=" in path:
                name = path.split("name=")[1].split("&")[0]
                name = urllib.parse.unquote(name)
                return {"results": [m for m in self.mappings if m["name"] == name]}
            return {"results": self.mappings}
        if path.startswith("/crypto/certificatekeypairs/"):
            name = path.split("name=")[1].split("&")[0]
            return {"results": [{"pk": self.keypairs[name], "name": name}] if name in self.keypairs else []}
        if path.startswith("/sources/ldap/"):
            return {"results": []}
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, data):
        self.posts.append((path, data))
        if path.startswith("/propertymappings/source/ldap/"):
            self.mappings.append({"pk": "gm-new", "name": data["name"], "managed": None})
            return {"pk": "gm-new"}
        return {"pk": "src-1"}

    def patch(self, path, data):
        self.patches.append((path, data))
        return {}


def _source_post(api):
    return next(d for p, d in api.posts if p == "/sources/ldap/")


# --- Fix 1: memory headroom -------------------------------------------------

def _mib(value: str) -> int:
    m = re.fullmatch(r"(\d+)([mg])", value.strip().lower())
    assert m, f"unparseable memory limit {value!r}"
    return int(m.group(1)) * (1024 if m.group(2) == "g" else 1)


def test_authentik_server_limit_leaves_headroom_for_bootstrap_shell():
    # ~700 MiB resident + a second Django shell (docker exec, same cgroup).
    limit = _mib(yaml.safe_load(VARS.read_text())["mem_authentik_server"])
    assert limit >= 1024, f"mem_authentik_server={limit}m is below the 1g floor (OOM-kill seen at 512m)"


# --- Fix 2: Traefik service binding -----------------------------------------

@pytest.mark.parametrize("router", ["authentik", "authentik-outpost"])
def test_authentik_routers_bind_their_service_explicitly(router):
    text = COMPOSE.read_text()
    assert f'"traefik.http.routers.{router}.service={router}"' in text, (
        f"router {router} needs an explicit .service= label (two services on one container)"
    )


# --- Fix 3: mapping selection, group mapping, LDAP source parameters --------

def test_only_configured_user_mappings_are_selected(load):
    b = load()
    api = FakeAPI()
    b.ensure_ldap_source(api, "pw")
    selected = set(_source_post(api)["user_property_mappings"])
    assert selected == {"m-uid", "m-cn", "m-mail", "m-dn"}
    assert "m-ms" not in selected and "m-name" not in selected


def test_user_mapping_list_is_a_role_variable(load):
    b = load(authentik_ldap_user_mappings=["default-name"])
    api = FakeAPI()
    b.ensure_ldap_source(api, "pw")
    assert _source_post(api)["user_property_mappings"] == ["m-name"]


def test_group_mapping_sets_name_from_cn_and_group_sync_is_on(load):
    b = load()
    api = FakeAPI()
    b.ensure_ldap_source(api, "pw")
    (_, mapping), = [(p, d) for p, d in api.posts if p.startswith("/propertymappings/source/ldap/")]
    assert 'ldap.get("cn")' in mapping["expression"]
    data = _source_post(api)
    assert data["group_property_mappings"] == ["gm-new"]
    assert data["sync_groups"] is True


def test_group_mapping_is_reused_on_rerun(load):
    b = load()
    api = FakeAPI(existing_group_mapping=True)
    b.ensure_ldap_source(api, "pw")
    assert [p for p, _ in api.posts if p.startswith("/propertymappings/")] == []
    assert _source_post(api)["group_property_mappings"] == ["gm-old"]


def test_defaults_match_389ds_native_layout(load):
    b = load()
    api = FakeAPI()
    b.ensure_ldap_source(api, "pw")
    d = _source_post(api)
    assert d["user_object_filter"] == "(|(objectClass=inetOrgPerson)(objectClass=nsOrgPerson))"
    assert d["group_object_filter"] == "(objectClass=groupOfNames)"
    assert d["object_uniqueness_field"] == "entryUUID"
    assert d["group_membership_field"] == "member"
    assert d["additional_user_dn"] == "ou=people"
    assert d["additional_group_dn"] == "ou=groups"
    assert d["server_uri"] == "ldaps://ldap.example.test:636"
    assert d["peer_certificate"] is None


def test_ldap_source_parameters_are_overridable(load):
    b = load(authentik_ldap_user_filter="(objectClass=person)",
             authentik_ldap_group_filter="(objectClass=groupOfUniqueNames)",
             authentik_ldap_uniqueness_field="uid",
             authentik_ldap_group_membership_field="uniqueMember",
             authentik_ldap_user_dn="", authentik_ldap_group_dn="ou=teams")
    api = FakeAPI()
    b.ensure_ldap_source(api, "pw")
    d = _source_post(api)
    assert d["user_object_filter"] == "(objectClass=person)"
    assert d["group_object_filter"] == "(objectClass=groupOfUniqueNames)"
    assert d["object_uniqueness_field"] == "uid"
    assert d["group_membership_field"] == "uniqueMember"
    assert d["additional_user_dn"] == ""
    assert d["additional_group_dn"] == "ou=teams"


def test_tls_verification_uses_named_ca_keypair(load):
    b = load(authentik_ldap_tls_ca_name="ewh-ca")
    api = FakeAPI(keypairs={"ewh-ca": "kp-1"})
    b.ensure_ldap_source(api, "pw")
    assert _source_post(api)["peer_certificate"] == "kp-1"


def test_tls_verification_with_unknown_ca_fails_instead_of_silently_skipping(load):
    b = load(authentik_ldap_tls_ca_name="missing-ca")
    api = FakeAPI()
    with pytest.raises(RuntimeError, match="missing-ca"):
        b.ensure_ldap_source(api, "pw")
    assert [p for p, _ in api.posts if p == "/sources/ldap/"] == []


def test_every_ldap_role_default_has_a_schema_key():
    defaults = (ROLE / "defaults" / "main.yml").read_text()
    keys = set(re.findall(r"auth\.ldap\.(\w+)", defaults))
    assert {"user_filter", "group_filter", "uniqueness_field", "group_membership_field",
            "user_dn", "group_dn", "user_mappings", "group_name_attr", "tls_ca_name"} <= keys
    schema = yaml.safe_load(SCHEMA.read_text())
    props = schema["properties"]["auth"]["properties"]["ldap"]["properties"]
    assert keys <= set(props), f"auth.ldap keys without schema entry: {sorted(keys - set(props))}"


# --- Pin ----------------------------------------------------------------------

def _versions(path: Path) -> set[str]:
    return set(re.findall(r"goauthentik/server:(\d+\.\d+\.\d+)", path.read_text()))


def test_authentik_pin_is_2026_8_6_everywhere():
    files = [VARS, COMPOSE, REPO_ROOT / "molecule" / "authentik" / "molecule.yml",
             REPO_ROOT / "molecule" / "authentik-ldap" / "molecule.yml",
             REPO_ROOT / "docs" / "configuration.md"]
    for f in files:
        assert _versions(f) == {"2026.8.6"}, f"{f.relative_to(REPO_ROOT)}: {_versions(f)}"
