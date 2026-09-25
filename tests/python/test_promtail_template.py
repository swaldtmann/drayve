"""Tests for optional Promtail Basic-Auth + host label (v0.8.3).

Covers two templates:

1. ``ansible/roles/monitoring/templates/promtail-config.yml.j2`` — new
   ``basic_auth`` (username + ``password_file``, never a plaintext password)
   and ``external_labels.host`` blocks under the Loki ``clients`` entry,
   gated on ``_promtail_loki_basic_auth_user`` / ``_promtail_host_label``
   (``roles/monitoring/tasks/main.yml``).
2. ``ansible/templates/docker-compose.yml.j2`` — the Promtail service's
   ``volumes:`` block gains a read-only mount of the password file, gated
   on ``_deploy_promtail_basic_auth_user`` (``roles/deploy/tasks/main.yml``).

Hard requirement, same idiom as ``test_docker_daemon_template.py``: with
both new vars empty (the default), both templates render byte-identical
to the frozen v0.8.2 output — any drift here would force-recreate/restart
Promtail on every existing Drayve host on the next deploy
(``Force-recreate Promtail on config change``, ``roles/monitoring/tasks/main.yml``).
The v0.8.2 template text below is frozen verbatim (``git show
v0.8.2:<path>``) rather than fetched via git at test time, so the test
stays reproducible without repo history.

Run: ``pytest tests/python/test_promtail_template.py``
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMTAIL_TEMPLATE_DIR = REPO_ROOT / "ansible" / "roles" / "monitoring" / "templates"
COMPOSE_TEMPLATE = REPO_ROOT / "ansible" / "templates" / "docker-compose.yml.j2"

# Frozen verbatim from
# `git show v0.8.2:ansible/roles/monitoring/templates/promtail-config.yml.j2`.
V082_PROMTAIL_CONFIG = (
    'server:\n'
    '  http_listen_port: 9080\n'
    '  grpc_listen_port: 0\n'
    'positions:\n'
    '  filename: /tmp/positions.yaml\n'
    'clients:\n'
    '  - url: "{{ _promtail_loki_url }}"\n'
    'scrape_configs:\n'
    '  - job_name: docker\n'
    '    docker_sd_configs:\n'
    '      - host: unix:///var/run/docker.sock\n'
    '        refresh_interval: 5s\n'
    '    relabel_configs:\n'
    "      - source_labels: ['__meta_docker_container_name']\n"
    "        regex: '/(.*)'\n"
    "        target_label: 'container'\n"
    "      - source_labels: ['__meta_docker_container_name']\n"
    "        regex: '/(.*)'\n"
    "        target_label: 'service'\n"
    '{% for cfg in monitoring_promtail_extra_scrape_configs %}\n'
    '  - {{ cfg | to_nice_yaml(indent=2) | indent(4) | trim }}\n'
    '{% endfor %}\n'
)

# Frozen verbatim from `git show v0.8.2:ansible/templates/docker-compose.yml.j2`,
# promtail service block only (same isolated-snippet approach as
# test_grafana_auth_middleware.py).
V082_PROMTAIL_COMPOSE_SNIPPET = (
    '{% if monitoring_services.promtail | default(false) | bool %}\n'
    '  promtail:\n'
    '    image: {{ promtail_image }}\n'
    '    container_name: promtail\n'
    '    restart: unless-stopped\n'
    '    mem_limit: {{ mem_promtail }}\n'
    '    command: -config.file=/etc/promtail/promtail-config.yml\n'
    '    volumes:\n'
    '      - ./monitoring/promtail/promtail-config.yml:/etc/promtail/promtail-config.yml:ro\n'
    '      - /var/log:/var/log:ro\n'
    '      - /var/run/docker.sock:/var/run/docker.sock:ro\n'
    '{% endif %}\n'
)


def _base_config_ctx(**overrides: object) -> dict[str, object]:
    ctx: dict[str, object] = {
        "_promtail_loki_url": "http://loki:3100/loki/api/v1/push",
        "_promtail_loki_basic_auth_user": "",
        "_promtail_host_label": "",
        "monitoring_promtail_extra_scrape_configs": [],
    }
    ctx.update(overrides)
    return ctx


@pytest.fixture
def env(ansible_jinja_env):
    e = ansible_jinja_env(PROMTAIL_TEMPLATE_DIR, strict=True)
    # Mirror Ansible's real template-module default (trim_blocks=True) —
    # the byte-identity assertions below compare exact rendered text.
    e.trim_blocks = True
    return e


@pytest.fixture
def render_config(env):
    template = env.get_template("promtail-config.yml.j2")

    def _render(**overrides: object) -> str:
        return template.render(**_base_config_ctx(**overrides))

    return _render


@pytest.fixture
def compose_env(ansible_jinja_env):
    """Same Ansible-filter-stubbed Environment as `env`, but for
    `Environment.from_string` snippets (docker-compose.yml.j2 isn't loaded
    via FileSystemLoader here — see test_grafana_auth_middleware.py for the
    same isolated-snippet approach)."""
    e = ansible_jinja_env(PROMTAIL_TEMPLATE_DIR, strict=False)
    e.trim_blocks = True
    return e


@pytest.fixture
def render_v082_config(compose_env):
    template = compose_env.from_string(V082_PROMTAIL_CONFIG)

    def _render(**overrides: object) -> str:
        ctx: dict[str, object] = {
            "_promtail_loki_url": "http://loki:3100/loki/api/v1/push",
            "monitoring_promtail_extra_scrape_configs": [],
        }
        ctx.update(overrides)
        return template.render(**ctx)

    return _render


def _render_compose_snippet(env: Environment, text: str, **ctx: object) -> str:
    template = env.from_string(text)
    return template.render(**ctx)


# ---- 1) Unset (default) is byte-identical to v0.8.2 -----------------------


def test_promtail_config_default_is_byte_identical_to_v082(render_config, render_v082_config):
    old = render_v082_config()
    new = render_config()
    assert new == old


def test_promtail_config_default_with_extra_scrape_configs_is_byte_identical_to_v082(
    render_config, render_v082_config
):
    extra = [{"job_name": "kedge", "static_configs": [{"targets": ["localhost"]}]}]
    old = render_v082_config(monitoring_promtail_extra_scrape_configs=extra)
    new = render_config(monitoring_promtail_extra_scrape_configs=extra)
    assert new == old


def test_compose_promtail_snippet_default_is_byte_identical_to_v082(compose_env):
    ctx = {
        "monitoring_services": {"promtail": True},
        "promtail_image": "grafana/promtail:3.3.2",
        "mem_promtail": "128m",
        "_deploy_promtail_basic_auth_user": "",
    }
    old = _render_compose_snippet(compose_env, V082_PROMTAIL_COMPOSE_SNIPPET, **ctx)
    new = _render_compose_snippet(compose_env, _current_compose_promtail_snippet(), **ctx)
    assert new == old


def test_compose_promtail_snippet_undefined_user_is_byte_identical_to_v082(compose_env):
    """`_deploy_promtail_basic_auth_user` entirely absent (e.g. old context) — `default('')` catches it."""
    ctx = {
        "monitoring_services": {"promtail": True},
        "promtail_image": "grafana/promtail:3.3.2",
        "mem_promtail": "128m",
    }
    old = _render_compose_snippet(compose_env, V082_PROMTAIL_COMPOSE_SNIPPET, **ctx)
    new = _render_compose_snippet(compose_env, _current_compose_promtail_snippet(), **ctx)
    assert new == old


# ---- 2) Basic-Auth user set: username + password_file, no plaintext password ----


def test_promtail_config_with_basic_auth_user_has_username_and_password_file(render_config):
    rendered = render_config(_promtail_loki_basic_auth_user="buckbeak")
    parsed = yaml.safe_load(rendered)
    client = parsed["clients"][0]
    assert client["basic_auth"]["username"] == "buckbeak"
    assert client["basic_auth"]["password_file"] == "/etc/promtail/loki-basic-auth-password"
    assert "password" not in client["basic_auth"]


def test_promtail_config_never_contains_a_plaintext_password_field(render_config):
    """No code path in the template can render a literal `password:` key."""
    rendered = render_config(_promtail_loki_basic_auth_user="buckbeak")
    assert "password_file" in rendered
    assert "\n      password:" not in rendered


def test_promtail_config_without_basic_auth_user_has_no_basic_auth_block(render_config):
    rendered = render_config(_promtail_loki_basic_auth_user="")
    assert "basic_auth" not in rendered


def test_compose_promtail_snippet_with_basic_auth_user_mounts_password_file(compose_env):
    ctx = {
        "monitoring_services": {"promtail": True},
        "promtail_image": "grafana/promtail:3.3.2",
        "mem_promtail": "128m",
        "_deploy_promtail_basic_auth_user": "buckbeak",
    }
    rendered = _render_compose_snippet(compose_env, _current_compose_promtail_snippet(), **ctx)
    assert (
        "./monitoring/promtail/loki-basic-auth-password:/etc/promtail/loki-basic-auth-password:ro"
        in rendered
    )


def test_compose_promtail_snippet_without_basic_auth_user_has_no_password_mount(compose_env):
    ctx = {
        "monitoring_services": {"promtail": True},
        "promtail_image": "grafana/promtail:3.3.2",
        "mem_promtail": "128m",
        "_deploy_promtail_basic_auth_user": "",
    }
    rendered = _render_compose_snippet(compose_env, _current_compose_promtail_snippet(), **ctx)
    assert "loki-basic-auth-password" not in rendered


# ---- 3) Host label -----------------------------------------------------


def test_promtail_config_with_host_label_sets_external_labels(render_config):
    rendered = render_config(_promtail_host_label="buckbeak")
    parsed = yaml.safe_load(rendered)
    client = parsed["clients"][0]
    assert client["external_labels"] == {"host": "buckbeak"}


def test_promtail_config_without_host_label_has_no_external_labels(render_config):
    rendered = render_config(_promtail_host_label="")
    assert "external_labels" not in rendered


def test_promtail_config_with_user_and_label_is_valid_yaml(render_config):
    rendered = render_config(
        _promtail_loki_basic_auth_user="buckbeak",
        _promtail_host_label="buckbeak",
    )
    parsed = yaml.safe_load(rendered)
    client = parsed["clients"][0]
    assert client["basic_auth"]["username"] == "buckbeak"
    assert client["external_labels"] == {"host": "buckbeak"}


# ---- 4) Anchors against the real files (not just an isolated copy) --------


_JINJA_IF_TAG = re.compile(r"{%-?\s*if\b|{%-?\s*endif\s*-?%}")


def _current_compose_promtail_snippet() -> str:
    """Extract the promtail service's `{% if %}...{% endif %}` block from the
    real compose template, honoring nesting (the block now contains its own
    inner `{% if _deploy_promtail_basic_auth_user %}` for the password mount,
    so a naive "first endif after start" search would truncate early)."""
    text = COMPOSE_TEMPLATE.read_text()
    start = text.index("{% if monitoring_services.promtail")
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


def test_compose_template_promtail_block_matches_frozen_snippet_structure():
    """Anchor: the real compose template's promtail block still starts/ends
    where the isolated snippet above assumes — catches silent drift if the
    block gets reshaped without updating this test."""
    snippet = _current_compose_promtail_snippet()
    assert "container_name: promtail" in snippet
    assert "command: -config.file=/etc/promtail/promtail-config.yml" in snippet
