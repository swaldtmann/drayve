"""Tests for AFKI-W-238 — Prometheus scrape-config vs. monitoring-service gate.

Bug: ``roles/monitoring/tasks/main.yml`` rendered the Prometheus scrape config
as a single ``copy`` task gated only on ``_mon_prometheus``. The embedded
``cadvisor``/``node`` scrape jobs were hard-wired in regardless of whether the
cadvisor container / node_exporter container were themselves enabled
(``_mon_cadvisor`` / ``_mon_node_exporter``, mirroring the correctly-gated
container blocks in ``ansible/templates/docker-compose.yml.j2``). A stack with
``monitoring_cadvisor: false`` (e.g. drayve-prod-genua, cadvisor disabled for
a memcg-OOM loop, S328-F193) got a scrape job for a target that structurally
never exists — a permanent `target-down` alert (firing since 2026-07-18).

Fix under test: the ``content`` block of the "Deploy Prometheus config" task
now wraps the ``node`` and ``cadvisor`` scrape jobs each in their own
``{% if _mon_node_exporter %}`` / ``{% if _mon_cadvisor %}`` guard, matching
the container-gating pattern already used in docker-compose.yml.j2. The
``traefik`` job stays ungated — traefik is deployed unconditionally (no
``monitoring_services.traefik`` flag exists anywhere in the compose
template), so there is no analogous mismatch there.

AFKI-W-240 (Befund 2) adds the same gating for the ``crowdsec`` job: the
CrowdSec container is controlled by ``crowdsec_enabled`` (checked in
``docker-compose.yml.j2``), not by an ``_mon_*`` flag — the scrape job was
still hard-wired in regardless, producing the same class of structurally
permanent ``target-down`` alert when ``crowdsec_enabled: false``. The
``content`` block now also wraps the ``crowdsec`` job in
``{% if crowdsec_enabled %}``.

This test renders the *actual* ``content`` string straight out of
``tasks/main.yml`` (parsed via ``yaml.safe_load``, not duplicated by hand) so
a regression in the real file is caught, not just a copy of it. It builds its
own Jinja2 ``Environment`` with ``trim_blocks=True`` rather than reusing the
shared ``ansible_jinja_env`` fixture from conftest.py, because that fixture
does not set ``trim_blocks`` — Ansible's real templating engine
(``AnsibleEnvironment``) does, and the block-tag placement here depends on it
to avoid stray blank lines. Reproduces exactly the runtime behavior of
``ansible.builtin.copy``'s ``content:`` argument, which is templated through
the same engine as ``ansible.builtin.template``.

Run: ``pytest tests/python/test_monitoring_prometheus_scrape_gate.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_FILE = REPO_ROOT / "ansible" / "roles" / "monitoring" / "tasks" / "main.yml"


def _load_tasks() -> list[dict]:
    return yaml.safe_load(TASKS_FILE.read_text())


def _prometheus_config_content() -> str:
    for task in _load_tasks():
        if task.get("name") == "Deploy Prometheus config":
            return task["ansible.builtin.copy"]["content"]
    raise AssertionError("task 'Deploy Prometheus config' not found in tasks/main.yml")


@pytest.fixture(scope="module")
def render():
    content = _prometheus_config_content()
    env = Environment(trim_blocks=True, lstrip_blocks=False, keep_trailing_newline=True)
    template = env.from_string(content)

    def _render(*, cadvisor: bool, node_exporter: bool, crowdsec: bool = True) -> dict:
        rendered = template.render(
            _mon_cadvisor=cadvisor,
            _mon_node_exporter=node_exporter,
            crowdsec_enabled=crowdsec,
        )
        parsed = yaml.safe_load(rendered)
        assert parsed is not None, f"rendered prometheus.yml is not valid YAML:\n{rendered}"
        return parsed

    return _render


def _job_names(parsed: dict) -> list[str]:
    return [job["job_name"] for job in parsed["scrape_configs"]]


# ---- Task is still correctly gated on _mon_prometheus (unchanged) ----


def test_prometheus_config_task_still_gated_on_mon_prometheus():
    for task in _load_tasks():
        if task.get("name") == "Deploy Prometheus config":
            assert task.get("when") == "_mon_prometheus"
            return
    raise AssertionError("task not found")


# ---- cadvisor job follows _mon_cadvisor ----


def test_cadvisor_job_absent_when_cadvisor_disabled(render):
    """The AFKI-W-238 repro case: genua-style stack with cadvisor off."""
    parsed = render(cadvisor=False, node_exporter=True)
    assert "cadvisor" not in _job_names(parsed)


def test_cadvisor_job_present_when_cadvisor_enabled(render):
    parsed = render(cadvisor=True, node_exporter=True)
    assert "cadvisor" in _job_names(parsed)


# ---- node job follows _mon_node_exporter (same gate class, node_exporter
#      is optionally gated in docker-compose.yml.j2 line ~252) ----


def test_node_job_absent_when_node_exporter_disabled(render):
    parsed = render(cadvisor=True, node_exporter=False)
    assert "node" not in _job_names(parsed)


def test_node_job_present_when_node_exporter_enabled(render):
    parsed = render(cadvisor=True, node_exporter=True)
    assert "node" in _job_names(parsed)


# ---- traefik + prometheus jobs stay unconditional (no monitoring_services
#      flag exists for traefik anywhere in the compose template) ----


@pytest.mark.parametrize("cadvisor", [True, False])
@pytest.mark.parametrize("node_exporter", [True, False])
@pytest.mark.parametrize("crowdsec", [True, False])
def test_unconditional_jobs_always_present(render, cadvisor, node_exporter, crowdsec):
    parsed = render(cadvisor=cadvisor, node_exporter=node_exporter, crowdsec=crowdsec)
    jobs = _job_names(parsed)
    assert "prometheus" in jobs
    assert "traefik" in jobs


# ---- crowdsec job follows crowdsec_enabled (AFKI-W-240, Befund 2) ----


def test_crowdsec_job_absent_when_crowdsec_disabled(render):
    """The AFKI-W-240 repro case: a stack with crowdsec_enabled: false."""
    parsed = render(cadvisor=True, node_exporter=True, crowdsec=False)
    assert "crowdsec" not in _job_names(parsed)


def test_crowdsec_job_present_when_crowdsec_enabled(render):
    parsed = render(cadvisor=True, node_exporter=True, crowdsec=True)
    assert "crowdsec" in _job_names(parsed)


# ---- exact job set per combination (locks in the full contract) ----


@pytest.mark.parametrize(
    ("cadvisor", "node_exporter", "crowdsec", "expected"),
    [
        (True, True, True, {"prometheus", "node", "cadvisor", "traefik", "crowdsec"}),
        (False, True, True, {"prometheus", "node", "traefik", "crowdsec"}),
        (True, False, True, {"prometheus", "cadvisor", "traefik", "crowdsec"}),
        (False, False, True, {"prometheus", "traefik", "crowdsec"}),
        (True, True, False, {"prometheus", "node", "cadvisor", "traefik"}),
        (False, False, False, {"prometheus", "traefik"}),
    ],
)
def test_exact_job_set(render, cadvisor, node_exporter, crowdsec, expected):
    parsed = render(cadvisor=cadvisor, node_exporter=node_exporter, crowdsec=crowdsec)
    assert set(_job_names(parsed)) == expected
