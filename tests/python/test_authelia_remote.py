"""Tests for ``auth.provider: authelia_remote`` and the configurable Authelia cookie.

Two-host setup: host A runs Authelia, host B has no Authelia of its own and
points its Traefik ``auth@file`` forwardAuth middleware at host A.

Covers:
  - Traefik role: forwardAuth middleware address for ``authelia`` (unchanged)
    and ``authelia_remote`` (``auth.remote.forward_auth_url``), stale-file
    cleanup so two files never define the middleware ``auth`` at once.
  - Compose: no authelia/lldap service on a remote-only host, routers still
    get ``auth@file``, landing page links the portal.
  - Authelia ``session.cookies``: ``auth.cookie_domain`` / ``auth.portal_url``,
    byte-identical output without them.
  - Fail-early checks (scripts/validate-stack.py and the Ansible preflight
    tasks file ``ansible/tasks/auth_preflight.yml``).

Run: ``pytest tests/python/test_authelia_remote.py``
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAEFIK_TASKS = REPO_ROOT / "ansible" / "roles" / "traefik" / "tasks" / "main.yml"
COMPOSE_DIR = REPO_ROOT / "ansible" / "templates"
AUTHELIA_TEMPLATE = REPO_ROOT / "config" / "authelia" / "configuration.yml.j2"
PREFLIGHT_TASKS = REPO_ROOT / "ansible" / "tasks" / "auth_preflight.yml"
VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate-stack.py"

LOCAL_ADDRESS = "http://authelia:9091/api/authz/forward-auth"
REMOTE_URL = "https://auth.node01.stream.example.org/api/authz/forward-auth"
PORTAL_URL = "https://auth.node01.stream.example.org"
REMOTE = {"forward_auth_url": REMOTE_URL, "portal_url": PORTAL_URL}


# ---- helpers ---------------------------------------------------------------


def _middleware_for(ansible_jinja_env, auth_provider: str, **ctx: object) -> dict | None:
    """Render the traefik role's auth middleware file(s) for a provider.

    Evaluates each ``auth-*.yml`` copy task's ``when`` and renders its
    ``content``. Returns the parsed middleware dict, ``None`` when no task
    fires, and fails when more than one fires (Traefik would see two
    definitions of ``auth``).
    """
    env = ansible_jinja_env(REPO_ROOT)
    tasks = yaml.safe_load(TRAEFIK_TASKS.read_text())
    context = {"auth_provider": auth_provider, **ctx}
    rendered = []
    for task in tasks:
        copy = task.get("ansible.builtin.copy")
        if not copy or not Path(copy.get("dest", "")).name.startswith("auth-"):
            continue
        if not env.compile_expression(task["when"])(**context):
            continue
        rendered.append(yaml.safe_load(env.from_string(copy["content"]).render(**context)))
    assert len(rendered) <= 1, f"{len(rendered)} auth middleware files rendered for {auth_provider}"
    return rendered[0] if rendered else None


def _stale_files(ansible_jinja_env, auth_provider: str) -> set[str]:
    """File names the traefik role's stale-cleanup task removes for a provider."""
    env = ansible_jinja_env(REPO_ROOT)
    tasks = yaml.safe_load(TRAEFIK_TASKS.read_text())
    task = next(t for t in tasks if t["name"] == "Remove stale auth middleware files")
    check = env.compile_expression(task["when"].replace("\n", " "))
    return {
        item
        for item in task["loop"]
        if check(item=item, auth_provider=auth_provider)
    }


@pytest.fixture
def render_compose(ansible_jinja_env):
    env = ansible_jinja_env(COMPOSE_DIR)
    template = env.get_template("docker-compose.yml.j2")

    def _render(**overrides: object) -> dict:
        ctx = {
            "crowdsec_enabled": False,
            "auth_provider": "authelia_remote",
            "security": {},
            "monitoring_services": {"grafana": True},
            "drayve_domain": "mon.stream.example.org",
            "mem_traefik": "128m",
            "mem_grafana": "256m",
            "grafana_image": "grafana/grafana:12.3.6",
            "traefik_certresolver": "letsencrypt",
        }
        ctx.update(overrides)
        return yaml.safe_load(template.render(**ctx))

    return _render


@pytest.fixture
def render_authelia(ansible_jinja_env):
    env = ansible_jinja_env(AUTHELIA_TEMPLATE.parent)
    template = env.get_template(AUTHELIA_TEMPLATE.name)
    base = {
        "drayve_domain": "node01.stream.example.org",
        "auth_lldap": False,
        "authelia_session_secret": "s",
        "authelia_storage_encryption_key": "k",
        "authelia_jwt_secret": "j",
        "authelia_oidc_hmac_secret": "h",
        "authelia_jwks_pem": "stub\n",
        "authelia_oidc_clients": [],
        "authelia_oidc_clients_default": [],
    }

    def _render(**overrides: object) -> str:
        return template.render(**{**base, **overrides})

    return _render


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_stack", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def _stack(auth: dict, domain: str = "node01.stream.example.org") -> dict:
    return {
        "drayve_version": "0.1.0",
        "stack": {"name": "x", "domain": domain, "acme_email": "ops@example.org"},
        "auth": auth,
    }


def _auth_errors(auth: dict, domain: str = "node01.stream.example.org") -> list[str]:
    errors, _ = validator.validate(_stack(auth, domain))
    return errors


# ---- (a)/(b) traefik forwardAuth middleware ------------------------------------


def test_remote_middleware_uses_configured_forward_auth_url(ansible_jinja_env):
    mw = _middleware_for(ansible_jinja_env, "authelia_remote", auth={"remote": REMOTE})
    assert mw is not None, "no auth middleware file rendered for authelia_remote"
    fa = mw["http"]["middlewares"]["auth"]["forwardAuth"]
    assert fa["address"] == REMOTE_URL
    assert fa["trustForwardHeader"] is True
    assert fa["authResponseHeaders"] == ["Remote-User", "Remote-Groups", "Remote-Email"]


def test_local_authelia_middleware_keeps_container_address(ansible_jinja_env):
    mw = _middleware_for(ansible_jinja_env, "authelia", auth={"provider": "authelia"})
    fa = mw["http"]["middlewares"]["auth"]["forwardAuth"]
    assert fa["address"] == LOCAL_ADDRESS
    assert fa["authResponseHeaders"] == ["Remote-User", "Remote-Groups", "Remote-Email"]


def test_local_authelia_ignores_remote_block(ansible_jinja_env):
    """auth.remote left over in stack.yaml must not redirect a host with its own Authelia."""
    mw = _middleware_for(ansible_jinja_env, "authelia", auth={"remote": REMOTE})
    assert mw["http"]["middlewares"]["auth"]["forwardAuth"]["address"] == LOCAL_ADDRESS


@pytest.mark.parametrize("provider", ["basic", "authentik", "none"])
def test_other_providers_never_render_authelia_forward_auth(ansible_jinja_env, provider):
    mw = _middleware_for(ansible_jinja_env, provider, auth={"remote": REMOTE})
    forward = (mw or {}).get("http", {}).get("middlewares", {}).get("auth", {}).get("forwardAuth", {})
    assert forward.get("address") not in {LOCAL_ADDRESS, REMOTE_URL}


def test_stale_cleanup_keeps_authelia_file_for_remote(ansible_jinja_env):
    stale = _stale_files(ansible_jinja_env, "authelia_remote")
    assert "auth-authelia.yml" not in stale
    assert stale == {"auth-basic.yml", "auth-authentik.yml", "auth-none.yml"}


def test_stale_cleanup_removes_authelia_file_for_other_providers(ansible_jinja_env):
    for provider in ("basic", "authentik", "none"):
        assert "auth-authelia.yml" in _stale_files(ansible_jinja_env, provider)


# ---- (c) compose --------------------------------------------------------------


def test_remote_compose_has_no_authelia_or_lldap_service(render_compose):
    services = render_compose(auth_provider="authelia_remote")["services"]
    assert "authelia" not in services
    assert "lldap" not in services


def test_local_compose_still_has_authelia_service(render_compose):
    services = render_compose(auth_provider="authelia", mem_authelia="256m")["services"]
    assert "authelia" in services


def test_remote_compose_routers_keep_auth_middleware(render_compose):
    services = render_compose(auth_provider="authelia_remote")["services"]
    for name, router in (("traefik", "dashboard"), ("landing", "landing"), ("grafana", "grafana")):
        labels = services[name]["labels"]
        assert f"traefik.http.routers.{router}.middlewares=auth@file" in labels, name


def test_remote_compose_grafana_has_no_authelia_oidc(render_compose):
    env = render_compose(auth_provider="authelia_remote")["services"]["grafana"]["environment"]
    assert not any("GF_AUTH_GENERIC_OAUTH" in e for e in env)


# ---- landing page ---------------------------------------------------------------


@pytest.fixture
def render_landing(ansible_jinja_env):
    env = ansible_jinja_env(COMPOSE_DIR)
    template = env.get_template("landing.html.j2")

    def _render(**overrides: object) -> str:
        ctx = {"drayve_domain": "mon.stream.example.org", "monitoring_services": {}, "apps": []}
        ctx.update(overrides)
        return template.render(**ctx)

    return _render


def test_landing_links_remote_portal(render_landing):
    html = render_landing(auth_provider="authelia_remote", auth={"remote": REMOTE})
    assert f'href="{PORTAL_URL}"' in html
    assert "ldap.mon.stream.example.org" not in html


def test_landing_local_authelia_link_unchanged(render_landing):
    html = render_landing(auth_provider="authelia", auth={"remote": REMOTE})
    assert 'href="https://auth.mon.stream.example.org"' in html
    assert PORTAL_URL not in html


# ---- (d) session.cookies --------------------------------------------------------


def test_cookies_default_to_drayve_domain(render_authelia):
    cookies = yaml.safe_load(render_authelia())["session"]["cookies"]
    assert cookies == [
        {
            "domain": "node01.stream.example.org",
            "authelia_url": "https://auth.node01.stream.example.org",
        }
    ]


def test_cookies_render_cookie_domain_and_portal_url(render_authelia):
    cfg = yaml.safe_load(
        render_authelia(
            auth={
                "cookie_domain": "stream.example.org",
                "portal_url": "https://login.stream.example.org",
            }
        )
    )
    assert cfg["session"]["cookies"] == [
        {"domain": "stream.example.org", "authelia_url": "https://login.stream.example.org"}
    ]


def test_cookies_without_new_keys_render_byte_identical_to_legacy(render_authelia):
    """Empty/absent auth.* keys must reproduce the pre-change cookie block exactly."""
    legacy = (
        "  cookies:\n"
        '    - domain: "node01.stream.example.org"\n'
        '      authelia_url: "https://auth.node01.stream.example.org"\n'
    )
    assert legacy in render_authelia()
    assert legacy in render_authelia(auth={})
    assert legacy in render_authelia(auth={"oidc_clients": []})


def test_cookie_domain_alone_keeps_default_portal(render_authelia):
    cookies = yaml.safe_load(render_authelia(auth={"cookie_domain": "stream.example.org"}))[
        "session"
    ]["cookies"]
    assert cookies[0]["domain"] == "stream.example.org"
    assert cookies[0]["authelia_url"] == "https://auth.node01.stream.example.org"


# ---- (e) fail-early: validate-stack.py ------------------------------------------


def test_validator_accepts_authelia_remote():
    assert _auth_errors({"provider": "authelia_remote", "remote": REMOTE}) == []


@pytest.mark.parametrize("missing", ["forward_auth_url", "portal_url"])
def test_validator_rejects_remote_without_url(missing):
    remote = {k: v for k, v in REMOTE.items() if k != missing}
    errors = _auth_errors({"provider": "authelia_remote", "remote": remote})
    assert any(f"auth.remote.{missing}" in e for e in errors), errors


def test_validator_rejects_remote_without_remote_block():
    errors = _auth_errors({"provider": "authelia_remote"})
    assert any("auth.remote.forward_auth_url" in e for e in errors), errors
    assert any("auth.remote.portal_url" in e for e in errors), errors


@pytest.mark.parametrize(
    "url", ["http://auth.example.org/api/authz/forward-auth", "auth.example.org/x", "ftp://a/b"]
)
def test_validator_rejects_non_https_forward_auth_url(url):
    errors = _auth_errors(
        {"provider": "authelia_remote", "remote": {**REMOTE, "forward_auth_url": url}}
    )
    assert any("forward_auth_url" in e and "https://" in e for e in errors), errors


def test_validator_rejects_lldap_with_remote():
    errors = _auth_errors({"provider": "authelia_remote", "remote": REMOTE, "lldap": True})
    assert any("auth.lldap" in e for e in errors), errors


def test_validator_accepts_portal_under_cookie_domain():
    auth = {
        "provider": "authelia",
        "cookie_domain": "stream.example.org",
        "portal_url": "https://auth.node01.stream.example.org",
    }
    assert _auth_errors(auth) == []
    assert _auth_errors({**auth, "portal_url": "https://stream.example.org"}) == []


@pytest.mark.parametrize(
    "portal",
    [
        "https://auth.other.org",
        "https://evilstream.example.org",  # suffix without label boundary
        "http://auth.stream.example.org",  # Authelia requires https
    ],
)
def test_validator_rejects_portal_outside_cookie_domain(portal):
    errors = _auth_errors(
        {"provider": "authelia", "cookie_domain": "stream.example.org", "portal_url": portal}
    )
    assert any("portal_url" in e for e in errors), errors


def test_validator_checks_default_portal_against_cookie_domain():
    """cookie_domain alone: default portal auth.<stack.domain> must still sit under it."""
    errors = _auth_errors({"provider": "authelia", "cookie_domain": "other.org"})
    assert any("portal_url" in e for e in errors), errors


def test_validator_without_new_keys_is_clean():
    assert _auth_errors({"provider": "authelia"}) == []


# ---- (e) fail-early: Ansible preflight tasks -------------------------------------


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


def _run_preflight(tmp_path: Path, **extra_vars: object) -> subprocess.CompletedProcess[str]:
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            ---
            - name: auth preflight wrapper
              hosts: localhost
              connection: local
              gather_facts: false
              tasks:
                - ansible.builtin.import_tasks: {PREFLIGHT_TASKS}
            """
        )
    )
    cmd = ["ansible-playbook", "-i", "localhost,", str(wrapper), "-e", "drayve_domain=node01.stream.example.org"]
    if extra_vars:
        cmd += ["-e", json.dumps(extra_vars)]
    env = {"ANSIBLE_LOCALHOST_WARNING": "False", "ANSIBLE_INVENTORY_UNPARSED_WARNING": "False"}
    return subprocess.run(
        cmd, capture_output=True, text=True, check=False, env={**os.environ, **env}
    )


def test_preflight_passes_for_valid_remote(ansible_available, tmp_path):
    r = _run_preflight(tmp_path, auth={"provider": "authelia_remote", "remote": REMOTE})
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("missing", ["forward_auth_url", "portal_url"])
def test_preflight_fails_for_remote_without_url(ansible_available, tmp_path, missing):
    remote = {k: v for k, v in REMOTE.items() if k != missing}
    r = _run_preflight(tmp_path, auth={"provider": "authelia_remote", "remote": remote})
    assert r.returncode != 0, r.stdout
    assert f"auth.remote.{missing}" in r.stdout + r.stderr


def test_preflight_fails_for_non_https_forward_auth_url(ansible_available, tmp_path):
    remote = {**REMOTE, "forward_auth_url": "http://auth.example.org/api/authz/forward-auth"}
    r = _run_preflight(tmp_path, auth={"provider": "authelia_remote", "remote": remote})
    assert r.returncode != 0, r.stdout
    assert "https://" in r.stdout + r.stderr


def test_preflight_fails_for_portal_outside_cookie_domain(ansible_available, tmp_path):
    auth = {"provider": "authelia", "cookie_domain": "stream.example.org", "portal_url": "https://auth.other.org"}
    r = _run_preflight(tmp_path, auth=auth)
    assert r.returncode != 0, r.stdout
    assert "portal_url" in r.stdout + r.stderr


def test_preflight_passes_for_authelia_without_new_keys(ansible_available, tmp_path):
    r = _run_preflight(tmp_path, auth={"provider": "authelia"})
    assert r.returncode == 0, r.stdout + r.stderr


def test_preflight_passes_for_authelia_cookie_domain_and_portal(ansible_available, tmp_path):
    auth = {
        "provider": "authelia",
        "cookie_domain": "stream.example.org",
        "portal_url": "https://auth.node01.stream.example.org",
    }
    r = _run_preflight(tmp_path, auth=auth)
    assert r.returncode == 0, r.stdout + r.stderr


def test_preflight_ignores_other_providers(ansible_available, tmp_path):
    r = _run_preflight(tmp_path, auth={"provider": "basic"})
    assert r.returncode == 0, r.stdout + r.stderr
