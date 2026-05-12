"""Shared pytest fixtures for Drayve template tests.

The ``ansible_jinja_env`` fixture builds a plain Jinja2 ``Environment``
with Ansible-specific filters stubbed in. Unit tests render Ansible
templates without an Ansible runtime, so any filter Ansible adds to the
default Jinja2 set must be stubbed — otherwise ``| bool``, ``| to_json``
etc. raise ``TemplateAssertionError`` at parse time.

Stubbed filters mirror Ansible semantics close enough for template
contract tests, not for behavioral parity. Add more stubs here when a
template starts using them — that's cheaper than duplicating the stub
in every test module.

Beifang-Anlass: W-144 (S328-Folge13) — test_authelia_template.py and
test_kedge_template.py duplicated the ``bool``/``to_json`` stubs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


def _ansible_bool(value: object) -> bool:
    """Mimic Ansible's ``| bool`` filter."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _ansible_combine(base: dict, *others: dict, recursive: bool = False) -> dict:
    """Mimic Ansible's ``| combine`` filter (shallow merge by default)."""
    out = dict(base or {})
    for other in others:
        if not other:
            continue
        if recursive:
            for k, v in other.items():
                if isinstance(v, dict) and isinstance(out.get(k), dict):
                    out[k] = _ansible_combine(out[k], v, recursive=True)
                else:
                    out[k] = v
        else:
            out.update(other)
    return out


def _ansible_mandatory(value, msg: str = "Mandatory variable not set"):
    """Mimic Ansible's ``| mandatory`` — raises when value is falsy/None."""
    if value is None or value == "":
        raise ValueError(msg)
    return value


def _install_ansible_filters(env: Environment) -> None:
    env.filters["bool"] = _ansible_bool
    env.filters["to_json"] = lambda v, **_: json.dumps(v)
    env.filters["to_nice_json"] = lambda v, indent=4, **_: json.dumps(v, indent=indent)
    env.filters["to_yaml"] = lambda v, **_: yaml.safe_dump(v, default_flow_style=False)
    env.filters["to_nice_yaml"] = lambda v, **_: yaml.safe_dump(v, default_flow_style=False)
    env.filters["combine"] = _ansible_combine
    env.filters["mandatory"] = _ansible_mandatory


@pytest.fixture
def ansible_jinja_env() -> Callable[..., Environment]:
    """Factory that returns a Jinja2 Environment with Ansible filters stubbed.

    Usage:
        def test_x(ansible_jinja_env):
            env = ansible_jinja_env(template_dir, strict=True)
            tpl = env.get_template("foo.j2")
            ...

    Args:
        template_dir: Path or str of the template directory for FileSystemLoader.
        strict: If True, use StrictUndefined (catches missing vars at render).
    """
    def _factory(template_dir: Path | str, *, strict: bool = False) -> Environment:
        kwargs: dict = {
            "loader": FileSystemLoader(str(template_dir)),
            "keep_trailing_newline": True,
        }
        if strict:
            kwargs["undefined"] = StrictUndefined
        env = Environment(**kwargs)
        _install_ansible_filters(env)
        return env

    return _factory
