"""Tests for the acme_challenge/traefik_certresolver resolve logic (DRAYVE-W-011).

Before this fix, stack.acme_dns_provider had no stack.yaml-to-Ansible-var
resolve task at all, and every Traefik router label in docker-compose.yml.j2
hardcoded certresolver=letsencrypt regardless — so DNS-01 was unreachable no
matter what a user set. This tests the new resolve chain in isolation (same
approach as test_preflight_vars.py: import_tasks against ansible-playbook)
plus anchors that confirm the real files match.

Requires ansible-playbook on PATH (true in the .venv).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_TASKS = REPO_ROOT / "ansible" / "roles" / "deploy" / "tasks" / "main.yml"
COMPOSE_TEMPLATE = REPO_ROOT / "ansible" / "templates" / "docker-compose.yml.j2"
TRAEFIK_DEFAULTS = REPO_ROOT / "ansible" / "roles" / "traefik" / "defaults" / "main.yml"

# Isolated copy of the four new resolve tasks from deploy/tasks/main.yml —
# same isolation approach as test_grafana_auth_middleware.py's router snippet.
_ACME_RESOLVE_SNIPPET = textwrap.dedent(
    """\
    - name: Resolve acme_challenge from stack.yaml
      ansible.builtin.set_fact:
        acme_challenge: "{{ stack.acme_challenge | default('http') }}"

    - name: Resolve acme_dns_provider from stack.yaml
      ansible.builtin.set_fact:
        acme_dns_provider: "{{ stack.acme_dns_provider | default(acme_dns_provider) | default('hetzner') }}"

    - name: Resolve acme_dns_env_vars from stack.yaml
      ansible.builtin.set_fact:
        acme_dns_env_vars: "{{ stack.acme_dns_env_vars | default(acme_dns_env_vars) | default(['HETZNER_API_TOKEN']) }}"

    - name: Resolve acme_dns_propagation_delay from stack.yaml
      ansible.builtin.set_fact:
        acme_dns_propagation_delay: "{{ stack.acme_dns_propagation_delay | default(acme_dns_propagation_delay) | default(30) }}"

    - name: Derive traefik_certresolver from acme_challenge
      ansible.builtin.set_fact:
        traefik_certresolver: "{{ 'letsencrypt-dns' if acme_challenge == 'dns' else 'letsencrypt' }}"

    - ansible.builtin.debug:
        msg: "RESULT acme_challenge={{ acme_challenge }} traefik_certresolver={{ traefik_certresolver }} acme_dns_provider={{ acme_dns_provider }}"
    """
)


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


@pytest.fixture(scope="module")
def wrapper_playbook(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tasks_file = tmp_path_factory.mktemp("acme_resolve") / "tasks.yml"
    tasks_file.write_text(_ACME_RESOLVE_SNIPPET)
    playbook = tasks_file.parent / "wrapper.yml"
    playbook.write_text(
        textwrap.dedent(
            f"""\
            ---
            - name: Test acme_challenge resolve
              hosts: localhost
              connection: local
              gather_facts: false
              tasks:
                - ansible.builtin.import_tasks: {tasks_file}
            """
        )
    )
    return playbook


def _run(wrapper: Path, stack: dict) -> subprocess.CompletedProcess[str]:
    cmd = [
        "ansible-playbook",
        "-i",
        "localhost,",
        str(wrapper),
        "-e",
        json.dumps({"stack": stack}),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_default_is_http_challenge_letsencrypt(ansible_available, wrapper_playbook):
    """No acme_challenge set in stack.yaml -> http, resolver stays letsencrypt (unchanged default)."""
    result = _run(wrapper_playbook, {})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "acme_challenge=http" in result.stdout
    assert "traefik_certresolver=letsencrypt " in result.stdout
    assert "traefik_certresolver=letsencrypt-dns" not in result.stdout


def test_acme_challenge_dns_switches_resolver(ansible_available, wrapper_playbook):
    """stack.acme_challenge: dns -> resolver becomes letsencrypt-dns."""
    result = _run(wrapper_playbook, {"acme_challenge": "dns"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "acme_challenge=dns" in result.stdout
    assert "traefik_certresolver=letsencrypt-dns" in result.stdout


def test_acme_dns_provider_passed_through(ansible_available, wrapper_playbook):
    """stack.acme_dns_provider actually reaches the resolved var now (it never did before)."""
    result = _run(wrapper_playbook, {"acme_challenge": "dns", "acme_dns_provider": "cloudflare"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "acme_dns_provider=cloudflare" in result.stdout


def test_acme_dns_provider_defaults_to_hetzner(ansible_available, wrapper_playbook):
    result = _run(wrapper_playbook, {"acme_challenge": "dns"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "acme_dns_provider=hetzner" in result.stdout


def test_deploy_tasks_contains_the_real_resolve_chain():
    """Anchor: the isolated snippet above must match what's actually deployed."""
    text = DEPLOY_TASKS.read_text()
    assert "stack.acme_challenge | default('http')" in text
    assert "'letsencrypt-dns' if acme_challenge == 'dns' else 'letsencrypt'" in text


def test_no_router_hardcodes_letsencrypt_certresolver():
    """Regression guard: DRAYVE-W-011 Fund 1 — every router must use the variable,
    not a hardcoded resolver name (which made acme_dns_provider inert)."""
    text = COMPOSE_TEMPLATE.read_text()
    assert 'certresolver=letsencrypt"' not in text
    assert text.count("certresolver={{ traefik_certresolver }}") == 7


def test_traefik_defaults_has_matching_certresolver_default():
    text = TRAEFIK_DEFAULTS.read_text()
    assert "traefik_certresolver: letsencrypt" in text
