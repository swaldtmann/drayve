"""Tests for the 14-day log retention (traefik logrotate + journald).

Renders ``ansible/roles/traefik/templates/logrotate-traefik.j2`` and checks
the role defaults / task wiring. Backup exclusion is covered in
``test_kedge_template.py``.

Run: ``pytest tests/python/test_log_retention.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAEFIK_ROLE = REPO_ROOT / "ansible" / "roles" / "traefik"
COMMON_ROLE = REPO_ROOT / "ansible" / "roles" / "common"
COMPOSE_TEMPLATE = REPO_ROOT / "ansible" / "templates" / "docker-compose.yml.j2"


def _traefik_defaults() -> dict:
    return yaml.safe_load((TRAEFIK_ROLE / "defaults" / "main.yml").read_text())


@pytest.fixture
def render(ansible_jinja_env):
    env = ansible_jinja_env(TRAEFIK_ROLE / "templates", strict=True)
    template = env.get_template("logrotate-traefik.j2")

    def _render(**overrides: object) -> str:
        ctx = {
            "ansible_managed": "Ansible managed",
            "traefik_access_log_retention_days": 14,
            "traefik_access_log_path": "/var/lib/docker/volumes/drayve_traefik_logs/_data/access.log",
            "traefik_container_name": "traefik",
        }
        ctx.update(overrides)
        return template.render(**ctx)

    return _render


def test_default_rule_has_rotate_13_and_maxage_14(render):
    out = render()
    assert "    rotate 13\n" in out
    assert "    maxage 14\n" in out
    for directive in ("daily", "missingok", "notifempty", "compress",
                      "delaycompress", "sharedscripts"):
        assert f"    {directive}\n" in out


def test_postrotate_signals_traefik_container(render):
    out = render()
    assert 'docker kill --signal="USR1" traefik' in out
    assert "postrotate" in out and "endscript" in out


@pytest.mark.parametrize("days,rotate", [(14, 13), (7, 6), (30, 29)])
def test_rotate_is_retention_minus_one(render, days, rotate):
    out = render(traefik_access_log_retention_days=days)
    assert f"    rotate {rotate}\n" in out
    assert f"    maxage {days}\n" in out


def test_rule_block_targets_configured_path(render):
    out = render(traefik_access_log_path="/data/docker/volumes/x/_data/access.log")
    assert "\n/data/docker/volumes/x/_data/access.log {\n" in out


def test_defaults_retention_is_14():
    assert _traefik_defaults()["traefik_access_log_retention_days"] == 14


def test_default_path_follows_compose_volume_and_data_root():
    """Path default must name the compose project + volume actually used in
    docker-compose.yml.j2 and honour docker_daemon_extra['data-root']."""
    compose = COMPOSE_TEMPLATE.read_text()
    assert "\nname: drayve\n" in compose
    assert "- traefik_logs:/var/log/traefik\n" in compose
    assert "container_name: traefik\n" in compose
    path = _traefik_defaults()["traefik_access_log_path"]
    assert "drayve_traefik_logs/_data/access.log" in path
    assert "docker_daemon_extra" in path and "'data-root'" in path
    assert "/var/lib/docker" in path


def test_default_path_renders_with_and_without_data_root(ansible_jinja_env):
    env = ansible_jinja_env(TRAEFIK_ROLE / "templates", strict=True)
    expr = _traefik_defaults()["traefik_access_log_path"]
    tpl = env.from_string(expr)
    assert tpl.render(docker_daemon_extra={}) == (
        "/var/lib/docker/volumes/drayve_traefik_logs/_data/access.log")
    assert tpl.render(docker_daemon_extra={"data-root": "/data/containers/docker"}) == (
        "/data/containers/docker/volumes/drayve_traefik_logs/_data/access.log")


def test_traefik_task_deploys_logrotate_rule():
    tasks = yaml.safe_load((TRAEFIK_ROLE / "tasks" / "main.yml").read_text())
    hits = [t for t in tasks
            if t.get("ansible.builtin.template", {}).get("dest")
            == "/etc/logrotate.d/drayve-traefik"]
    assert len(hits) == 1
    assert hits[0]["ansible.builtin.template"]["src"] == "logrotate-traefik.j2"
    assert "when" not in hits[0]  # unconditional: independent of crowdsec


def test_common_journald_max_retention_default_and_task():
    defaults = yaml.safe_load((COMMON_ROLE / "defaults" / "main.yml").read_text())
    assert defaults["journald_max_retention_sec"] == "14day"
    tasks = yaml.safe_load((COMMON_ROLE / "tasks" / "main.yml").read_text())
    hits = [t for t in tasks if t.get("name") == "Set journald MaxRetentionSec"]
    assert len(hits) == 1
    lif = hits[0]["ansible.builtin.lineinfile"]
    assert lif["path"] == "/etc/systemd/journald.conf"
    assert lif["line"] == "MaxRetentionSec={{ journald_max_retention_sec }}"
    assert hits[0]["notify"] == "Restart systemd-journald"


def test_common_journald_max_file_sec_default_and_task():
    defaults = yaml.safe_load((COMMON_ROLE / "defaults" / "main.yml").read_text())
    assert defaults["journald_max_file_sec"] == "1day"
    tasks = yaml.safe_load((COMMON_ROLE / "tasks" / "main.yml").read_text())
    hits = [t for t in tasks if t.get("name") == "Set journald MaxFileSec"]
    assert len(hits) == 1
    lif = hits[0]["ansible.builtin.lineinfile"]
    assert lif["path"] == "/etc/systemd/journald.conf"
    assert lif["line"] == "MaxFileSec={{ journald_max_file_sec }}"
    assert hits[0]["notify"] == "Restart systemd-journald"
