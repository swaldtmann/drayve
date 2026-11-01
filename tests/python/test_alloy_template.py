"""Tests for the Grafana Alloy log collector (replaces the Promtail tests).

Covers three things:

1. ``ansible/roles/monitoring/templates/alloy-config.alloy.j2`` — Docker log
   source with the ``container`` + ``service`` labels (same label names the
   Grafana queries have always used), optional ``host`` external label,
   optional Basic-Auth (password from a file, never inline) and the extra
   log files from ``monitoring_extra_log_files`` (label ``job``).
2. ``ansible/templates/docker-compose.yml.j2`` — the ``alloy`` service block
   (named volume for the positions/WAL state, optional password mount).
3. ``ansible/roles/monitoring/tasks/main.yml`` — the migration assert that
   stops a deploy when the old ``monitoring_promtail_extra_scrape_configs``
   key is still set.

Run: ``pytest tests/python/test_alloy_template.py``
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "ansible" / "roles" / "monitoring" / "templates"
COMPOSE_TEMPLATE = REPO_ROOT / "ansible" / "templates" / "docker-compose.yml.j2"
MONITORING_TASKS = REPO_ROOT / "ansible" / "roles" / "monitoring" / "tasks" / "main.yml"
MONITORING_DEFAULTS = REPO_ROOT / "ansible" / "roles" / "monitoring" / "defaults" / "main.yml"
SCHEMA = REPO_ROOT / "config" / "stack.schema.yaml"


def _base_ctx(**overrides: object) -> dict[str, object]:
    ctx: dict[str, object] = {
        "_alloy_loki_url": "http://loki:3100/loki/api/v1/push",
        "_alloy_loki_basic_auth_user": "",
        "_alloy_host_label": "",
        "monitoring_extra_log_files": [],
    }
    ctx.update(overrides)
    return ctx


@pytest.fixture
def env(ansible_jinja_env):
    e = ansible_jinja_env(TEMPLATE_DIR, strict=True)
    # Mirror Ansible's real template-module default (trim_blocks=True).
    e.trim_blocks = True
    return e


@pytest.fixture
def render_config(env):
    template = env.get_template("alloy-config.alloy.j2")

    def _render(**overrides: object) -> str:
        return template.render(**_base_ctx(**overrides))

    return _render


@pytest.fixture
def compose_env(ansible_jinja_env):
    e = ansible_jinja_env(TEMPLATE_DIR, strict=False)
    e.trim_blocks = True
    return e


# ---- 1) Docker source: labels container + service -------------------------


def test_config_has_docker_discovery_over_the_socket(render_config):
    rendered = render_config()
    assert 'discovery.docker "containers"' in rendered
    assert 'host             = "unix:///var/run/docker.sock"' in rendered
    assert 'refresh_interval = "5s"' in rendered
    assert 'loki.source.docker "containers"' in rendered


@pytest.mark.parametrize("label", ["container", "service"])
def test_config_relabels_container_name_into_label(render_config, label):
    rendered = render_config()
    pattern = re.compile(
        r'rule \{\s*source_labels = \["__meta_docker_container_name"\]\s*'
        r'regex\s*=\s*"/\(\.\*\)"\s*target_label\s*=\s*"' + label + r'"\s*\}'
    )
    assert pattern.search(rendered), f"relabel rule for {label!r} missing:\n{rendered}"


def test_config_writes_to_the_configured_loki_url(render_config):
    rendered = render_config(_alloy_loki_url="https://loki.example.org/loki/api/v1/push")
    assert 'url = "https://loki.example.org/loki/api/v1/push"' in rendered


# ---- 2) host label optional ------------------------------------------------


def test_config_with_host_label_sets_external_labels(render_config):
    rendered = render_config(_alloy_host_label="buckbeak")
    assert "external_labels" in rendered
    assert 'host = "buckbeak"' in rendered


def test_config_without_host_label_has_no_external_labels(render_config):
    assert "external_labels" not in render_config(_alloy_host_label="")


# ---- 3) basic_auth optional ------------------------------------------------


def test_config_with_basic_auth_user_has_username_and_password_file(render_config):
    rendered = render_config(_alloy_loki_basic_auth_user="buckbeak")
    assert "basic_auth {" in rendered
    assert 'username      = "buckbeak"' in rendered
    assert 'password_file = "/etc/alloy/loki-basic-auth-password"' in rendered


def test_config_never_contains_a_plaintext_password_field(render_config):
    rendered = render_config(_alloy_loki_basic_auth_user="buckbeak")
    assert not re.search(r"^\s*password\s*=", rendered, re.M)


def test_config_without_basic_auth_user_has_no_basic_auth_block(render_config):
    assert "basic_auth" not in render_config(_alloy_loki_basic_auth_user="")


# ---- 4) extra log files with job label ------------------------------------


def test_config_without_extra_log_files_has_no_file_source(render_config):
    rendered = render_config(monitoring_extra_log_files=[])
    assert "loki.source.file" not in rendered
    assert "local.file_match" not in rendered


def test_config_extra_log_file_carries_job_label_and_path(render_config):
    rendered = render_config(
        monitoring_extra_log_files=[{"job": "kedge", "path": "/var/log/kedge.log"}]
    )
    assert 'local.file_match "extra_1"' in rendered
    assert '"__path__" = "/var/log/kedge.log"' in rendered
    assert '"job" = "kedge"' in rendered
    assert 'loki.source.file "extra_1"' in rendered
    assert "local.file_match.extra_1.targets" in rendered


def test_config_multiple_extra_log_files_get_distinct_components(render_config):
    rendered = render_config(
        monitoring_extra_log_files=[
            {"job": "kedge", "path": "/var/log/kedge.log"},
            {"job": "cron-x", "path": "/var/log/cron-x.log"},
        ]
    )
    assert 'local.file_match "extra_1"' in rendered
    assert 'local.file_match "extra_2"' in rendered
    assert '"job" = "cron-x"' in rendered


def test_config_quotes_in_job_are_escaped(render_config):
    rendered = render_config(
        monitoring_extra_log_files=[{"job": 'a"b', "path": "/var/log/x.log"}]
    )
    assert '"job" = "a\\"b"' in rendered


# ---- 5) compose service block ---------------------------------------------

_JINJA_IF_TAG = re.compile(r"{%-?\s*if\b|{%-?\s*endif\s*-?%}")


def _compose_alloy_snippet() -> str:
    """The alloy service's ``{% if %}...{% endif %}`` block (nesting-aware)."""
    text = COMPOSE_TEMPLATE.read_text()
    start = text.index("{% if monitoring_services.alloy")
    depth = 0
    pos = start
    for m in _JINJA_IF_TAG.finditer(text, start):
        if "endif" in m.group():
            depth -= 1
            if depth == 0:
                pos = m.end()
                break
        else:
            depth += 1
    end = text.index("\n", pos) + 1
    return text[start:end]


def _render_alloy_service(env: Environment, **ctx: object) -> dict:
    base = {
        "monitoring_services": {"alloy": True},
        "alloy_image": "grafana/alloy:v1.21.3",
        "mem_alloy": "128m",
        "_deploy_alloy_basic_auth_user": "",
    }
    base.update(ctx)
    text = "services:\n" + env.from_string(_compose_alloy_snippet()).render(**base)
    return yaml.safe_load(text)["services"]["alloy"]


def test_compose_alloy_service_basics(compose_env):
    svc = _render_alloy_service(compose_env)
    assert svc["image"] == "grafana/alloy:v1.21.3"
    assert svc["container_name"] == "alloy"
    assert svc["mem_limit"] == "128m"
    assert "--storage.path=/var/lib/alloy/data" in svc["command"]
    assert "/etc/alloy/config.alloy" in svc["command"]


def test_compose_alloy_mounts_config_logs_socket_and_state_volume(compose_env):
    vols = _render_alloy_service(compose_env)["volumes"]
    assert "./monitoring/alloy/config.alloy:/etc/alloy/config.alloy:ro" in vols
    assert "/var/log:/var/log:ro" in vols
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in vols
    assert "alloy_data:/var/lib/alloy/data" in vols


def test_compose_alloy_with_basic_auth_user_mounts_password_file(compose_env):
    vols = _render_alloy_service(compose_env, _deploy_alloy_basic_auth_user="buckbeak")["volumes"]
    assert (
        "./monitoring/alloy/loki-basic-auth-password:/etc/alloy/loki-basic-auth-password:ro" in vols
    )


def test_compose_alloy_without_basic_auth_user_has_no_password_mount(compose_env):
    vols = _render_alloy_service(compose_env)["volumes"]
    assert not any("loki-basic-auth-password" in v for v in vols)


def test_compose_alloy_undefined_user_has_no_password_mount(compose_env):
    base = {"monitoring_services": {"alloy": True}, "alloy_image": "x", "mem_alloy": "1m"}
    text = "services:\n" + compose_env.from_string(_compose_alloy_snippet()).render(**base)
    vols = yaml.safe_load(text)["services"]["alloy"]["volumes"]
    assert not any("loki-basic-auth-password" in v for v in vols)


def test_compose_declares_alloy_data_volume_only_when_alloy_enabled(compose_env):
    text = COMPOSE_TEMPLATE.read_text()
    assert "{% if monitoring_services.alloy | default(false) | bool %}\n  alloy_data: {}" in text


def test_compose_has_no_promtail_service():
    assert "container_name: promtail" not in COMPOSE_TEMPLATE.read_text()


# ---- 6) migration assert on the old key -----------------------------------


def _migration_assert_task() -> dict:
    tasks = yaml.safe_load(MONITORING_TASKS.read_text())
    hits = [
        t
        for t in tasks
        if "ansible.builtin.assert" in t
        and "monitoring_promtail_extra_scrape_configs" in str(t["ansible.builtin.assert"]["that"])
    ]
    assert len(hits) == 1, "expected exactly one migration assert on the old key"
    return hits[0]


@pytest.mark.parametrize(
    "ctx, passes",
    [
        ({}, True),
        ({"monitoring_promtail_extra_scrape_configs": []}, False),
        ({"monitoring_promtail_extra_scrape_configs": [{"job_name": "kedge"}]}, False),
    ],
)
def test_old_extra_scrape_key_fails_the_deploy(compose_env, ctx, passes):
    task = _migration_assert_task()
    conditions = task["ansible.builtin.assert"]["that"]
    results = [compose_env.compile_expression(c)(**ctx) for c in conditions]
    assert all(results) is passes


def test_migration_assert_message_points_to_the_new_key():
    task = _migration_assert_task()
    msg = task["ansible.builtin.assert"]["fail_msg"]
    assert "monitoring_extra_log_files" in msg
    assert "job" in msg and "path" in msg


def test_migration_assert_runs_without_monitoring_tags_skipping_it():
    assert "monitoring" in _migration_assert_task()["tags"]


# ---- 7) defaults + schema --------------------------------------------------


def test_defaults_declare_new_key_and_drop_the_old_one():
    text = MONITORING_DEFAULTS.read_text()
    assert re.search(r"^monitoring_extra_log_files:\s*\[\]\s*$", text, re.M)
    assert not re.search(r"^monitoring_promtail_extra_scrape_configs:", text, re.M)


def test_schema_documents_extra_log_files():
    schema = yaml.safe_load(SCHEMA.read_text())
    prop = schema["properties"]["monitoring_extra_log_files"]
    assert prop["type"] == "array"
    assert set(prop["items"]["required"]) == {"job", "path"}
    assert prop["items"]["properties"]["path"]["pattern"] == "^/var/log/"
