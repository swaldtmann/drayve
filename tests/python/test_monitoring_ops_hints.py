"""Tests for the operator hints folded in after v0.8.13 field feedback.

1. Grafana provisioning: the compose file mounts the whole
   ``monitoring/grafana/provisioning`` directory as ``/etc/grafana/provisioning``,
   so Grafana looks for ``plugins/`` in it. A missing directory is logged at
   every start ("Failed to read plugin provisioning files ...").
2. Loki query limits (``max_entries_limit_per_query``, ``max_query_series``)
   are role variables rendered into ``limits_config``; ``mem_loki`` defaults
   to 1g.
3. ``grafana_health_allowlist``: optional extra Traefik router for exactly
   ``/api/health`` that skips ``auth@file`` for the listed sources only.

The real ``tasks/main.yml`` and the real ``docker-compose.yml.j2`` are
rendered, not copies of them.

Run: ``pytest tests/python/test_monitoring_ops_hints.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_FILE = REPO_ROOT / "ansible" / "roles" / "monitoring" / "tasks" / "main.yml"
DEFAULTS_FILE = REPO_ROOT / "ansible" / "roles" / "monitoring" / "defaults" / "main.yml"
VARS_FILE = REPO_ROOT / "ansible" / "inventory" / "group_vars" / "all" / "vars.yml"
COMPOSE_TEMPLATE = REPO_ROOT / "ansible" / "templates" / "docker-compose.yml.j2"


def _task(name: str) -> dict:
    for task in yaml.safe_load(TASKS_FILE.read_text()):
        if task.get("name") == name:
            return task
    raise AssertionError(f"task {name!r} not found in tasks/main.yml")


def _env() -> Environment:
    # trim_blocks like Ansible's own templating engine.
    env = Environment(trim_blocks=True, keep_trailing_newline=True)
    env.filters["bool"] = lambda v: str(v).lower() in {"1", "true", "yes", "on"}
    return env


# ============================================================
# 1. Grafana plugins provisioning directory
# ============================================================

def test_grafana_plugins_provisioning_dir_is_created():
    loop = _task("Create Grafana provisioning directories")["loop"]
    assert any(item.endswith("/monitoring/grafana/provisioning/plugins") for item in loop)


# ============================================================
# 2. Loki limits + memory
# ============================================================

def _loki_config(**overrides) -> dict:
    defaults = yaml.safe_load(DEFAULTS_FILE.read_text())
    ctx = {
        "monitoring_loki_retention_period": "168h",
        "monitoring_loki_max_entries_limit_per_query": defaults.get(
            "monitoring_loki_max_entries_limit_per_query"),
        "monitoring_loki_max_query_series": defaults.get("monitoring_loki_max_query_series"),
    }
    ctx.update(overrides)
    content = _task("Deploy Loki config")["ansible.builtin.copy"]["content"]
    return yaml.safe_load(_env().from_string(content).render(**ctx))


def test_loki_limits_config_has_query_limits():
    limits = _loki_config()["limits_config"]
    assert "max_entries_limit_per_query" in limits
    assert "max_query_series" in limits
    assert limits["retention_period"] == "168h"


def test_loki_limit_defaults_are_upstream_defaults_of_pinned_version():
    # Loki 3.3.2: -validation.max-entries-limit=5000, -querier.max-query-series=500
    # (pkg/validation/limits.go, checked 2026-10-20).
    assert "grafana/loki:3.3.2" in VARS_FILE.read_text()
    limits = _loki_config()["limits_config"]
    assert limits["max_entries_limit_per_query"] == 5000
    assert limits["max_query_series"] == 500


def test_loki_limits_follow_role_variables():
    limits = _loki_config(
        monitoring_loki_max_entries_limit_per_query=1000,
        monitoring_loki_max_query_series=100,
    )["limits_config"]
    assert limits["max_entries_limit_per_query"] == 1000
    assert limits["max_query_series"] == 100


def test_mem_loki_default_is_1g():
    assert yaml.safe_load(VARS_FILE.read_text())["mem_loki"] == "1g"


# ============================================================
# 3. grafana_health_allowlist
# ============================================================

def _grafana_labels(**overrides) -> list[str]:
    text = COMPOSE_TEMPLATE.read_text()
    start = text.index("{% if monitoring_services.grafana")
    end = text.index("{% if monitoring_services.loki")
    ctx = {
        "monitoring_services": {"grafana": True},
        "grafana_image": "grafana/grafana:x",
        "mem_grafana": "256m",
        "drayve_domain": "example.com",
        "auth_provider": "authelia_remote",
        "crowdsec_enabled": False,
        "traefik_certresolver": "letsencrypt",
    }
    ctx.update(overrides)
    out = _env().from_string(text[start:end]).render(**ctx)
    return [ln.strip() for ln in out.splitlines() if "traefik." in ln]


def test_health_allowlist_unset_renders_no_extra_router():
    for labels in (_grafana_labels(), _grafana_labels(grafana_health_allowlist=[])):
        assert not any("grafana-health" in ln for ln in labels)
        assert not any("ipallowlist" in ln for ln in labels)
        assert '- "traefik.http.routers.grafana.middlewares=auth@file"' in labels


def test_health_allowlist_renders_exact_path_router_with_ipallowlist():
    labels = _grafana_labels(grafana_health_allowlist=["203.0.113.7", "198.51.100.0/24"])
    joined = "\n".join(labels)
    assert ("traefik.http.routers.grafana-health.rule="
            "Host(`grafana.example.com`) && Path(`/api/health`)") in joined
    assert "PathPrefix" not in joined
    assert ("traefik.http.middlewares.grafana-health-allowlist.ipallowlist.sourcerange="
            "203.0.113.7,198.51.100.0/24") in joined
    assert "traefik.http.routers.grafana-health.middlewares=grafana-health-allowlist" in joined
    assert "traefik.http.routers.grafana-health.service=grafana" in joined
    # No auth@file on the health router, and it outranks the main router.
    assert "grafana-health.middlewares=auth@file" not in joined
    prio = [ln for ln in labels if "routers.grafana-health.priority=" in ln]
    assert len(prio) == 1 and int(prio[0].split("=")[1].strip('"')) >= 1000
    # Main router stays protected.
    assert '- "traefik.http.routers.grafana.middlewares=auth@file"' in labels


def test_health_allowlist_default_is_empty_list():
    defaults = yaml.safe_load(DEFAULTS_FILE.read_text())
    assert defaults["grafana_health_allowlist"] == []
