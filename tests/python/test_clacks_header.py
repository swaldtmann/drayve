"""Tests for the opt-in X-Clacks-Overhead header (``headers.clacks``).

Covers: static-config rendering (on/off/default, crowdsec combinations),
byte-identical output vs. the pre-feature template when off, the traefik
role task, schema entry, and the stack.yaml validator.

Run: ``pytest tests/python/test_clacks_header.py``
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "config" / "traefik" / "traefik.yml.j2"
ROLE_TASKS = REPO_ROOT / "ansible" / "roles" / "traefik" / "tasks" / "main.yml"
SCHEMA = REPO_ROOT / "config" / "stack.schema.yaml"

_spec = importlib.util.spec_from_file_location(
    "validate_stack", REPO_ROOT / "scripts" / "validate-stack.py"
)
validate_stack = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_stack)


def _render(source: str | None = None, **ctx: object) -> str:
    # trim_blocks=True matches Ansible's template module default.
    env = Environment(
        loader=FileSystemLoader(TEMPLATE.parent),
        keep_trailing_newline=True,
        trim_blocks=True,
    )
    tpl = env.from_string(source) if source is not None else env.get_template(TEMPLATE.name)
    return tpl.render(acme_email="ops@example.com", **ctx)


def _entrypoint_middlewares(rendered: str) -> list[str] | None:
    cfg = yaml.safe_load(rendered)
    return cfg["entryPoints"]["websecure"].get("http", {}).get("middlewares")


def test_default_has_no_clacks():
    assert _entrypoint_middlewares(_render()) == ["crowdsec@file"]


def test_clacks_off_explicit_equals_default():
    assert _render(clacks_enabled=False) == _render()


def test_clacks_on_with_crowdsec():
    mw = _entrypoint_middlewares(_render(crowdsec_enabled=True, clacks_enabled=True))
    assert mw == ["crowdsec@file", "clacks@file"]


def test_clacks_on_without_crowdsec():
    mw = _entrypoint_middlewares(_render(crowdsec_enabled=False, clacks_enabled=True))
    assert mw == ["clacks@file"]


def test_both_off_has_no_http_block():
    assert _entrypoint_middlewares(_render(crowdsec_enabled=False, clacks_enabled=False)) is None


@pytest.mark.parametrize("crowdsec", [True, False])
def test_off_is_byte_identical_to_pre_feature_template(crowdsec: bool):
    """Render diff must be empty against the template as committed at HEAD."""
    old = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", "HEAD:config/traefik/traefik.yml.j2"],
        capture_output=True,
        text=True,
    )
    if old.returncode != 0 or "clacks" in old.stdout:
        pytest.skip("HEAD template unavailable or already contains clacks")
    assert _render(old.stdout, crowdsec_enabled=crowdsec) == _render(
        crowdsec_enabled=crowdsec, clacks_enabled=False
    )


def test_role_deploys_middleware_only_when_enabled():
    tasks = yaml.safe_load(ROLE_TASKS.read_text())
    deploy = next(t for t in tasks if t.get("name") == "Deploy X-Clacks-Overhead middleware")
    remove = next(
        t for t in tasks if t.get("name") == "Remove X-Clacks-Overhead middleware when disabled"
    )
    assert deploy["when"] == "clacks_enabled"
    assert remove["when"] == "not (clacks_enabled)"
    assert remove["ansible.builtin.file"]["state"] == "absent"
    content = yaml.safe_load(deploy["ansible.builtin.copy"]["content"])
    hdr = content["http"]["middlewares"]["clacks"]["headers"]["customResponseHeaders"]
    assert hdr == {"X-Clacks-Overhead": "GNU Terry Pratchett"}


def test_schema_declares_boolean_default_false():
    prop = yaml.safe_load(SCHEMA.read_text())["properties"]["headers"]["properties"]["clacks"]
    assert prop["type"] == "boolean"
    assert prop["default"] is False


def _cfg(**extra: object) -> dict:
    cfg = {
        "drayve_version": "0.1.0",
        "stack": {"name": "t", "domain": "example.com", "acme_email": "a@b.de"},
    }
    cfg.update(extra)
    return cfg


@pytest.mark.parametrize("headers", [None, {}, {"clacks": True}, {"clacks": False}])
def test_validator_accepts(headers):
    cfg = _cfg() if headers is None else _cfg(headers=headers)
    errors, _ = validate_stack.validate(cfg)
    assert errors == []


@pytest.mark.parametrize("value", ["true", "yes", 1, 0, None, []])
def test_validator_rejects_non_bool(value):
    errors, _ = validate_stack.validate(_cfg(headers={"clacks": value}))
    assert any("headers.clacks must be a boolean" in e for e in errors)


def test_validator_rejects_non_mapping_headers():
    errors, _ = validate_stack.validate(_cfg(headers=True))
    assert any("headers must be a mapping" in e for e in errors)
