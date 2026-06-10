"""Tests for the stack.yaml `extra_files:` validator (out-of-band per-service config).

Imports `validate()` from scripts/validate-stack.py by path (the script has no
package), feeds it config dicts, and asserts the structural rules:
src required + relative, dest relative, mode octal, the whole block a list.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate-stack.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_stack", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def _base(extra_files):
    return {
        "drayve_version": "0.1.0",
        "stack": {"name": "genua", "domain": "genua.authbox.de"},
        "extra_files": extra_files,
    }


def _errors(config):
    errors, _warnings = validator.validate(config)
    return [e for e in errors if "extra_files" in e]


def test_absent_block_is_valid():
    config = {
        "drayve_version": "0.1.0",
        "stack": {"name": "genua", "domain": "genua.authbox.de"},
    }
    assert _errors(config) == []


def test_minimal_src_only_is_valid():
    assert _errors(_base([{"src": "myapp/nginx.conf"}])) == []


def test_full_entry_is_valid():
    config = _base([{"src": "myapp/.htpasswd", "dest": "myapp/.htpasswd", "mode": "0640"}])
    assert _errors(config) == []


def test_mode_without_leading_zero_is_valid():
    assert _errors(_base([{"src": "a/b.conf", "mode": "644"}])) == []


def test_block_must_be_a_list():
    config = _base({"src": "oops"})
    errs = _errors(config)
    assert any("must be a list" in e for e in errs)


def test_entry_must_be_mapping():
    errs = _errors(_base(["just-a-string"]))
    assert any("must be a mapping" in e for e in errs)


def test_src_is_required():
    errs = _errors(_base([{"dest": "x/y.conf"}]))
    assert any("src is required" in e for e in errs)


def test_src_must_not_be_absolute():
    errs = _errors(_base([{"src": "/etc/passwd"}]))
    assert any("relative path" in e for e in errs)


def test_src_must_not_traverse():
    errs = _errors(_base([{"src": "../../secrets.yml"}]))
    assert any("relative path" in e for e in errs)


def test_dest_must_not_be_absolute():
    errs = _errors(_base([{"src": "a.conf", "dest": "/opt/x"}]))
    assert any("dest must be a relative path" in e for e in errs)


def test_dest_must_not_traverse():
    errs = _errors(_base([{"src": "a.conf", "dest": "../x"}]))
    assert any("dest must be a relative path" in e for e in errs)


def test_mode_must_be_octal():
    errs = _errors(_base([{"src": "a.conf", "mode": "999"}]))
    assert any("octal string" in e for e in errs)


def test_mode_non_octal_letters_rejected():
    errs = _errors(_base([{"src": "a.conf", "mode": "rwxr"}]))
    assert any("octal string" in e for e in errs)


def test_multiple_errors_accumulate():
    config = _base([{"dest": "x"}, {"src": "/abs"}, {"src": "ok", "mode": "8"}])
    errs = _errors(config)
    assert len(errs) >= 3


@pytest.mark.parametrize("mode", ["0644", "0640", "0600", "0755", "644", "755"])
def test_valid_modes(mode):
    assert _errors(_base([{"src": "a.conf", "mode": mode}])) == []
