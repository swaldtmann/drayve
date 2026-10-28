"""Regression test: the bouncer-watchdog systemd unit templates must keep
`Description=` and `Documentation=` on separate lines.

The timer template once had both glued together on one line
(`Description=... TimerDocumentation=...`), which systemd reads as a single
Description and silently drops the Documentation directive.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "ansible" / "roles" / "traefik" / "templates"

UNIT_TEMPLATES = ["bouncer-watchdog.service.j2", "bouncer-watchdog.timer.j2"]


@pytest.mark.parametrize("name", UNIT_TEMPLATES)
def test_description_and_documentation_on_separate_lines(name: str) -> None:
    lines = (TEMPLATES / name).read_text(encoding="utf-8").splitlines()

    desc = [ln for ln in lines if ln.startswith("Description=")]
    doc = [ln for ln in lines if ln.startswith("Documentation=")]

    assert len(desc) == 1, f"{name}: expected exactly one Description= line"
    assert len(doc) == 1, f"{name}: expected a Documentation= line at line start"
    assert "Documentation=" not in desc[0], (
        f"{name}: Documentation= is glued onto the Description= line"
    )


def test_service_unit_sets_resolve_ip_from_role_variable() -> None:
    text = (TEMPLATES / "bouncer-watchdog.service.j2").read_text(encoding="utf-8")
    assert "Environment=RESOLVE_IP={{ bouncer_watchdog_resolve_ip }}" in text


def test_defaults_pin_probe_to_loopback() -> None:
    text = (TEMPLATES.parent / "defaults" / "main.yml").read_text(encoding="utf-8")
    assert 'bouncer_watchdog_resolve_ip: "127.0.0.1"' in text


def _rendered_unit_url(**ctx: object) -> str:
    """Render the real defaults + service unit the way the traefik role sees them.

    The traefik role runs BEFORE the deploy role (playbooks/deploy.yml), so the
    deploy role's `landing_domain` fact does not exist yet. Only group_vars
    (`landing_domain: ""`) and stack.yaml (`services.landing.domain`) are known.
    """
    import jinja2
    import yaml

    defaults = yaml.safe_load(
        (TEMPLATES.parent / "defaults" / "main.yml").read_text(encoding="utf-8")
    )
    jenv = jinja2.Environment(undefined=jinja2.StrictUndefined)
    context = {"drayve_domain": "example.org", **ctx}
    context["bouncer_watchdog_check_url"] = jenv.from_string(
        defaults["bouncer_watchdog_check_url"]
    ).render(context)
    context.update(
        bouncer_watchdog_resolve_ip="127.0.0.1",
        bouncer_watchdog_threshold_seconds=60,
        bouncer_watchdog_curl_timeout=10,
        drayve_deploy_dir="/opt/drayve/deploy/stack",
    )
    unit = jenv.from_string(
        (TEMPLATES / "bouncer-watchdog.service.j2").read_text(encoding="utf-8")
    ).render(context)
    lines = [ln for ln in unit.splitlines() if ln.startswith("Environment=URL=")]
    assert len(lines) == 1
    return lines[0].removeprefix("Environment=URL=")


def test_probe_url_uses_stack_landing_domain() -> None:
    url = _rendered_unit_url(
        landing_domain="", services={"landing": {"domain": "dash.example.org"}}
    )
    assert url == "https://dash.example.org/"


def test_probe_url_uses_landing_domain_var() -> None:
    assert _rendered_unit_url(landing_domain="dash.example.org") == (
        "https://dash.example.org/"
    )


def test_probe_url_falls_back_to_drayve_domain() -> None:
    assert _rendered_unit_url() == "https://example.org/"
    assert _rendered_unit_url(landing_domain="", services={}) == "https://example.org/"
