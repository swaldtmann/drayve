"""Tests for the LDAP property-mapping wait in the authentik bootstrap script.

authentik creates its built-in LDAP property mappings asynchronously via
blueprints. A single lookup right after ``/-/health/ready/`` can race them;
``POST /sources/ldap/`` with ``sync_users`` and an empty mapping list then
fails with HTTP 400 (release gate authentik-ldap). ``ensure_ldap_source`` must
wait for the configured user mappings and never send an empty user list. The group mapping is
created by the bootstrap itself (authentik ships none).

Same approach as ``test_authentik_bootstrap_flows.py``: template rendered with
dummy values, HTTP and ``time`` stubbed.

Run: ``pytest tests/python/test_authentik_bootstrap_ldap_mappings.py``
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "ansible" / "roles" / "authentik" / "templates"

USER_MAPPING = {"pk": "u1", "name": "authentik default LDAP Mapping: Name",
                "managed": "goauthentik.io/sources/ldap/default-name"}
# Not configured in the fixture (authentik_ldap_user_mappings=["default-name"]): must be ignored.
OTHER_MAPPING = {"pk": "x1", "name": "authentik default LDAP Mapping: ms-samaccountname",
                 "managed": "goauthentik.io/sources/ldap/ms-samaccountname"}


@pytest.fixture
def bootstrap(ansible_jinja_env, tmp_path):
    env = ansible_jinja_env(TEMPLATE_DIR)
    env.filters["ternary"] = lambda cond, yes, no=None: yes if cond else no
    rendered = env.get_template("bootstrap.py.j2").render(
        drayve_domain="example.test",
        authentik_oidc_grafana_secret="dummy",
        authentik_ldap_enabled=True,
        authentik_ldap_host="ldap.example.test",
        authentik_ldap_port=389,
        authentik_ldap_base_dn="dc=example,dc=test",
        authentik_ldap_bind_dn="cn=bind,dc=example,dc=test",
        authentik_ldap_sync_interval=60,
        authentik_ldap_start_tls=False,
        authentik_ldap_user_dn="ou=people",
        authentik_ldap_group_dn="ou=groups",
        authentik_ldap_user_filter="(|(objectClass=inetOrgPerson)(objectClass=nsOrgPerson))",
        authentik_ldap_group_filter="(objectClass=groupOfNames)",
        authentik_ldap_uniqueness_field="entryUUID",
        authentik_ldap_group_membership_field="member",
        authentik_ldap_user_mappings=["default-name"],
        authentik_ldap_group_name_attr="cn",
        authentik_ldap_tls_ca_name="",
    )
    path = tmp_path / "bootstrap_rendered.py"
    path.write_text(rendered)
    spec = importlib.util.spec_from_file_location("bootstrap_rendered", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeAPI:
    """Serves the mapping endpoint from call number ``ready_from`` on
    (``None`` = never)."""

    def __init__(self, ready_from):
        self.ready_from = ready_from
        self.mapping_calls = 0
        self.posts = []
        self.patches = []

    def get(self, path):
        if path.startswith("/propertymappings/source/ldap/?name="):
            return {"results": []}  # group mapping not there yet
        if path.startswith("/propertymappings/source/ldap/"):
            self.mapping_calls += 1
            if self.ready_from is not None and self.mapping_calls >= self.ready_from:
                return {"results": [USER_MAPPING, OTHER_MAPPING]}
            return {"results": []}
        if path.startswith("/sources/ldap/"):
            return {"results": []}
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, data):
        self.posts.append((path, data))
        if path.startswith("/propertymappings/source/ldap/"):
            return {"pk": "g1"}
        return {"pk": "src-1"}

    def patch(self, path, data):
        self.patches.append((path, data))
        return {}


@pytest.fixture
def clock(bootstrap, monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(bootstrap.time, "time", c.time)
    monkeypatch.setattr(bootstrap.time, "sleep", c.sleep)
    return c


def test_source_created_with_mappings_after_second_lookup(bootstrap, clock):
    api = FakeAPI(ready_from=2)
    slug = bootstrap.ensure_ldap_source(api, "pw")
    assert slug == "drayve-ldap"
    assert api.mapping_calls == 2
    assert len(clock.sleeps) == 1
    data, = [d for p, d in api.posts if p == "/sources/ldap/"]
    assert data["user_property_mappings"] == ["u1"]
    assert data["group_property_mappings"] == ["g1"]
    assert data["sync_groups"] is True


def test_never_ready_raises_and_sends_nothing(bootstrap, clock):
    api = FakeAPI(ready_from=None)
    with pytest.raises(RuntimeError, match="user"):
        bootstrap.wait_for_ldap_property_mappings(api, timeout=30, interval=5)
    with pytest.raises(RuntimeError):
        bootstrap.ensure_ldap_source(api, "pw")
    assert api.posts == []
    assert api.patches == []


def test_group_mapping_is_created_because_authentik_ships_none(bootstrap, clock):
    api = FakeAPI(ready_from=1)
    slug = bootstrap.ensure_ldap_source(api, "pw")
    assert slug == "drayve-ldap"
    assert clock.sleeps == []
    assert [p for p, _ in api.posts] == ["/propertymappings/source/ldap/", "/sources/ldap/"]


def test_missing_user_mappings_named_in_error(bootstrap, clock):
    api = FakeAPI(ready_from=None)
    with pytest.raises(RuntimeError) as exc:
        bootstrap.wait_for_ldap_property_mappings(api, timeout=30, interval=5)
    assert "user" in str(exc.value)
    assert "group" not in str(exc.value)


def test_all_present_means_one_lookup_and_no_sleep(bootstrap, clock):
    api = FakeAPI(ready_from=1)
    assert bootstrap.wait_for_ldap_property_mappings(api) == ["u1"]
    assert api.mapping_calls == 1
    assert clock.sleeps == []
