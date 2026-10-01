"""Tests for CrowdSec signal sharing (default off) via config.yaml.local.

Renders ``ansible/roles/traefik/templates/crowdsec-config.yaml.local.j2`` and
the crowdsec service of ``ansible/templates/docker-compose.yml.j2``, and
checks the role defaults / task wiring.

Run: ``pytest tests/python/test_crowdsec_signal_sharing.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAEFIK_ROLE = REPO_ROOT / "ansible" / "roles" / "traefik"
COMPOSE_DIR = REPO_ROOT / "ansible" / "templates"
MOUNT = "./crowdsec/config.yaml.local:/etc/crowdsec/config.yaml.local:ro"


def _defaults() -> dict:
    return yaml.safe_load((TRAEFIK_ROLE / "defaults" / "main.yml").read_text())


@pytest.fixture
def render_local(ansible_jinja_env):
    env = ansible_jinja_env(TRAEFIK_ROLE / "templates", strict=True)
    template = env.get_template("crowdsec-config.yaml.local.j2")

    def _render(**overrides: object) -> dict:
        ctx = {"ansible_managed": "Ansible managed", **overrides}
        return template.render(**ctx)

    return _render


@pytest.fixture
def render_compose(ansible_jinja_env):
    env = ansible_jinja_env(COMPOSE_DIR)  # non-strict: the template needs many unrelated vars
    template = env.get_template("docker-compose.yml.j2")

    def _render(**overrides: object) -> dict:
        ctx = {
            "crowdsec_enabled": True,
            "auth_provider": "basic",
            "security": {},
            "monitoring_services": {},
            "drayve_domain": "example.test",
        }
        ctx.update(overrides)
        return yaml.safe_load(template.render(**ctx))

    return _render


def test_default_is_off():
    assert _defaults()["crowdsec_signal_sharing"] is False


def test_rendered_local_config_has_sharing_false_at_documented_path(render_local):
    cfg = yaml.safe_load(render_local(crowdsec_signal_sharing=False))
    assert cfg == {"api": {"server": {"online_client": {"sharing": False}}}}


def test_rendered_local_config_sharing_true_when_enabled(render_local):
    cfg = yaml.safe_load(render_local(crowdsec_signal_sharing=True))
    assert cfg["api"]["server"]["online_client"]["sharing"] is True


def test_local_config_does_not_touch_pull_settings(render_local):
    """Community blocklist pull must stay at CrowdSec's default (on)."""
    text = render_local(crowdsec_signal_sharing=False)
    assert "pull" not in yaml.safe_load(text)["api"]["server"]["online_client"]
    assert "DISABLE_ONLINE_API" not in text


def test_compose_mounts_local_config_read_only_when_crowdsec_enabled(render_compose):
    compose = render_compose(crowdsec_enabled=True)
    assert MOUNT in compose["services"]["crowdsec"]["volumes"]


def test_compose_has_no_crowdsec_when_disabled(render_compose):
    compose = render_compose(crowdsec_enabled=False)
    assert "crowdsec" not in compose["services"]


def test_compose_does_not_set_disable_online_api(render_compose):
    env = render_compose()["services"]["crowdsec"]["environment"]
    assert not any(str(e).startswith("DISABLE_ONLINE_API") for e in env)


def test_traefik_task_deploys_local_config_gated_on_crowdsec():
    tasks = yaml.safe_load((TRAEFIK_ROLE / "tasks" / "main.yml").read_text())
    hits = [t for t in tasks
            if t.get("ansible.builtin.copy", {}).get("dest", "").endswith(
                "/crowdsec/config.yaml.local")]
    assert len(hits) == 1
    assert hits[0]["when"] == "crowdsec_enabled"
    assert hits[0]["register"] == "crowdsec_local_config"


def test_deploy_recreates_crowdsec_on_local_config_change():
    tasks = yaml.safe_load(
        (REPO_ROOT / "ansible" / "roles" / "deploy" / "tasks" / "main.yml").read_text())
    hits = [t for t in tasks
            if "--force-recreate crowdsec"
            in t.get("ansible.builtin.command", {}).get("cmd", "")]
    assert len(hits) == 1
    assert "crowdsec_local_config.changed" in hits[0]["when"]
    assert "crowdsec_whitelist.changed" in hits[0]["when"]
