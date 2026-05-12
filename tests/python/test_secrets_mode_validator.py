"""Tests for AFKI-W-124 — early-fail validator for SOPS/quickstart mismatch.

Runs ansible-playbook against a wrapper that imports the secrets-checks
from ansible/playbooks/validate.yml, with a fake deploy/<host>/secrets.yml
of varying shape. Asserts that the failure message mentions SOPS so a user
in real life can recognise the cause without reading downstream stack traces.

Requires ansible-playbook on PATH (true in the .venv).
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

PLAINTEXT_SECRETS = textwrap.dedent(
    """\
    lldap_jwt_secret: "0123456789abcdef0123456789abcdef"
    lldap_key_seed: "seed-12chars"
    """
)

SOPS_HEADER_SECRETS = textwrap.dedent(
    """\
    lldap_jwt_secret: ENC[AES256_GCM,data:fake,iv:fake,tag:fake,type:str]
    sops:
        kms: []
        age:
            - recipient: age1fake
              enc: |
                -----BEGIN AGE ENCRYPTED FILE-----
                fakebase64==
                -----END AGE ENCRYPTED FILE-----
        lastmodified: "2026-05-12T00:00:00Z"
        mac: ENC[AES256_GCM,data:fake,iv:fake,tag:fake,type:str]
        version: 3.8.0
    """
)


def _make_wrapper(tmp_path: Path) -> Path:
    """Inline the relevant tasks from validate.yml so we don't need apps/auth."""
    wrapper = tmp_path / "wrapper.yml"
    wrapper.write_text(
        textwrap.dedent(
            """\
            ---
            - name: Test secrets-mode validator
              hosts: localhost
              connection: local
              gather_facts: false
              tasks:
                - name: Stat host secrets file
                  ansible.builtin.stat:
                    path: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/secrets.yml"
                  register: _secrets_stat

                - name: Decrypt SOPS secrets (read-only, local)
                  ansible.builtin.command:
                    cmd: "sops -d {{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/secrets.yml"
                  environment:
                    SOPS_AGE_KEY_FILE: "{{ ops_secrets_root }}/deploy/.age-key.txt"
                  register: _sops_decrypt
                  changed_when: false
                  failed_when: false
                  no_log: true
                  when: _secrets_stat.stat.exists

                - name: Peek first bytes of secrets file (detect SOPS-encrypted header)
                  ansible.builtin.slurp:
                    src: "{{ ops_secrets_root }}/deploy/{{ inventory_hostname }}/secrets.yml"
                  register: _secrets_head
                  when: _secrets_stat.stat.exists
                  no_log: true

                - name: Resolve secrets-file shape (encrypted vs plaintext)
                  ansible.builtin.set_fact:
                    _secrets_is_encrypted: "{{ ((_secrets_head.content | b64decode) is search('(?m)^sops:')) }}"
                    _secrets_mode_declared: "{{ (secrets.mode | default('')) if (secrets is defined and secrets is mapping) else '' }}"
                  when: _secrets_stat.stat.exists

                - name: Fail early — secrets.yml is SOPS-encrypted but sops -d failed
                  ansible.builtin.assert:
                    that:
                      - not (_secrets_is_encrypted and (_sops_decrypt.rc | default(1) != 0))
                    fail_msg: |
                      deploy/{{ inventory_hostname }}/secrets.yml is SOPS-encrypted, but
                      `sops -d` could not decrypt it (rc={{ _sops_decrypt.rc | default('?') }}).
                  when: _secrets_stat.stat.exists

                - name: Fail early — stack.secrets.mode=quickstart but secrets.yml is SOPS-encrypted
                  ansible.builtin.assert:
                    that:
                      - not (_secrets_mode_declared == 'quickstart' and _secrets_is_encrypted)
                    fail_msg: |
                      stack.yaml declares secrets.mode: quickstart, but
                      deploy/{{ inventory_hostname }}/secrets.yml is SOPS-encrypted.
                  when: _secrets_stat.stat.exists
            """
        )
    )
    return wrapper


def _layout(tmp_path: Path, host: str, secrets_body: str) -> Path:
    root = tmp_path / "site"
    deploy = root / "deploy" / host
    deploy.mkdir(parents=True)
    (deploy / "secrets.yml").write_text(secrets_body)
    return root


def _run(wrapper: Path, root: Path, host: str, **extra: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        "ansible-playbook",
        "-i",
        f"{host},",
        str(wrapper),
        "-e",
        f"inventory_hostname={host}",
        "-e",
        f"ops_secrets_root={root}",
    ]
    for k, v in extra.items():
        cmd.extend(["-e", f"{k}={v}"])
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def ansible_available() -> None:
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")


def test_plaintext_quickstart_passes(ansible_available, tmp_path):
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", PLAINTEXT_SECRETS)
    # No `secrets` var provided → mode defaults to '' → quickstart-mismatch assert no-op
    result = _run(wrapper, root, "myhost")
    assert result.returncode == 0, result.stdout + result.stderr


def test_encrypted_without_age_key_fails_with_sops_message(ansible_available, tmp_path):
    """Without an .age-key.txt, sops -d must fail and the early-fail must mention SOPS."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", SOPS_HEADER_SECRETS)
    # Deliberately do NOT create .age-key.txt
    result = _run(wrapper, root, "myhost")
    if shutil.which("sops") is None:
        pytest.skip("sops not on PATH; AFKI-W-124 fail-message check needs sops binary")
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "SOPS-encrypted" in combined or "sops -d" in combined, combined


def test_quickstart_mode_with_encrypted_file_fails(ansible_available, tmp_path):
    """If stack declares quickstart but file is SOPS-encrypted, assert fires."""
    wrapper = _make_wrapper(tmp_path)
    root = _layout(tmp_path, "myhost", SOPS_HEADER_SECRETS)
    result = _run(
        wrapper,
        root,
        "myhost",
        secrets='{"mode":"quickstart"}',
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "quickstart" in combined.lower(), combined
