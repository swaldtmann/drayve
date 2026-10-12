"""Fail-early check: own ``auth.oidc_clients`` list without the ``grafana`` client.

``auth.oidc_clients`` replaces the default client list. Grafana, however, is
always rendered with the Authelia OAuth block (login form off, auto-login on)
when ``auth.provider=authelia`` and Grafana runs on the host. A custom list
without ``id: grafana`` therefore leaves Grafana without any working login.

Both pre-flights must abort with the same core message:
  - scripts/validate-stack.py (works on stack.yaml alone)
  - ansible/tasks/auth_preflight.yml (runs in deploy.yml and validate.yml)

Run: ``pytest tests/python/test_authelia_grafana_client_preflight.py``
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT_TASKS = REPO_ROOT / "ansible" / "tasks" / "auth_preflight.yml"
VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate-stack.py"
EXAMPLES = sorted((REPO_ROOT / "examples").glob("*.yaml"))

DOMAIN = "node01.stream.example.org"
FORGEJO = {
    "id": "forgejo",
    "redirect_uris": ["https://git.node01.stream.example.org/user/oauth2/Authelia/callback"],
}
GRAFANA = {
    "id": "grafana",
    "redirect_uris": [f"https://grafana.{DOMAIN}/login/generic_oauth"],
}
DOC_REF = "docs/auth-oidc-clients.md"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_stack", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def _errors(auth: dict, monitoring: dict | None = None, **extra: object) -> list[str]:
    config = {
        "drayve_version": "0.1.0",
        "stack": {"name": "x", "domain": DOMAIN, "acme_email": "ops@example.org"},
        "auth": auth,
        **extra,
    }
    if monitoring is not None:
        config["monitoring"] = monitoring
    errors, _ = validator.validate(config)
    return errors


def _grafana_errors(errors: list[str]) -> list[str]:
    return [e for e in errors if "grafana" in e.lower() and "oidc_clients" in e]


# ---- validate-stack.py ------------------------------------------------------


def test_validator_rejects_custom_list_without_grafana():
    errors = _grafana_errors(_errors({"provider": "authelia", "oidc_clients": [FORGEJO]}))
    assert len(errors) == 1, errors
    assert "id: grafana" in errors[0]
    assert DOC_REF in errors[0]
    assert f"https://grafana.{DOMAIN}/login/generic_oauth" in errors[0]


def test_validator_rejects_empty_custom_list():
    """An empty list replaces the default too (Jinja default() only fires on undefined)."""
    errors = _grafana_errors(_errors({"provider": "authelia", "oidc_clients": []}))
    assert len(errors) == 1, errors


@pytest.mark.parametrize("profile", ["full", None])
def test_validator_rejects_with_default_or_full_profile(profile):
    monitoring = {"profile": profile} if profile else None
    errors = _errors({"provider": "authelia", "oidc_clients": [FORGEJO]}, monitoring)
    assert _grafana_errors(errors)


def test_validator_accepts_custom_list_with_grafana():
    errors = _errors({"provider": "authelia", "oidc_clients": [FORGEJO, GRAFANA]})
    assert _grafana_errors(errors) == []


def test_validator_accepts_without_custom_list():
    assert _errors({"provider": "authelia"}) == []
    assert _errors({"provider": "authelia", "oidc_clients": None}) == []


@pytest.mark.parametrize("profile", ["light", "none"])
def test_validator_accepts_monitoring_without_grafana(profile):
    errors = _errors({"provider": "authelia", "oidc_clients": [FORGEJO]}, {"profile": profile})
    assert _grafana_errors(errors) == []


def test_validator_grafana_flag_overrides_profile():
    off = _errors(
        {"provider": "authelia", "oidc_clients": [FORGEJO]}, monitoring_grafana=False
    )
    on = _errors(
        {"provider": "authelia", "oidc_clients": [FORGEJO]},
        {"profile": "light"},
        monitoring_grafana=True,
    )
    assert _grafana_errors(off) == []
    assert _grafana_errors(on)


@pytest.mark.parametrize("provider", ["basic", "none", "authentik", "authelia_remote"])
def test_validator_ignores_other_providers(provider):
    auth = {"provider": provider, "oidc_clients": [FORGEJO]}
    if provider == "authelia_remote":
        auth["remote"] = {
            "forward_auth_url": "https://auth.example.org/api/authz/forward-auth",
            "portal_url": "https://auth.example.org",
        }
    assert _grafana_errors(_errors(auth)) == []


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.name for p in EXAMPLES])
def test_examples_still_validate(path):
    r = subprocess.run(
        ["python3", str(VALIDATOR_PATH), str(path)], capture_output=True, text=True, check=False
    )
    assert r.returncode == 0, r.stdout + r.stderr


# ---- Ansible preflight -------------------------------------------------------


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


def _run_preflight(tmp_path: Path, **extra_vars: object) -> subprocess.CompletedProcess[str]:
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            ---
            - name: auth preflight wrapper
              hosts: localhost
              connection: local
              gather_facts: false
              tasks:
                - ansible.builtin.import_tasks: {PREFLIGHT_TASKS}
            """
        )
    )
    cmd = ["ansible-playbook", "-i", "localhost,", str(wrapper), "-e", f"drayve_domain={DOMAIN}"]
    if extra_vars:
        cmd += ["-e", json.dumps(extra_vars)]
    env = {"ANSIBLE_LOCALHOST_WARNING": "False", "ANSIBLE_INVENTORY_UNPARSED_WARNING": "False"}
    return subprocess.run(
        cmd, capture_output=True, text=True, check=False, env={**os.environ, **env}
    )


def test_preflight_rejects_custom_list_without_grafana(ansible_available, tmp_path):
    r = _run_preflight(tmp_path, auth={"provider": "authelia", "oidc_clients": [FORGEJO]})
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "id: grafana" in out
    assert DOC_REF in out
    assert f"https://grafana.{DOMAIN}/login/generic_oauth" in out


def test_preflight_rejects_empty_custom_list(ansible_available, tmp_path):
    r = _run_preflight(tmp_path, auth={"provider": "authelia", "oidc_clients": []})
    assert r.returncode != 0, r.stdout


def test_preflight_rejects_with_explicit_full_profile(ansible_available, tmp_path):
    r = _run_preflight(
        tmp_path,
        auth={"provider": "authelia", "oidc_clients": [FORGEJO]},
        monitoring={"profile": "full"},
    )
    assert r.returncode != 0, r.stdout


def test_preflight_accepts_custom_list_with_grafana(ansible_available, tmp_path):
    r = _run_preflight(
        tmp_path, auth={"provider": "authelia", "oidc_clients": [FORGEJO, GRAFANA]}
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_preflight_accepts_without_custom_list(ansible_available, tmp_path):
    r = _run_preflight(tmp_path, auth={"provider": "authelia"})
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("profile", ["light", "none"])
def test_preflight_accepts_monitoring_without_grafana(ansible_available, tmp_path, profile):
    r = _run_preflight(
        tmp_path,
        auth={"provider": "authelia", "oidc_clients": [FORGEJO]},
        monitoring={"profile": profile},
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_preflight_grafana_flag_overrides_profile(ansible_available, tmp_path):
    auth = {"provider": "authelia", "oidc_clients": [FORGEJO]}
    off = _run_preflight(tmp_path, auth=auth, monitoring_grafana=False)
    assert off.returncode == 0, off.stdout + off.stderr
    on = _run_preflight(tmp_path, auth=auth, monitoring={"profile": "light"}, monitoring_grafana=True)
    assert on.returncode != 0, on.stdout


@pytest.mark.parametrize("provider", ["basic", "none", "authentik"])
def test_preflight_ignores_other_providers(ansible_available, tmp_path, provider):
    r = _run_preflight(tmp_path, auth={"provider": provider, "oidc_clients": [FORGEJO]})
    assert r.returncode == 0, r.stdout + r.stderr


def test_preflight_ignores_authelia_remote(ansible_available, tmp_path):
    auth = {
        "provider": "authelia_remote",
        "oidc_clients": [FORGEJO],
        "remote": {
            "forward_auth_url": "https://auth.example.org/api/authz/forward-auth",
            "portal_url": "https://auth.example.org",
        },
    }
    r = _run_preflight(tmp_path, auth=auth)
    assert r.returncode == 0, r.stdout + r.stderr
