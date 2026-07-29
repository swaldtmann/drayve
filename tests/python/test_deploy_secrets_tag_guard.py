"""Tests for AFKI-W-233 — cross-tag guard: `.env` must not template with
empty secrets when a tag-scoped deploy run skips the `secrets` role.

Bug (oops-0092, prod-genua): `ansible-playbook ... --tags monitoring,deploy`
skips roles/secrets (tag [secrets]) entirely. The secret facts (lldap_*,
authelia_*, ...) stay undefined, and roles/deploy's "Template .env" task
(tag [deploy]) renders templates/env.j2 anyway — every `{{ var | default('') }}`
falls back to empty, silently overwriting a working `.env`. lldap/Authelia
then crash on `docker compose up`.

Fix under test: roles/secrets sets a sentinel fact
(`drayve_secrets_facts_loaded`) once host_secrets is mapped; roles/deploy
re-stats secrets.yml right before "Template .env" and asserts that, if the
file exists, the sentinel must also be set — otherwise it fails early with
an actionable message instead of writing an empty `.env`.

The wrapper below inlines the exact task sequence added to
roles/secrets/tasks/main.yml and roles/deploy/tasks/main.yml (same
approach as test_secrets_mode_validator.py / AFKI-W-124) so the test can
run against `hosts: localhost` without a real target host or docker.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_J2 = REPO_ROOT / "ansible" / "templates" / "env.j2"

PLAINTEXT_SECRETS = textwrap.dedent(
    """\
    lldap_jwt_secret: "0123456789abcdef0123456789abcdef"
    lldap_key_seed: "seed-12chars"
    lldap_admin_password: "test-lldap-admin-pw"
    """
)


def _make_wrapper(tmp_path: Path) -> Path:
    """Inline copy of the AFKI-W-233 task sequence (secrets sentinel + deploy guard)."""
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            ---
            - name: Test AFKI-W-233 cross-tag secrets guard
              hosts: localhost
              connection: local
              gather_facts: false
              tasks:
                # --- roles/secrets/tasks/main.yml (relevant subset) ---
                - name: Check for host secrets file
                  ansible.builtin.stat:
                    path: "{{{{ ops_secrets_root }}}}/deploy/{{{{ inventory_hostname }}}}/secrets.yml"
                  register: secrets_file
                  tags: [secrets]

                - name: Decrypt SOPS secrets (if encrypted)
                  ansible.builtin.command:
                    cmd: "sops -d {{{{ ops_secrets_root }}}}/deploy/{{{{ inventory_hostname }}}}/secrets.yml"
                  register: sops_decrypt
                  changed_when: false
                  failed_when: false
                  no_log: true
                  when: secrets_file.stat.exists
                  tags: [secrets]

                - name: Load host secrets (plaintext/quickstart)
                  ansible.builtin.include_vars:
                    file: "{{{{ ops_secrets_root }}}}/deploy/{{{{ inventory_hostname }}}}/secrets.yml"
                    name: host_secrets
                  when: secrets_file.stat.exists and (sops_decrypt.rc != 0 or sops_decrypt is skipped)
                  tags: [secrets]

                - name: Set secret variables
                  ansible.builtin.set_fact:
                    lldap_jwt_secret: "{{{{ host_secrets.lldap_jwt_secret | default('') }}}}"
                    lldap_admin_password: "{{{{ host_secrets.lldap_admin_password | default('') }}}}"
                    lldap_key_seed: "{{{{ host_secrets.lldap_key_seed | default('') }}}}"
                  when: secrets_file.stat.exists
                  no_log: true
                  tags: [secrets]

                - name: Mark secret facts as loaded (cross-tag guard sentinel)
                  ansible.builtin.set_fact:
                    drayve_secrets_facts_loaded: true
                  when: secrets_file.stat.exists
                  tags: [secrets]

                # --- roles/deploy/tasks/main.yml (relevant subset) ---
                - name: Check for host secrets file (cross-tag guard, AFKI-W-233)
                  ansible.builtin.stat:
                    path: "{{{{ ops_secrets_root }}}}/deploy/{{{{ inventory_hostname }}}}/secrets.yml"
                  register: _deploy_secrets_file_check
                  tags: [deploy]

                - name: Guard — fail if secrets.yml exists but its facts were never loaded (AFKI-W-233)
                  ansible.builtin.assert:
                    that:
                      - not (_deploy_secrets_file_check.stat.exists and drayve_secrets_facts_loaded is not defined)
                    fail_msg: |
                      Secrets nicht geladen — Deploy ohne `secrets`-Tag gefahren?
                      deploy/{{{{ inventory_hostname }}}}/secrets.yml existiert, aber die Rolle
                      secrets (Task "Set secret variables", tag [secrets]) hat in diesem
                      Lauf nicht gesetzt. `.env` würde jetzt mit leeren Secret-Werten
                      überschrieben (Jinja default('')) — lldap/Authelia crashen beim
                      nächsten `docker compose up` (oops-0092).
                  tags: [deploy]

                - name: Template .env
                  ansible.builtin.template:
                    src: "{ENV_J2}"
                    dest: "{{{{ env_dest }}}}"
                  no_log: true
                  tags: [deploy]
            """
        )
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
    env_dest: Path,
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
        f"env_dest={env_dest}",
    ]
    if tags:
        cmd.extend(["--tags", tags])
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


def test_deploy_only_tag_fires_guard_instead_of_writing_empty_env(ansible_available, tmp_path):
    """oops-0092 repro: --tags deploy (no `secrets`) must fail hard, not write .env."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", PLAINTEXT_SECRETS)
    env_dest = tmp_path / ".env"
    result = _run(wrapper, root, "myhost", env_dest, tags="deploy")
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "Secrets nicht geladen" in combined, combined
    assert not env_dest.exists(), ".env must not be written when the guard fires"


def test_secrets_plus_deploy_tags_pass_and_env_has_real_secret(ansible_available, tmp_path):
    """Fix suggested in the guard message must actually resolve the failure."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", PLAINTEXT_SECRETS)
    env_dest = tmp_path / ".env"
    result = _run(wrapper, root, "myhost", env_dest, tags="secrets,deploy")
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert env_dest.exists()
    content = env_dest.read_text()
    assert "LLDAP_JWT_SECRET=0123456789abcdef0123456789abcdef" in content


def test_full_run_without_tag_scoping_passes(ansible_available, tmp_path):
    """No --tags at all (normal full deploy) must remain unaffected."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", PLAINTEXT_SECRETS)
    env_dest = tmp_path / ".env"
    result = _run(wrapper, root, "myhost", env_dest, tags=None)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert env_dest.exists()


def test_deploy_tag_without_any_secrets_file_is_not_blocked(ansible_available, tmp_path):
    """Nicht-Ziel: a host with no secrets.yml at all (quickstart, no secrets
    provided) must still be able to run a tag-scoped deploy — the guard only
    fires when secrets.yml exists but wasn't loaded."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", secrets_body=None)
    env_dest = tmp_path / ".env"
    result = _run(wrapper, root, "myhost", env_dest, tags="deploy")
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert env_dest.exists()
