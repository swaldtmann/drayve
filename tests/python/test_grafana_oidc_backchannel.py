"""Tests for Grafana's OIDC back channel to Authelia (token + userinfo).

Authelia >= 4.39 derives the effective issuer from the incoming request and
rejects plain HTTP without ``X-Forwarded-Proto: https`` (``invalid
X-Forwarded-Proto header value 'http'``). Grafana therefore must not call
``http://authelia:9091`` for the token exchange; it goes through Traefik under
the public Authelia name (``auth.portal_url``, default
``https://auth.<drayve_domain>``). To keep that traffic on the Docker network,
Traefik carries the portal hostname as a network alias in the shared (default)
compose network.

Covers:
  - AUTH_URL / TOKEN_URL / API_URL / signout URL all share one portal base
  - no ``http://authelia:9091`` left in the Grafana service
  - ``auth.portal_url`` overrides the base everywhere
  - Traefik alias with the portal hostname, Grafana in the same network
  - TLS_SKIP_VERIFY_INSECURE only with ``grafana_oauth_tls_skip_verify``
  - other providers render unchanged
  - rendered compose stays valid YAML

Run: ``pytest tests/python/test_grafana_oidc_backchannel.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_DIR = REPO_ROOT / "ansible" / "templates"

DOMAIN = "mon.example.org"
DEFAULT_BASE = f"https://auth.{DOMAIN}"
CUSTOM_BASE = "https://auth.node01.stream.example.org"
CUSTOM_HOST = "auth.node01.stream.example.org"

OAUTH = "GF_AUTH_GENERIC_OAUTH_"
SKIP_VERIFY = OAUTH + "TLS_SKIP_VERIFY_INSECURE"


@pytest.fixture
def render(ansible_jinja_env):
    env = ansible_jinja_env(COMPOSE_DIR)
    template = env.get_template("docker-compose.yml.j2")

    def _render(**overrides: object) -> dict:
        ctx = {
            "crowdsec_enabled": False,
            "auth_provider": "authelia",
            "security": {},
            "monitoring_services": {"grafana": True},
            "drayve_domain": DOMAIN,
            "mem_traefik": "128m",
            "mem_grafana": "256m",
            "mem_authelia": "128m",
            "grafana_image": "grafana/grafana:12.3.6",
            "traefik_certresolver": "letsencrypt",
        }
        ctx.update(overrides)
        return yaml.safe_load(template.render(**ctx))

    return _render


def _env(service: dict) -> dict[str, str]:
    out = {}
    for item in service["environment"]:
        key, _, value = item.partition("=")
        out[key] = value
    return out


def _grafana_env(compose: dict) -> dict[str, str]:
    return _env(compose["services"]["grafana"])


# ---- (a) no internal HTTP back channel ------------------------------------


def test_authelia_back_channel_goes_through_portal_base(render):
    env = _grafana_env(render())
    assert env[OAUTH + "AUTH_URL"] == f"{DEFAULT_BASE}/api/oidc/authorization"
    assert env[OAUTH + "TOKEN_URL"] == f"{DEFAULT_BASE}/api/oidc/token"
    assert env[OAUTH + "API_URL"] == f"{DEFAULT_BASE}/api/oidc/userinfo"
    assert env["GF_AUTH_SIGNOUT_REDIRECT_URL"] == f"{DEFAULT_BASE}/logout"


def test_no_internal_authelia_http_in_grafana_service(render):
    compose = render()
    blob = yaml.safe_dump(compose["services"]["grafana"])
    assert "http://authelia:9091" not in blob
    assert "http://" not in _grafana_env(compose)[OAUTH + "TOKEN_URL"]


# ---- (b) auth.portal_url drives all four addresses ------------------------


@pytest.mark.parametrize("portal", [CUSTOM_BASE, CUSTOM_BASE + "/"])
def test_portal_url_overrides_all_addresses(render, portal):
    env = _grafana_env(render(auth={"portal_url": portal}))
    assert env[OAUTH + "AUTH_URL"] == f"{CUSTOM_BASE}/api/oidc/authorization"
    assert env[OAUTH + "TOKEN_URL"] == f"{CUSTOM_BASE}/api/oidc/token"
    assert env[OAUTH + "API_URL"] == f"{CUSTOM_BASE}/api/oidc/userinfo"
    assert env["GF_AUTH_SIGNOUT_REDIRECT_URL"] == f"{CUSTOM_BASE}/logout"


def test_empty_portal_url_falls_back_to_default(render):
    env = _grafana_env(render(auth={"portal_url": ""}))
    assert env[OAUTH + "TOKEN_URL"] == f"{DEFAULT_BASE}/api/oidc/token"


# ---- (c) Traefik alias in the shared network ------------------------------


def _aliases(service: dict, network: str = "default") -> list[str]:
    return service.get("networks", {}).get(network, {}).get("aliases", [])


def test_traefik_carries_default_portal_host_alias(render):
    compose = render()
    assert f"auth.{DOMAIN}" in _aliases(compose["services"]["traefik"])


def test_traefik_alias_follows_portal_url_host(render):
    compose = render(auth={"portal_url": CUSTOM_BASE})
    aliases = _aliases(compose["services"]["traefik"])
    assert aliases == [CUSTOM_HOST]


def test_grafana_and_traefik_share_the_default_network(render):
    compose = render()
    # No custom top-level networks: every service without a `networks:` key
    # sits in the project's default network, which is where the alias lives.
    assert "networks" not in compose
    grafana = compose["services"]["grafana"]
    assert "networks" not in grafana or "default" in grafana["networks"]
    assert "default" in compose["services"]["traefik"]["networks"]


def test_no_alias_when_grafana_disabled(render):
    compose = render(monitoring_services={"grafana": False})
    assert "networks" not in compose["services"]["traefik"]


# ---- (d) TLS skip verify only on request ----------------------------------


def test_tls_skip_verify_absent_by_default(render):
    assert SKIP_VERIFY not in _grafana_env(render())


def test_tls_skip_verify_absent_when_false(render):
    assert SKIP_VERIFY not in _grafana_env(render(grafana_oauth_tls_skip_verify=False))


@pytest.mark.parametrize("value", [True, "true"])
def test_tls_skip_verify_set_on_request(render, value):
    env = _grafana_env(render(grafana_oauth_tls_skip_verify=value))
    assert env[SKIP_VERIFY] == "true"


# ---- (e) other providers unchanged ----------------------------------------


def test_authentik_block_unchanged(render):
    compose = render(
        auth_provider="authentik",
        mem_authentik_pg="1", mem_authentik_redis="1",
        mem_authentik_server="1", mem_authentik_worker="1",
        grafana_oauth_tls_skip_verify=True,
    )
    env = _grafana_env(compose)
    assert env[OAUTH + "AUTH_URL"] == f"https://auth.{DOMAIN}/application/o/authorize/"
    assert env[OAUTH + "TOKEN_URL"] == "http://authentik-server:9000/application/o/token/"
    assert env[OAUTH + "API_URL"] == "http://authentik-server:9000/application/o/userinfo/"
    assert SKIP_VERIFY not in env
    assert "networks" not in compose["services"]["traefik"]


@pytest.mark.parametrize("provider", ["basic", "none", "authelia_remote"])
def test_other_providers_have_no_oauth_and_no_alias(render, provider):
    compose = render(auth_provider=provider, grafana_oauth_tls_skip_verify=True)
    env = _grafana_env(compose)
    assert not [k for k in env if k.startswith("GF_AUTH_GENERIC_OAUTH_")]
    assert "networks" not in compose["services"]["traefik"]
    assert "networks" not in compose["services"]["grafana"]


# ---- (f) valid YAML -------------------------------------------------------


@pytest.mark.parametrize(
    "ctx",
    [
        {},
        {"auth": {"portal_url": CUSTOM_BASE}},
        {"grafana_oauth_tls_skip_verify": True},
        {"crowdsec_enabled": True, "mem_crowdsec": "1", "auth_lldap": True, "mem_lldap": "1"},
    ],
)
def test_rendered_compose_is_valid_yaml(render, ctx):
    compose = render(**ctx)
    assert set(compose) >= {"services", "volumes"}
    assert "grafana" in compose["services"]
