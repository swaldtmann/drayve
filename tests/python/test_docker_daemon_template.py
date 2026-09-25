"""Tests for ansible/roles/docker/templates/daemon.json.j2 (docker_daemon_extra).

Renders the template with the shared ``ansible_jinja_env`` fixture and
verifies:

1. Byte-identical output to the frozen v0.8.1 template when
   ``docker_daemon_extra`` is empty (the default) — both without and with
   ``docker_registry_mirrors`` set. This is the hard requirement: any
   drift here would restart Docker on every existing Drayve host on the
   next deploy, since the role restarts Docker whenever the rendered
   daemon.json changes (``ansible/roles/docker/tasks/main.yml``).
2. ``docker_daemon_extra`` keys are merged into the rendered JSON and
   survive round-tripping through ``json.loads``.

The v0.8.1 template text below is frozen verbatim (``git show
v0.8.1:ansible/roles/docker/templates/daemon.json.j2``) rather than
fetched via git at test time, so the test stays reproducible without
repo history.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "ansible" / "roles" / "docker" / "templates"
DEFAULTS_FILE = REPO_ROOT / "ansible" / "roles" / "docker" / "defaults" / "main.yml"

# Frozen verbatim from `git show v0.8.1:ansible/roles/docker/templates/daemon.json.j2`.
V081_TEMPLATE = (
    '{\n'
    '  "log-driver": "journald",\n'
    '  "log-opts": {\n'
    '    "tag": "{% raw %}{{.Name}}{% endraw %}"\n'
    '  }{% if docker_registry_mirrors %},\n'
    '  "registry-mirrors": {{ docker_registry_mirrors | to_json }}{% endif %}\n'
    '\n'
    '}\n'
)


def _base_ctx(**overrides: object) -> dict[str, object]:
    ctx: dict[str, object] = {
        "docker_registry_mirrors": [],
        "docker_daemon_extra": {},
    }
    ctx.update(overrides)
    return ctx


@pytest.fixture
def env(ansible_jinja_env):
    e = ansible_jinja_env(TEMPLATE_DIR, strict=True)
    # Mirror Ansible's real template-module default (trim_blocks=True) —
    # load-bearing here since the byte-identity assertions below compare
    # exact rendered text, not just substrings.
    e.trim_blocks = True
    return e


@pytest.fixture
def render_new(env):
    template = env.get_template("daemon.json.j2")

    def _render(**overrides: object) -> str:
        return template.render(**_base_ctx(**overrides))

    return _render


@pytest.fixture
def render_v081(env):
    template = env.from_string(V081_TEMPLATE)

    def _render(**overrides: object) -> str:
        ctx = {"docker_registry_mirrors": []}
        ctx.update(overrides)
        return template.render(**ctx)

    return _render


# ---- 1) Empty docker_daemon_extra must not change a single byte ----

def test_empty_extra_no_mirrors_is_byte_identical_to_v081(render_new, render_v081):
    old = render_v081(docker_registry_mirrors=[])
    new = render_new(docker_registry_mirrors=[], docker_daemon_extra={})
    assert new == old


def test_empty_extra_with_mirrors_is_byte_identical_to_v081(render_new, render_v081):
    mirrors = ["http://192.0.2.10:5000"]
    old = render_v081(docker_registry_mirrors=mirrors)
    new = render_new(docker_registry_mirrors=mirrors, docker_daemon_extra={})
    assert new == old


# ---- 2) docker_daemon_extra merges into valid JSON ----

def test_extra_merges_new_top_level_keys(render_new):
    rendered = render_new(
        docker_daemon_extra={
            "data-root": "/data/containers/docker",
            "runtimes": {
                "nvidia": {"args": [], "path": "nvidia-container-runtime"}
            },
        }
    )
    parsed = json.loads(rendered)
    assert parsed["data-root"] == "/data/containers/docker"
    assert parsed["runtimes"]["nvidia"]["path"] == "nvidia-container-runtime"
    # base keys survive the merge
    assert parsed["log-driver"] == "journald"
    assert parsed["log-opts"]["tag"] == "{{.Name}}"


def test_extra_output_is_valid_json(render_new):
    rendered = render_new(
        docker_daemon_extra={"data-root": "/data/containers/docker"}
    )
    json.loads(rendered)  # raises if invalid


def test_extra_with_mirrors_keeps_mirrors(render_new):
    rendered = render_new(
        docker_registry_mirrors=["http://192.0.2.10:5000"],
        docker_daemon_extra={"data-root": "/data/containers/docker"},
    )
    parsed = json.loads(rendered)
    assert parsed["registry-mirrors"] == ["http://192.0.2.10:5000"]
    assert parsed["data-root"] == "/data/containers/docker"


# ---- 3) defaults declare the new var ----

def test_defaults_declare_docker_daemon_extra():
    text = DEFAULTS_FILE.read_text()
    assert "docker_daemon_extra:" in text
