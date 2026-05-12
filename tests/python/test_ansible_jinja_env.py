"""Smoke tests for the shared ``ansible_jinja_env`` fixture (see conftest.py).

Locks in the contract so template-test authors can rely on the stubs
without re-discovering which filters are available.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


EXPECTED_FILTERS = {
    "bool",
    "to_json",
    "to_nice_json",
    "to_yaml",
    "to_nice_yaml",
    "combine",
    "mandatory",
}


def test_factory_installs_expected_filters(ansible_jinja_env, tmp_path: Path):
    env = ansible_jinja_env(tmp_path)
    missing = EXPECTED_FILTERS - set(env.filters)
    assert not missing, f"missing filters: {missing}"


def test_bool_filter_handles_strings_and_native(ansible_jinja_env, tmp_path: Path):
    env = ansible_jinja_env(tmp_path)
    bool_filter = env.filters["bool"]
    assert bool_filter("true") is True
    assert bool_filter("YES") is True
    assert bool_filter("0") is False
    assert bool_filter(True) is True
    assert bool_filter(0) is False


def test_to_json_filter_renders_dict(ansible_jinja_env, tmp_path: Path):
    (tmp_path / "x.j2").write_text("{{ data | to_json }}")
    env = ansible_jinja_env(tmp_path)
    out = env.get_template("x.j2").render(data={"a": 1, "b": [2, 3]})
    assert json.loads(out) == {"a": 1, "b": [2, 3]}


def test_to_yaml_filter_renders_dict(ansible_jinja_env, tmp_path: Path):
    (tmp_path / "y.j2").write_text("{{ data | to_yaml }}")
    env = ansible_jinja_env(tmp_path)
    out = env.get_template("y.j2").render(data={"a": 1})
    assert yaml.safe_load(out) == {"a": 1}


def test_combine_shallow_merge(ansible_jinja_env, tmp_path: Path):
    (tmp_path / "c.j2").write_text("{{ (a | combine(b)) | to_json }}")
    env = ansible_jinja_env(tmp_path)
    out = env.get_template("c.j2").render(a={"x": 1, "y": 2}, b={"y": 9, "z": 3})
    assert json.loads(out) == {"x": 1, "y": 9, "z": 3}


def test_mandatory_raises_on_empty(ansible_jinja_env, tmp_path: Path):
    (tmp_path / "m.j2").write_text("{{ v | mandatory }}")
    env = ansible_jinja_env(tmp_path)
    with pytest.raises(ValueError):
        env.get_template("m.j2").render(v="")


def test_strict_mode_catches_undefined(ansible_jinja_env, tmp_path: Path):
    from jinja2 import UndefinedError

    (tmp_path / "u.j2").write_text("{{ missing_var }}")
    env = ansible_jinja_env(tmp_path, strict=True)
    with pytest.raises(UndefinedError):
        env.get_template("u.j2").render()
