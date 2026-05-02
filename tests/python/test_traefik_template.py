"""Tests for the traefik static-config Jinja template (W-107).

Renders ``config/traefik/traefik.yml.j2`` with various values for
``acme_dns_provider`` / ``acme_dns_propagation_delay`` and verifies that
the generated YAML is valid and contains the expected DNS-challenge block.
Also covers backward compatibility: when no overrides are given, the
rendered output keeps the previous defaults (``hetzner`` / ``30``).

Run: ``pytest tests/python/test_traefik_template.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAEFIK_TEMPLATE = REPO_ROOT / "config" / "traefik" / "traefik.yml.j2"
COMPOSE_TEMPLATE = REPO_ROOT / "ansible" / "templates" / "docker-compose.yml.j2"


def _render_traefik(**overrides: object) -> str:
    env = Environment(
        loader=FileSystemLoader(TRAEFIK_TEMPLATE.parent),
        keep_trailing_newline=True,
    )
    template = env.get_template(TRAEFIK_TEMPLATE.name)
    ctx = {
        "acme_email": "ops@example.com",
        "crowdsec_enabled": True,
    }
    ctx.update(overrides)
    return template.render(**ctx)


def _parse_yaml(rendered: str) -> dict:
    return yaml.safe_load(rendered)


# ---- 1) Backward compatibility — no overrides ----

def test_backward_compat_renders_hetzner_30():
    """No overrides → defaults match the pre-W-107 hardcoded values."""
    rendered = _render_traefik()
    cfg = _parse_yaml(rendered)
    dns = cfg["certificatesResolvers"]["letsencrypt-dns"]["acme"]["dnsChallenge"]
    assert dns["provider"] == "hetzner"
    assert dns["propagation"]["delayBeforeChecks"] == 30


# ---- 2) Custom DNS provider ----

@pytest.mark.parametrize(
    "provider",
    ["cloudflare", "netcup", "route53", "gcloud", "digitalocean"],
)
def test_dns_provider_override(provider: str):
    rendered = _render_traefik(acme_dns_provider=provider)
    cfg = _parse_yaml(rendered)
    dns = cfg["certificatesResolvers"]["letsencrypt-dns"]["acme"]["dnsChallenge"]
    assert dns["provider"] == provider


# ---- 3) Propagation delay override ----

@pytest.mark.parametrize("delay", [10, 30, 120, 300])
def test_propagation_delay_override(delay: int):
    rendered = _render_traefik(acme_dns_propagation_delay=delay)
    cfg = _parse_yaml(rendered)
    dns = cfg["certificatesResolvers"]["letsencrypt-dns"]["acme"]["dnsChallenge"]
    assert dns["propagation"]["delayBeforeChecks"] == delay


# ---- 4) Both together — netcup needs longer delay ----

def test_netcup_full_override():
    rendered = _render_traefik(
        acme_dns_provider="netcup",
        acme_dns_propagation_delay=300,
    )
    cfg = _parse_yaml(rendered)
    dns = cfg["certificatesResolvers"]["letsencrypt-dns"]["acme"]["dnsChallenge"]
    assert dns["provider"] == "netcup"
    assert dns["propagation"]["delayBeforeChecks"] == 300


# ---- 5) HTTP challenge resolver still present (regression) ----

def test_http_challenge_unchanged():
    rendered = _render_traefik()
    cfg = _parse_yaml(rendered)
    http = cfg["certificatesResolvers"]["letsencrypt"]["acme"]
    assert http["httpChallenge"]["entryPoint"] == "web"
    assert http["email"] == "ops@example.com"


# ---- 6) crowdsec_enabled=False does not affect DNS block ----

def test_crowdsec_disabled_keeps_dns_block():
    rendered = _render_traefik(crowdsec_enabled=False)
    cfg = _parse_yaml(rendered)
    dns = cfg["certificatesResolvers"]["letsencrypt-dns"]["acme"]["dnsChallenge"]
    assert dns["provider"] == "hetzner"
    assert "experimental" not in cfg


# ---- 7) Output is valid YAML always ----

@pytest.mark.parametrize(
    "provider",
    ["hetzner", "cloudflare", "netcup", "route53"],
)
def test_rendered_yaml_is_valid(provider: str):
    rendered = _render_traefik(acme_dns_provider=provider)
    cfg = yaml.safe_load(rendered)
    assert isinstance(cfg, dict)
    assert "certificatesResolvers" in cfg


# ---- 8) Compose template — env-var passthrough ----

def _render_compose_traefik_env(env_vars: list[str]) -> str:
    """Render only the traefik service env-var block from docker-compose.yml.j2.

    The full compose template requires a long context dict; here we copy the
    env-var loop into a small standalone template to test the rendering
    contract in isolation.
    """
    env = Environment(undefined=StrictUndefined)
    snippet = (
        "{% for var in acme_dns_env_vars | default(['HETZNER_API_TOKEN']) %}"
        "      - {{ var }}=${{ '{' }}{{ var }}:-}\n"
        "{% endfor %}"
    )
    template = env.from_string(snippet)
    return template.render(acme_dns_env_vars=env_vars)


def test_compose_env_default_hetzner():
    """No override → only HETZNER_API_TOKEN is forwarded."""
    out = _render_compose_traefik_env(["HETZNER_API_TOKEN"])
    assert "HETZNER_API_TOKEN=${HETZNER_API_TOKEN:-}" in out
    assert out.count("- ") == 1


def test_compose_env_cloudflare():
    out = _render_compose_traefik_env(["CF_DNS_API_TOKEN"])
    assert "CF_DNS_API_TOKEN=${CF_DNS_API_TOKEN:-}" in out
    assert "HETZNER" not in out


def test_compose_env_netcup_three_vars():
    out = _render_compose_traefik_env([
        "NETCUP_CUSTOMER_NUMBER",
        "NETCUP_API_KEY",
        "NETCUP_API_PASSWORD",
    ])
    assert "NETCUP_CUSTOMER_NUMBER=${NETCUP_CUSTOMER_NUMBER:-}" in out
    assert "NETCUP_API_KEY=${NETCUP_API_KEY:-}" in out
    assert "NETCUP_API_PASSWORD=${NETCUP_API_PASSWORD:-}" in out
    assert out.count("- ") == 3


def test_compose_env_empty_list_renders_nothing():
    """A user can opt out of env passthrough entirely (e.g. http-challenge only)."""
    out = _render_compose_traefik_env([])
    assert out.strip() == ""


# ---- 9) Live compose template references the loop ----

def test_compose_template_uses_acme_dns_env_vars_loop():
    """Ensure the production template was actually updated, not just docs."""
    text = COMPOSE_TEMPLATE.read_text()
    assert "for var in acme_dns_env_vars" in text
    # No more hardcoded HETZNER passthrough outside the loop.
    hardcoded_count = text.count("- HETZNER_API_TOKEN=${HETZNER_API_TOKEN:-}")
    assert hardcoded_count == 0


# ---- 10) Live traefik template references the variable ----

def test_traefik_template_references_provider_var():
    text = TRAEFIK_TEMPLATE.read_text()
    assert "{{ acme_dns_provider" in text
    assert "{{ acme_dns_propagation_delay" in text
    # No more hardcoded provider/delay.
    assert "provider: hetzner\n" not in text
    assert "delayBeforeChecks: 30\n" not in text
