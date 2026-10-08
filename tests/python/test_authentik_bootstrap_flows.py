"""Tests for the flow wait in the authentik bootstrap script.

``ansible/roles/authentik/templates/bootstrap.py.j2`` runs right after
authentik reports ``/-/health/ready/``. The default flows are created
asynchronously by blueprints, so a single lookup can race them (CI run 38:
``Authorization flow: None`` -> HTTP error on ``POST /providers/proxy/``).
``wait_for_flows`` must poll until the flows exist and ``main`` must never
create a provider with an empty flow.

The template is rendered with dummy values and loaded as a module; HTTP and
``time`` are stubbed, no network and no real waiting.

Run: ``pytest tests/python/test_authentik_bootstrap_flows.py``
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "ansible" / "roles" / "authentik" / "templates"

IMPLICIT = "default-provider-authorization-implicit-consent"
EXPLICIT = "default-provider-authorization-explicit"
INVALIDATION = "default-provider-invalidation-flow"


@pytest.fixture
def bootstrap(ansible_jinja_env, tmp_path):
    env = ansible_jinja_env(TEMPLATE_DIR)
    # Ansible's ``ternary`` is not in the shared stubs; local stub only.
    env.filters["ternary"] = lambda cond, yes, no=None: yes if cond else no
    rendered = env.get_template("bootstrap.py.j2").render(
        drayve_domain="example.test",
        authentik_oidc_grafana_secret="dummy",
        authentik_ldap_enabled=False,
        authentik_ldap_host="ldap.example.test",
        authentik_ldap_port=389,
        authentik_ldap_base_dn="dc=example,dc=test",
        authentik_ldap_bind_dn="cn=bind,dc=example,dc=test",
        authentik_ldap_sync_interval=60,
        authentik_ldap_start_tls=False,
    )
    path = tmp_path / "bootstrap_rendered.py"
    path.write_text(rendered)
    spec = importlib.util.spec_from_file_location("bootstrap_rendered", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeClock:
    """Replaces time.time/time.sleep: sleep advances the clock, nothing blocks."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeAPI:
    """Serves ``/flows/instances/?slug=<slug>``.

    ``schedule[slug]`` is the 1-based call number from which the flow exists
    (``None`` = never).
    """

    def __init__(self, schedule, base_url=None, token=None):
        self.schedule = schedule
        self.calls = {}
        self.posts = []

    def get(self, path):
        assert path.startswith("/flows/instances/?slug="), path
        slug = path.split("slug=")[1].split("&")[0]
        n = self.calls[slug] = self.calls.get(slug, 0) + 1
        first = self.schedule.get(slug)
        if first is not None and n >= first:
            return {"results": [{"pk": f"pk-{slug}"}]}
        return {"results": []}

    def post(self, path, data):
        self.posts.append(path)
        raise AssertionError(f"unexpected POST {path}")


@pytest.fixture
def clock(bootstrap, monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(bootstrap.time, "time", c.time)
    monkeypatch.setattr(bootstrap.time, "sleep", c.sleep)
    return c


def test_flows_appear_on_third_poll(bootstrap, clock):
    api = FakeAPI({IMPLICIT: 3, INVALIDATION: 3})
    auth, inval = bootstrap.wait_for_flows(api)
    assert auth == f"pk-{IMPLICIT}"
    assert inval == f"pk-{INVALIDATION}"
    assert api.calls[IMPLICIT] == 3
    assert len(clock.sleeps) == 2


def test_explicit_used_when_implicit_never_appears(bootstrap, clock):
    api = FakeAPI({IMPLICIT: None, EXPLICIT: 1, INVALIDATION: 1})
    auth, inval = bootstrap.wait_for_flows(api)
    assert auth == f"pk-{EXPLICIT}"
    assert inval == f"pk-{INVALIDATION}"
    # Not switched to explicit right away: implicit got a grace period.
    assert api.calls[IMPLICIT] > 1
    assert sum(clock.sleeps) >= bootstrap.IMPLICIT_GRACE


def test_implicit_arriving_within_grace_beats_explicit(bootstrap, clock):
    api = FakeAPI({IMPLICIT: 4, EXPLICIT: 1, INVALIDATION: 1})
    auth, _ = bootstrap.wait_for_flows(api)
    assert auth == f"pk-{IMPLICIT}"


def test_no_authorization_flow_raises_naming_flow(bootstrap, clock):
    api = FakeAPI({IMPLICIT: None, EXPLICIT: None, INVALIDATION: 1})
    with pytest.raises(RuntimeError) as exc:
        bootstrap.wait_for_flows(api, timeout=30, interval=5)
    assert IMPLICIT in str(exc.value)
    assert EXPLICIT in str(exc.value)


def test_main_creates_no_provider_without_authorization_flow(
    bootstrap, clock, monkeypatch
):
    api = FakeAPI({IMPLICIT: None, EXPLICIT: None, INVALIDATION: 1})
    monkeypatch.setattr(bootstrap, "get_container_ip", lambda name: "127.0.0.1")
    monkeypatch.setattr(bootstrap, "wait_for_ready", lambda *a, **k: None)
    monkeypatch.setattr(bootstrap, "ensure_api_token", lambda: "tok")
    monkeypatch.setattr(bootstrap, "AuthentikAPI", lambda base_url, token: api)
    # Shorten the wait limit used by main's call.
    orig = bootstrap.wait_for_flows
    monkeypatch.setattr(
        bootstrap, "wait_for_flows", lambda a: orig(a, timeout=30, interval=5)
    )
    with pytest.raises(RuntimeError, match=IMPLICIT):
        bootstrap.main()
    assert api.posts == []
    # Only flow lookups reached the API; no /providers/ request of any kind.
    assert set(api.calls) <= {IMPLICIT, EXPLICIT, INVALIDATION}


def test_missing_invalidation_flow_raises(bootstrap, clock):
    api = FakeAPI({IMPLICIT: 1, INVALIDATION: None})
    with pytest.raises(RuntimeError) as exc:
        bootstrap.wait_for_flows(api, timeout=30, interval=5)
    assert INVALIDATION in str(exc.value)


def test_all_present_means_one_lookup_each_and_no_sleep(bootstrap, clock):
    api = FakeAPI({IMPLICIT: 1, EXPLICIT: 1, INVALIDATION: 1})
    auth, inval = bootstrap.wait_for_flows(api)
    assert (auth, inval) == (f"pk-{IMPLICIT}", f"pk-{INVALIDATION}")
    assert api.calls == {IMPLICIT: 1, INVALIDATION: 1}
    assert clock.sleeps == []
