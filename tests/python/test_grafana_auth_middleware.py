"""Tests for the Grafana Traefik router's auth@file middleware (EWH-W-131 Folge).

Before this fix, ``docker-compose.yml.j2`` gated ``landing`` and ``dashboard``
(Traefik's own UI) behind ``auth@file`` whenever ``auth_provider != 'none'``, but
never gated ``grafana`` the same way — Grafana relied solely on its own native
login (admin/GF_SECURITY_ADMIN_PASSWORD or OIDC auto-login). Found live on
ewh-lab (EWH-W-132) when `auth: provider: basic` was rolled out: Landing +
Dashboard prompted for BasicAuth, Grafana did not.

Run: ``pytest tests/python/test_grafana_auth_middleware.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_TEMPLATE = REPO_ROOT / "ansible" / "templates" / "docker-compose.yml.j2"

# Isolated copy of the grafana router-labels snippet from docker-compose.yml.j2 —
# same isolation approach as test_traefik_template.py's env-var-loop snippet (the
# full compose template needs a long context dict to render end-to-end).
_GRAFANA_ROUTER_SNIPPET = (
    '      - "traefik.http.routers.grafana.rule=Host(`grafana.{{ drayve_domain }}`)"\n'
    '      - "traefik.http.routers.grafana.entrypoints=websecure"\n'
    '      - "traefik.http.routers.grafana.tls=true"\n'
    '      - "traefik.http.routers.grafana.tls.certresolver=letsencrypt"\n'
    "{% if auth_provider != 'none' %}\n"
    '      - "traefik.http.routers.grafana.middlewares=auth@file"\n'
    "{% endif %}\n"
    '      - "traefik.http.services.grafana.loadbalancer.server.port=3000"\n'
)


def _render(auth_provider: str) -> str:
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    template = env.from_string(_GRAFANA_ROUTER_SNIPPET)
    return template.render(drayve_domain="example.com", auth_provider=auth_provider)


@pytest.mark.parametrize("auth_provider", ["basic", "authelia", "authentik"])
def test_grafana_gets_middleware_when_auth_enabled(auth_provider: str):
    out = _render(auth_provider)
    assert 'traefik.http.routers.grafana.middlewares=auth@file' in out


def test_grafana_has_no_middleware_when_auth_none():
    out = _render("none")
    assert "middlewares=auth@file" not in out
    # Router + service labels still present — only the middleware line is gated.
    assert "traefik.http.routers.grafana.rule=Host(`grafana.example.com`)" in out
    assert "traefik.http.services.grafana.loadbalancer.server.port=3000" in out


def test_compose_template_actually_gates_grafana():
    """Anchor test: the production template was changed, not just this snippet."""
    text = COMPOSE_TEMPLATE.read_text()
    assert (
        '- "traefik.http.routers.grafana.middlewares=auth@file"' in text
    ), "grafana router must reference the auth@file middleware, same as landing/dashboard"


def test_compose_template_keeps_landing_and_dashboard_gated():
    """Regression guard: this fix must not remove the existing landing/dashboard gates."""
    text = COMPOSE_TEMPLATE.read_text()
    assert '- "traefik.http.routers.landing.middlewares=auth@file"' in text
    assert '- "traefik.http.routers.dashboard.middlewares=auth@file"' in text
