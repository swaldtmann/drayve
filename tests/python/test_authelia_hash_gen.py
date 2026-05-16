"""Regression tests for the Authelia OIDC client-secret hash generation task.

HUB-W-003: Drayve generated pbkdf2 hashes via Python's hashlib + stdlib-base64
(`+/` alphabet). Authelia's pbkdf2 decoder expects crypt-format base64 (`./`
alphabet) and rejects salts/hashes containing `+` with
"illegal base64 data at input byte N" — probabilistically (~60%) crashing
Authelia at config-load. Fix: switch to argon2id via the Authelia CLI which
produces a format Authelia reliably accepts.

These tests guard against accidental reintroduction of the Python pbkdf2 path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROLE_DIR = Path(__file__).resolve().parents[2] / "ansible" / "roles" / "authelia"
TASKS = (ROLE_DIR / "tasks" / "main.yml").read_text(encoding="utf-8")


def test_no_python_pbkdf2_generation() -> None:
    """The role must not call hashlib.pbkdf2_hmac (HUB-W-003 regression)."""
    assert "pbkdf2_hmac" not in TASKS, (
        "Python hashlib.pbkdf2_hmac re-introduced. "
        "It produces stdlib-base64 ('+/') which Authelia's pbkdf2 decoder rejects. "
        "Use argon2id via the Authelia CLI instead."
    )


def test_no_stdlib_b64_for_hash_encoding() -> None:
    """Stdlib base64.b64encode for hash-encoding is forbidden (HUB-W-003)."""
    assert "base64.b64encode" not in TASKS, (
        "Stdlib base64.b64encode reintroduced for password-hash encoding. "
        "Authelia expects crypt-format base64; use argon2id via Authelia CLI."
    )


def test_argon2_generation_via_authelia_cli() -> None:
    """Hash generation must use the Authelia CLI argon2 path."""
    assert "authelia crypto hash generate argon2" in TASKS, (
        "Argon2id hash generation via the Authelia CLI is missing."
    )
    assert "--variant argon2id" in TASKS, "Argon2 variant must be argon2id."


@pytest.mark.parametrize("flag", ["--no-confirm", "--password"])
def test_argon2_flags_present(flag: str) -> None:
    """Argon2 generation must be non-interactive (--no-confirm, --password)."""
    assert flag in TASKS, f"Argon2 invocation missing flag: {flag}"
