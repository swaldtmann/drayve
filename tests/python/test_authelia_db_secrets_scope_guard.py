"""Tests for AFKI-W-240 (Befund 1) — destructive tag-scope gap in the Authelia
DB removal, closes the last of the AFKI-W-233 bug-class instances found in the
AFKI-W-238/239 debriefs.

Bug: ``roles/authelia/tasks/main.yml`` ("Remove stale Authelia DB on config
change") deletes ``db.sqlite3`` whenever the "Deploy Authelia configuration"
template task reports ``changed: true``. That template renders
``authelia_jwt_secret``/``authelia_session_secret``/
``authelia_storage_encryption_key``/``authelia_ldap_password``/
``authelia_oidc_*`` — all of which come exclusively from ``roles/secrets``
(tag ``[secrets]``). A ``--tags authelia`` run (no ``secrets`` tag) leaves
those fields empty, the rendered config differs from the correctly-deployed
one on disk, "Deploy Authelia configuration" reports ``changed: true``, and
the removal task then deletes an already-deployed, working Authelia DB
(2FA registrations, sessions, tokens) — same bug class as
AFKI-W-233/238/239.

Fix under test: a re-stat of ``secrets.yml`` + the DB file (cheap, independent
of whether ``roles/secrets`` ran this pass) plus an ``ansible.builtin.assert``
that fails hard when the file exists but ``drayve_secrets_facts_loaded``
(AFKI-W-233 sentinel) was never set, the config task reports ``changed``, AND
the DB already exists — same pattern as AFKI-W-239's monitoring-role guard.
The DB-exists check means a genuinely fresh install (nothing to lose) is not
blocked.

The wrapper below inlines the exact task sequence added to
``roles/secrets/tasks/main.yml`` and ``roles/authelia/tasks/main.yml`` (same
approach as ``test_deploy_secrets_tag_guard.py`` / AFKI-W-233 and
``test_monitoring_deploy_secrets_scope_guard.py`` / AFKI-W-239) so the test
runs against ``hosts: localhost`` without a real target host, docker, or the
full role dependency chain (OIDC key generation, argon2 hashing, ...).
"Deploy Authelia configuration"'s ``changed`` outcome itself is simulated via
an extra-var (``authelia_config_changed``) rather than re-derived from a real
template diff — that diff mechanism is Ansible's own well-tested behavior,
out of scope here (same simplification ``test_monitoring_deploy_secrets_scope_guard.py``
makes for ``grafana_alert_api_token`` being empty).

Run: ``pytest tests/python/test_authelia_db_secrets_scope_guard.py``
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

WRAPPER_TASKS = """\
            # --- roles/secrets/tasks/main.yml (relevant subset) ---
            - name: Check for host secrets file
              ansible.builtin.stat:
                path: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/secrets.yml"
              register: secrets_file
              tags: [secrets]

            - name: Load host secrets (plaintext/quickstart)
              ansible.builtin.include_vars:
                file: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/secrets.yml"
                name: host_secrets
              when: secrets_file.stat.exists
              tags: [secrets]

            - name: Set secret variables
              ansible.builtin.set_fact:
                authelia_jwt_secret: "{{ host_secrets.authelia_jwt_secret | default('') }}"
              when: secrets_file.stat.exists
              no_log: true
              tags: [secrets]

            - name: Mark secret facts as loaded (cross-tag guard sentinel)
              ansible.builtin.set_fact:
                drayve_secrets_facts_loaded: true
              when: secrets_file.stat.exists
              tags: [secrets]

            # --- roles/authelia/tasks/main.yml (relevant subset) ---
            - name: Simulate "Deploy Authelia configuration" result
              ansible.builtin.set_fact:
                authelia_config: "{{ {'changed': authelia_config_changed | bool} }}"
              tags: [authelia]

            - name: Check for host secrets file (cross-tag guard, AFKI-W-240 authelia)
              ansible.builtin.stat:
                path: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/secrets.yml"
              register: _authelia_secrets_file_check
              when: authelia_config.changed | default(false)
              tags: [authelia]

            - name: Check for existing Authelia DB (cross-tag guard, AFKI-W-240 authelia)
              ansible.builtin.stat:
                path: "{{ authelia_data_dir }}/db.sqlite3"
              register: _authelia_db_precheck
              when: authelia_config.changed | default(false)
              tags: [authelia]

            - name: Guard — fail instead of deleting Authelia DB when secrets weren't loaded (AFKI-W-240)
              ansible.builtin.assert:
                that:
                  - >-
                    not (
                      _authelia_secrets_file_check.stat.exists and
                      drayve_secrets_facts_loaded is not defined and
                      (authelia_config.changed | default(false)) and
                      (_authelia_db_precheck.stat.exists | default(false))
                    )
                fail_msg: |
                  Secrets nicht geladen -- Authelia-Deploy ohne `secrets`-Tag gefahren?
              when: authelia_config.changed | default(false)
              tags: [authelia]

            - name: Remove stale Authelia DB on config change
              ansible.builtin.file:
                path: "{{ authelia_data_dir }}/db.sqlite3"
                state: absent
              when: authelia_config.changed | default(false)
              tags: [authelia]
"""


def _make_wrapper(tmp_path: Path) -> Path:
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        "---\n"
        "- name: Test AFKI-W-240 authelia DB cross-tag guard\n"
        "  hosts: localhost\n"
        "  connection: local\n"
        "  gather_facts: false\n"
        "  tasks:\n" + textwrap.indent(WRAPPER_TASKS, "    ")
    )
    return wrapper


def _layout(tmp_path: Path, host: str, secrets_body: str | None) -> Path:
    root = tmp_path / "site"
    deploy = root / "deploy" / host
    deploy.mkdir(parents=True)
    if secrets_body is not None:
        (deploy / "secrets.yml").write_text(secrets_body)
    return root


def _run(
    wrapper: Path,
    root: Path,
    host: str,
    authelia_data_dir: Path,
    authelia_config_changed: bool,
    tags: str | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "ansible-playbook",
        "-i",
        f"{host},",
        str(wrapper),
        "-e",
        f"inventory_hostname={host}",
        "-e",
        f"ops_secrets_root={root}",
        "-e",
        f"authelia_data_dir={authelia_data_dir}",
        "-e",
        f"authelia_config_changed={'true' if authelia_config_changed else 'false'}",
    ]
    if tags:
        cmd.extend(["--tags", tags])
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


def _authelia_layout(tmp_path: Path) -> Path:
    data_dir = tmp_path / "authelia_data"
    data_dir.mkdir(parents=True)
    (data_dir / "db.sqlite3").write_text("fake-sqlite-db")
    return data_dir


def test_authelia_only_tag_fires_guard_instead_of_deleting_db(ansible_available, tmp_path):
    """AFKI-W-233-class repro: --tags authelia (no secrets), config counted
    as changed because secrets rendered empty -- must not delete an existing
    Authelia DB."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="lldap_jwt_secret: x\n")
    data_dir = _authelia_layout(tmp_path)
    result = _run(
        wrapper,
        root,
        "myhost",
        authelia_data_dir=data_dir,
        authelia_config_changed=True,
        tags="authelia",
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "Secrets nicht geladen" in combined, combined
    assert (data_dir / "db.sqlite3").exists(), "db.sqlite3 must survive when the guard fires"


def test_secrets_plus_authelia_tags_with_real_change_deletes_as_designed(ansible_available, tmp_path):
    """Legitimate case: secrets loaded, config genuinely changed (e.g. a real
    OIDC client added) -- deletion is the intended behavior and must not be
    blocked by the guard."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="lldap_jwt_secret: x\n")
    data_dir = _authelia_layout(tmp_path)
    result = _run(
        wrapper,
        root,
        "myhost",
        authelia_data_dir=data_dir,
        authelia_config_changed=True,
        tags="secrets,authelia",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert not (data_dir / "db.sqlite3").exists()


def test_authelia_tag_without_config_change_is_not_blocked(ansible_available, tmp_path):
    """Normal steady-state run: config unchanged -- guard tasks are skipped
    entirely (when: authelia_config.changed), DB stays untouched."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="lldap_jwt_secret: x\n")
    data_dir = _authelia_layout(tmp_path)
    result = _run(
        wrapper,
        root,
        "myhost",
        authelia_data_dir=data_dir,
        authelia_config_changed=False,
        tags="authelia",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert (data_dir / "db.sqlite3").exists()


def test_authelia_tag_without_any_secrets_file_is_not_blocked(ansible_available, tmp_path):
    """Nicht-Ziel: a host with no secrets.yml at all must not be blocked by
    the guard (nothing to protect against a secrets-role skip)."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body=None)
    data_dir = _authelia_layout(tmp_path)
    result = _run(
        wrapper,
        root,
        "myhost",
        authelia_data_dir=data_dir,
        authelia_config_changed=True,
        tags="authelia",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert not (data_dir / "db.sqlite3").exists()


def test_authelia_only_tag_with_no_existing_db_is_not_blocked(ansible_available, tmp_path):
    """Nicht-Ziel: fresh install, config counted as changed (first render),
    but there's no DB yet to lose -- guard must not fire."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="lldap_jwt_secret: x\n")
    data_dir = tmp_path / "authelia_data_fresh"
    data_dir.mkdir(parents=True)
    result = _run(
        wrapper,
        root,
        "myhost",
        authelia_data_dir=data_dir,
        authelia_config_changed=True,
        tags="authelia",
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert not (data_dir / "db.sqlite3").exists()


def test_full_run_without_tag_scoping_passes(ansible_available, tmp_path):
    """No --tags at all (normal full deploy) must remain unaffected."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body="lldap_jwt_secret: x\n")
    data_dir = _authelia_layout(tmp_path)
    result = _run(
        wrapper,
        root,
        "myhost",
        authelia_data_dir=data_dir,
        authelia_config_changed=True,
        tags=None,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert not (data_dir / "db.sqlite3").exists()
