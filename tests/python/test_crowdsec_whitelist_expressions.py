"""Tests for CrowdSec whitelist expressions and extra hub parsers.

Renders ``ansible/roles/traefik/templates/crowdsec-whitelist.yaml.j2`` and the
crowdsec service of ``ansible/templates/docker-compose.yml.j2``, and checks the
task wiring and the stack schema.

Run: ``pytest tests/python/test_crowdsec_whitelist_expressions.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAEFIK_ROLE = REPO_ROOT / "ansible" / "roles" / "traefik"
COMPOSE_DIR = REPO_ROOT / "ansible" / "templates"
MOUNT = "./crowdsec/whitelist.yaml:/etc/crowdsec/parsers/s02-enrich/whitelist.yaml:ro"

# Output of the template before the expression key existed (IP list only).
GOLDEN_IP_ONLY = """\
name: drayve/whitelist
description: "Whitelisted IPs from stack.yaml"
whitelist:
  reason: "stack.yaml crowdsec_whitelist"
  ip:
    - "127.0.0.1"
    - "203.0.113.1"
"""

EXPR = (
    "evt.Meta.traefik_router_name startsWith 'nextcloud@' "
    "&& evt.Meta.http_verb == 'GET' "
    "&& evt.Meta.http_path startsWith '/ocs/v2.php/apps/user_status/api/v1/statuses/'"
)


@pytest.fixture
def render_whitelist(ansible_jinja_env):
    env = ansible_jinja_env(TRAEFIK_ROLE / "templates", strict=True)
    env.trim_blocks = True  # Ansible's template/lookup default
    template = env.get_template("crowdsec-whitelist.yaml.j2")

    def _render(**security: object) -> str:
        return template.render(security=security)

    return _render


@pytest.fixture
def render_compose(ansible_jinja_env):
    env = ansible_jinja_env(COMPOSE_DIR)
    template = env.get_template("docker-compose.yml.j2")

    def _render(**security: object) -> dict:
        ctx = {
            "crowdsec_enabled": True,
            "auth_provider": "basic",
            "security": security,
            "monitoring_services": {},
            "drayve_domain": "example.test",
        }
        return yaml.safe_load(template.render(**ctx))

    return _render


def test_ips_only_output_unchanged(render_whitelist):
    ips = ["127.0.0.1", "203.0.113.1"]
    assert render_whitelist(crowdsec_whitelist=ips) == GOLDEN_IP_ONLY
    assert (
        render_whitelist(crowdsec_whitelist=ips, crowdsec_whitelist_expressions=[])
        == GOLDEN_IP_ONLY
    )


def test_expression_rendered_as_valid_yaml(render_whitelist):
    out = render_whitelist(
        crowdsec_whitelist=["127.0.0.1"], crowdsec_whitelist_expressions=[EXPR]
    )
    doc = yaml.safe_load(out)
    assert doc["whitelist"]["ip"] == ["127.0.0.1"]
    assert doc["whitelist"]["expression"] == [EXPR]


def test_expression_without_ips_has_no_empty_ip_key(render_whitelist):
    doc = yaml.safe_load(
        render_whitelist(crowdsec_whitelist=[], crowdsec_whitelist_expressions=[EXPR])
    )
    assert "ip" not in doc["whitelist"]
    assert doc["whitelist"]["expression"] == [EXPR]


def test_expression_with_quotes_and_backslash_roundtrips(render_whitelist):
    tricky = 'evt.Meta.http_path matches "^/a\\\\.b/" && evt.Meta.x == \'y\''
    doc = yaml.safe_load(
        render_whitelist(crowdsec_whitelist=[], crowdsec_whitelist_expressions=[tricky])
    )
    assert doc["whitelist"]["expression"] == [tricky]


def _crowdsec(compose: dict) -> dict:
    return compose["services"]["crowdsec"]


def test_compose_default_has_no_parsers_env(render_compose):
    env = _crowdsec(render_compose())["environment"]
    assert not any(e.startswith("PARSERS=") for e in env)


def test_compose_extra_parsers_env(render_compose):
    env = _crowdsec(
        render_compose(
            crowdsec_parsers=["crowdsecurity/nextcloud-whitelist", "foo/bar"]
        )
    )["environment"]
    assert "PARSERS=crowdsecurity/nextcloud-whitelist foo/bar" in env


def test_compose_mounts_whitelist_for_expressions_only(render_compose):
    vols = _crowdsec(
        render_compose(crowdsec_whitelist_expressions=[EXPR])
    )["volumes"]
    assert MOUNT in vols


def test_compose_no_whitelist_mount_by_default(render_compose):
    assert MOUNT not in _crowdsec(render_compose())["volumes"]


def _tasks() -> list[dict]:
    return yaml.safe_load((TRAEFIK_ROLE / "tasks" / "main.yml").read_text())


def test_deploy_task_runs_for_expressions(tmp_path):
    task = next(t for t in _tasks() if t.get("name") == "Deploy CrowdSec IP whitelist")
    assert "crowdsec_whitelist_expressions" in task["when"]


def test_remove_task_requires_both_lists_empty():
    task = next(
        t for t in _tasks() if t.get("name") == "Remove CrowdSec whitelist when empty"
    )
    assert "crowdsec_whitelist_expressions" in task["when"]


def test_schema_declares_new_keys():
    schema = yaml.safe_load((REPO_ROOT / "config" / "stack.schema.yaml").read_text())
    props = schema["properties"]["security"]["properties"]
    for key in ("crowdsec_whitelist_expressions", "crowdsec_parsers"):
        assert props[key]["type"] == "array"
        assert props[key]["items"] == {"type": "string"}
