"""Tests for the configurable Authelia session lifetime keys.

``auth.session_inactivity`` / ``auth.session_expiration`` / ``auth.session_remember_me``
are rendered on the top level of ``session:`` in config/authelia/configuration.yml.j2.
``session_remember_me: -1`` must reach Authelia as the bare YAML integer -1 (the only
form that switches "remember me" off); the string "-1" is rejected up front.

Run: ``pytest tests/python/test_authelia_session.py``
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTHELIA_TEMPLATE = REPO_ROOT / "config" / "authelia" / "configuration.yml.j2"
VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate-stack.py"
AUTH_FILTER_PATH = REPO_ROOT / "ansible" / "playbooks" / "filter_plugins" / "drayve_auth.py"

LEGACY_SESSION = (
    "session:\n"
    '  secret: "s"\n'
    "  cookies:\n"
    '    - domain: "node01.stream.example.org"\n'
    '      authelia_url: "https://auth.node01.stream.example.org"\n'
)


@pytest.fixture
def render_authelia(ansible_jinja_env):
    env = ansible_jinja_env(AUTHELIA_TEMPLATE.parent)
    env.trim_blocks = True  # Ansible's template module default; the plain fixture env has it off
    template = env.get_template(AUTHELIA_TEMPLATE.name)
    base = {
        "drayve_domain": "node01.stream.example.org",
        "auth_lldap": False,
        "authelia_session_secret": "s",
        "authelia_storage_encryption_key": "k",
        "authelia_jwt_secret": "j",
        "authelia_oidc_hmac_secret": "h",
        "authelia_jwks_pem": "stub\n",
        "authelia_oidc_clients": [],
        "authelia_oidc_clients_default": [],
    }

    def _render(**overrides: object) -> str:
        return template.render(**{**base, **overrides})

    return _render


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load(VALIDATOR_PATH, "validate_stack")
drayve_auth = _load(AUTH_FILTER_PATH, "drayve_auth_session")

SESSION_AUTH = {
    "session_inactivity": "30m",
    "session_expiration": "8h",
    "session_remember_me": -1,
}


def _errors(auth: dict) -> list[str]:
    stack = {
        "drayve_version": "0.1.0",
        "stack": {"name": "x", "domain": "node01.stream.example.org", "acme_email": "ops@example.org"},
        "auth": {"provider": "authelia", **auth},
    }
    return validator.validate(stack)[0]


# ---- rendering -------------------------------------------------------------


def test_session_keys_render_on_top_level(render_authelia):
    out = render_authelia(auth=SESSION_AUTH)
    session = yaml.safe_load(out)["session"]
    assert session["remember_me"] == -1
    assert isinstance(session["remember_me"], int) and not isinstance(session["remember_me"], bool)
    assert session["inactivity"] == "30m"
    assert session["expiration"] == "8h"
    assert "  remember_me: -1\n" in out  # bare number, not quoted
    assert "cookies" in session and "remember_me" not in session["cookies"][0]


def test_remember_me_duration_string_stays_string(render_authelia):
    session = yaml.safe_load(render_authelia(auth={"session_remember_me": "1M"}))["session"]
    assert session["remember_me"] == "1M"


def test_single_key_renders_only_that_key(render_authelia):
    session = yaml.safe_load(render_authelia(auth={"session_inactivity": "15m"}))["session"]
    assert session["inactivity"] == "15m"
    assert "expiration" not in session and "remember_me" not in session


def test_without_keys_session_block_is_byte_identical(render_authelia):
    assert LEGACY_SESSION in render_authelia()
    assert LEGACY_SESSION in render_authelia(auth={})
    assert LEGACY_SESSION in render_authelia(auth={"provider": "authelia"})


# ---- validation ------------------------------------------------------------


def test_validator_accepts_session_settings():
    assert _errors(SESSION_AUTH) == []


@pytest.mark.parametrize("value", ["30m", "8h", "1h30m", "1d", "2w", "90s"])
def test_validator_accepts_duration_strings(value):
    for key in ("session_inactivity", "session_expiration", "session_remember_me"):
        assert _errors({key: value}) == []


@pytest.mark.parametrize("value", ["-1", "-1s", "0", "30", "abc", "", "1h 30m", "30m\n", True, 5, 1.5, ["1h"]])
def test_validator_rejects_bad_remember_me(value):
    errors = _errors({"session_remember_me": value})
    assert len(errors) == 1 and "auth.session_remember_me" in errors[0]


def test_validator_explains_quoted_minus_one():
    (msg,) = _errors({"session_remember_me": "-1"})
    assert "unquoted" in msg and "-1" in msg


@pytest.mark.parametrize("value", ["-1", 30, -1, "0", "abc", True, "30 m"])
def test_validator_rejects_bad_inactivity_and_expiration(value):
    for key in ("session_inactivity", "session_expiration"):
        errors = _errors({key: value})
        assert len(errors) == 1 and f"auth.{key}" in errors[0]


def test_session_keys_only_checked_for_authelia():
    errors, _ = drayve_auth.check_auth({"provider": "basic", "session_remember_me": "-1"})
    assert errors == []
    errors, _ = drayve_auth.check_auth({"provider": "authelia", "session_remember_me": "-1"})
    assert len(errors) == 1


def test_filter_plugin_reports_same_errors():
    assert drayve_auth.drayve_auth_errors({"provider": "authelia", "session_remember_me": "-1"})


def test_null_means_unset(render_authelia):
    auth = {"session_inactivity": None, "session_expiration": None, "session_remember_me": None}
    assert _errors(auth) == []
    assert LEGACY_SESSION in render_authelia(auth=auth)
